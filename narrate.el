;;; narrate.el --- Turn books into chaptered audiobooks  -*- lexical-binding: t; -*-

;; Copyright (C) 2026 Stephen Webber

;; Author: Stephen Webber
;; Version: 0.3.0
;; Package-Requires: ((emacs "29.1"))
;; Keywords: multimedia, tools
;; URL: https://github.com/offgridauthor/narrate

;; This program is free software; you can redistribute it and/or modify
;; it under the terms of the GNU General Public License as published by
;; the Free Software Foundation, either version 3 of the License, or
;; (at your option) any later version.

;; This program is distributed in the hope that it will be useful,
;; but WITHOUT ANY WARRANTY; without even the implied warranty of
;; MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
;; GNU General Public License for more details.

;; You should have received a copy of the GNU General Public License
;; along with this program.  If not, see <https://www.gnu.org/licenses/>.

;;; Commentary:

;; Front end for narrate's Python backend, which reads EPUB, PDF, DOCX,
;; Org, Markdown, and plain text, voices it with the speech engines your
;; profiles name, and writes a chaptered M4B or MP3.
;;
;; `narrate-file' (or `narrate-dired') opens a book's chapter outline.  Mark
;; the chapters to read, audition the voice on a passage, and render.  Renders
;; run in the background with progress in the mode line; they wait out rate
;; limits on their own and resume from a cache if stopped.
;;
;; Voice profiles live in `narrate-profiles-file' (`narrate-edit-profiles').
;; API keys for cloud engines come from the environment or from auth-source
;; (see `narrate-auth-hosts'), e.g. in ~/.authinfo.gpg:
;;
;;   machine generativelanguage.googleapis.com login apikey password KEY
;;
;; No key is bound globally; bind `narrate-command-map' to a prefix you like.

;;; Code:

(require 'cl-lib)
(require 'subr-x)
(require 'seq)
(require 'tabulated-list)
(require 'auth-source)

(declare-function dired-get-filename "dired")

(defgroup narrate nil
  "Turn books into audiobooks."
  :group 'multimedia
  :prefix "narrate-")

(defconst narrate--dir
  (file-name-directory (or load-file-name buffer-file-name default-directory))
  "Directory containing narrate.el and the backend.")

(defun narrate--xdg (var fallback)
  "The narrate folder under XDG directory VAR, or under FALLBACK in ~."
  (expand-file-name "narrate/" (or (getenv var) (expand-file-name fallback "~"))))

(defcustom narrate-python
  (let ((venv (expand-file-name "venv/bin/python" (narrate--xdg "XDG_DATA_HOME" ".local/share"))))
    (if (file-executable-p venv) venv "python3"))
  "Python interpreter with narrate's dependencies installed.
Defaults to a venv at ~/.local/share/narrate/venv when there is one."
  :type 'string)

(defcustom narrate-backend (expand-file-name "narrate-cli" narrate--dir)
  "The backend launcher, run with `narrate-python'."
  :type 'file)

(defcustom narrate-profiles-file
  (or (getenv "NARRATE_CONFIG")
      (expand-file-name "profiles.toml" (narrate--xdg "XDG_CONFIG_HOME" ".config")))
  "Voice profiles.
Its folder also holds what narrate learns as you go: voices.json for
designed cloud voices, breeze-voices/ for saved local voices, and
plugins/ for engines and readers of your own."
  :type 'file)

(defcustom narrate-output-directory nil
  "Where finished audiobooks go.  nil uses output_dir from the profiles file."
  :type '(choice (const :tag "From profiles file" nil) directory))

(defcustom narrate-format nil
  "Output format.  nil uses the format set in the profiles file."
  :type '(choice (const :tag "From profiles file" nil)
                 (const "m4b") (const "mp3") (const "mp3-split")))

(defcustom narrate-player
  (cl-find-if #'executable-find '("mpv" "ffplay" "paplay" "aplay"))
  "Program used to play auditions and voice previews."
  :type '(choice (const nil) string))

(defcustom narrate-auth-hosts
  '(("GEMINI_API_KEY" . "generativelanguage.googleapis.com")
    ("SPEECHIFY_API_KEY" . "api.speechify.ai"))
  "Environment variables to fill from auth-source, keyed to their hosts.
A variable already set in the environment is left alone."
  :type '(alist :key-type string :value-type string))

(defcustom narrate-scratch-directory
  (expand-file-name "narrate/" (or (getenv "XDG_CACHE_HOME") "~/.cache/"))
  "Where unsaved buffers, regions, and audition clips are written."
  :type 'directory)

(defcustom narrate-tryout-designs-file nil
  "Voice descriptions for `narrate-tryout', one per line.
A line can end with \" | STYLE\" to try a delivery note with it, and
lines starting with # are skipped.  nil means tryout-designs.txt
beside `narrate-profiles-file'."
  :type '(choice (const :tag "Beside the profiles file" nil) file))

(defun narrate--designs-file ()
  "The tryout descriptions file in use."
  (or narrate-tryout-designs-file
      (expand-file-name "tryout-designs.txt" (file-name-directory narrate-profiles-file))))

(defcustom narrate-audition-length 900
  "Characters of a chapter to read when auditioning from the outline."
  :type 'integer)

(defcustom narrate-finished-hook nil
  "Functions called with the output path when a render finishes."
  :type 'hook)

(defface narrate-header
  '((t :inherit header-line))
  "The chapter outline's header line: book, profile, length, and keys."
  :group 'narrate)

(defface narrate-header-key
  '((t :inherit bold))
  "Key names in the outline's header line."
  :group 'narrate)

(defface narrate-header-profile
  '((t :inherit font-lock-constant-face))
  "The profile name in the outline's header line."
  :group 'narrate)

(defconst narrate--extensions '("epub" "pdf" "docx" "org" "md" "markdown" "txt"))


;;;; Talking to the backend

(defun narrate--environment ()
  "Return `process-environment' with API keys from auth-source added."
  (let ((env (cons "PYTHONUNBUFFERED=1" process-environment)))
    (pcase-dolist (`(,var . ,host) narrate-auth-hosts)
      (unless (getenv var)
        (when-let* ((secret (auth-source-pick-first-password :host host)))
          (push (concat var "=" secret) env))))
    env))

(defun narrate--command (&rest args)
  "Full backend command line for ARGS."
  (append (list narrate-python (expand-file-name narrate-backend)
                "--config" (expand-file-name narrate-profiles-file))
          args))

(defun narrate--last-line (file)
  "Last non-blank line of FILE, for error messages."
  (with-temp-buffer
    (insert-file-contents file)
    (car (last (split-string (buffer-string) "\n" t "[ \t]+")))))

(defun narrate--json (&rest args)
  "Run the backend with ARGS synchronously and parse the JSON it prints."
  (let ((process-environment (narrate--environment))
        (err (make-temp-file "narrate-err")))
    (unwind-protect
        (with-temp-buffer
          (let* ((cmd (apply #'narrate--command args))
                 (status (apply #'call-process (car cmd) nil (list t err) nil (cdr cmd))))
            (unless (eql status 0)
              (error "%s" (or (narrate--last-line err) (format "narrate exited %s" status))))
            (goto-char (point-min))
            (json-parse-buffer :object-type 'alist :array-type 'list
                               :null-object nil :false-object nil)))
      (when (file-exists-p err) (delete-file err)))))

(defvar narrate--profiles-cache nil "(MTIME . DATA) from `profiles --json'.")

(defun narrate--profiles ()
  "Return profile data, re-read whenever the profiles file is saved."
  (let ((mtime (file-attribute-modification-time
                (file-attributes narrate-profiles-file))))
    (unless (equal (car narrate--profiles-cache) mtime)
      (setq narrate--profiles-cache (cons mtime (narrate--json "profiles" "--json"))))
    (cdr narrate--profiles-cache)))

(defcustom narrate-confirm-cloud t
  "Non-nil means ask before sending a book's text off this machine.
Each engine says where its text goes (the backend's `remote'); engines
that run here never ask.  Stock audition and tryout passages go
without asking, since they aren't yours."
  :type 'boolean)

(defun narrate--engine (profile)
  "PROFILE's engine name, from the profiles file."
  (alist-get 'engine (cl-find profile (alist-get 'profiles (narrate--profiles))
                              :key (lambda (r) (alist-get 'name r)) :test #'equal)))

(defun narrate--cloud-where (profile)
  "Where PROFILE sends text, or nil when it stays on this machine."
  (alist-get 'remote (cl-find profile (alist-get 'profiles (narrate--profiles))
                              :key (lambda (r) (alist-get 'name r)) :test #'equal)))

(defun narrate--confirm-cloud (profile what)
  "Ask before PROFILE sends WHAT off this machine; signal quit on no."
  (when-let* ((narrate-confirm-cloud)
              (where (narrate--cloud-where profile)))
    (unless (yes-or-no-p (format "%s sends %s to %s.  Go ahead? " profile what where))
      (user-error "Not sent; p in the outline picks a profile that stays on this machine"))))

(defvar narrate-profile-history nil)

(defun narrate--read-profile (&optional prompt)
  "Read a profile name with PROMPT, annotated with engine and fallbacks."
  (let* ((data (narrate--profiles))
         (rows (alist-get 'profiles data))
         (default (alist-get 'default data))
         (completion-extra-properties
          (list :annotation-function
                (lambda (name)
                  (when-let* ((r (cl-find name rows :key (lambda (r) (alist-get 'name r))
                                          :test #'equal)))
                    (let-alist r
                      (format "  %-9s %-14s%s"
                              .engine
                              (if (string-empty-p .design) .voice "designed")
                              (if .fallback
                                  (concat " → " (string-join .fallback ", "))
                                ""))))))))
    (completing-read (format-prompt (or prompt "Profile") default)
                     (mapcar (lambda (r) (alist-get 'name r)) rows)
                     nil t nil 'narrate-profile-history default)))

(defun narrate--output-directory ()
  "Directory renders are written to."
  (expand-file-name (or narrate-output-directory
                        (alist-get 'output_dir (narrate--profiles))
                        "~/Audiobooks")))

(defun narrate--supported-p (file)
  "Return non-nil if FILE is a directory or a format narrate can read."
  (or (file-directory-p file)
      (member (downcase (or (file-name-extension file) "")) narrate--extensions)))


;;;; Background jobs

(cl-defstruct (narrate-job (:constructor narrate--make-job))
  title kind process log (done 0) (total 0) (state "starting") output on-done)

(defvar narrate--jobs nil "Running jobs.")

(defvar narrate--mode-line '(:eval (narrate--mode-line-string))
  "Mode-line construct showing render progress.")
(put 'narrate--mode-line 'risky-local-variable t)

(defun narrate--time-left (job)
  "Rough time left for JOB from the pace of its recent chunks, or nil.
Chunks found in the cache arrive in a burst, so they're left out by
measuring only the last few minutes of progress."
  (let* ((proc (narrate-job-process job))
         (recent (and proc (process-get proc 'narrate-recent)))
         (newest (car recent))
         (oldest (car (last recent))))
    (when (and newest (>= (length recent) 4))
      (let ((secs (- (car newest) (car oldest)))
            (chunks (- (cdr newest) (cdr oldest))))
        (when (and (> secs 20) (> chunks 0))
          (let ((left (/ (* secs (- (narrate-job-total job) (cdr newest))) chunks)))
            (if (>= left 3600)
                (format "%dh %02dm left" (floor left 3600) (floor (mod left 3600) 60))
              (format "%dm left" (max 1 (round left 60))))))))))

(defun narrate--mode-line-string ()
  "Progress of running renders, for the mode line."
  (when-let* ((jobs (cl-remove-if-not (lambda (j) (eq (narrate-job-kind j) 'render))
                                      narrate--jobs)))
    (let* ((j (car jobs))
           (total (narrate-job-total j))
           (done (narrate-job-done j))
           (state (narrate-job-state j))
           (s (concat " [narrate "
                      (if (and (equal state "rendering") (> total 0))
                          (concat (format "%d%% · %d/%d chunks" (floor (* 100 done) total) done total)
                                  (if-let* ((left (narrate--time-left j))) (concat " · " left) "")
                                  (if-let* ((proc (narrate-job-process j))
                                            (gpu (process-get proc 'narrate-gpu))
                                            ((< gpu 1)))
                                      (format " · GPU %d%%" (round (* 100 gpu)))
                                    ""))
                        state)
                      (if (cdr jobs) (format " +%d" (length (cdr jobs))) "")
                      "]")))
      ;; the mode line reads % as a format code, so a literal one is written %%
      (string-replace "%" "%%" s))))

(defun narrate--log (job line)
  "Append LINE to JOB's log buffer."
  (when-let* ((buf (narrate-job-log job)))
    (when (buffer-live-p buf)
      (with-current-buffer buf
        (let ((inhibit-read-only t)
              (at-end (= (point) (point-max))))
          (save-excursion
            (goto-char (point-max))
            (insert line "\n"))
          (when at-end (goto-char (point-max))))))))

(defun narrate--handle-event (job ev)
  "Update JOB from the backend event EV."
  (let-alist ev
    (pcase .event
      ("start"
       (setf (narrate-job-total job) .total
             (narrate-job-state job) "rendering")
       (narrate--log job (format "%s: %d chunks (%d cached), %d chapters, via %s"
                                 .title .total .cached .chapters .profile)))
      ("progress"
       (setf (narrate-job-done job) .done
             (narrate-job-total job) .total
             (narrate-job-state job) "rendering")
       ;; recent (TIME . DONE) pairs, newest first, for the time-left estimate; kept on
       ;; the process so reloading narrate.el mid-render doesn't trip over the job record
       (when-let* ((proc (narrate-job-process job)))
         (when-let* ((id (process-get proc 'narrate-record))
                     ((zerop (mod .done 20))))
           (narrate--update-record id `((done . ,.done) (total . ,.total))))
         (process-put proc 'narrate-gpu .gpu)
         (process-put proc 'narrate-recent
                      (seq-take (cons (cons (float-time) .done) (process-get proc 'narrate-recent))
                                40))))
      ("wait"
       (setf (narrate-job-state job)
             (if .local (format "quota until %s" .local) (format "waiting %ds" .seconds)))
       (narrate--log job (format "waiting: %s%s" .reason
                                 (if .local (format " (resumes %s)" .local)
                                   (format " (%ss)" .seconds)))))
      ("paused"
       (setf (narrate-job-state job) "paused")
       (narrate--log job (format "paused: %s%s" .reason
                                 (if .local (format "; quota resets %s" .local) ""))))
      ("fallback" (narrate--log job (format "%s can't run here: %s" .profile .reason)))
      ("patched" (narrate--log job (format "read by %s (%s): %s" .engine .reason .text)))
      ("assembling" (setf (narrate-job-state job) "assembling"))
      ("done" (setf (narrate-job-output job) .output))
      ("error"
       (setf (narrate-job-state job) "error")
       (narrate--log job (concat "error: " .message)))))
  (force-mode-line-update t))

(defun narrate--handle-line (job line)
  "Route one LINE of backend output for JOB."
  (if (string-prefix-p "{\"event\"" line)
      (condition-case nil
          (narrate--handle-event
           job (json-parse-string line :object-type 'alist :array-type 'list
                                  :null-object nil :false-object nil))
        (json-parse-error (narrate--log job line)))
    (unless (string-empty-p line) (narrate--log job line))))

(defun narrate--filter (proc string)
  "Process filter: split STRING from PROC into lines and handle them."
  (let* ((job (process-get proc 'narrate-job))
         (lines (split-string (concat (or (process-get proc 'narrate-pending) "") string)
                              "\n")))
    (process-put proc 'narrate-pending (car (last lines)))
    (dolist (line (butlast lines))
      (narrate--handle-line job line))))

(defun narrate--sentinel (proc _event)
  "Finish PROC's job when the backend exits."
  (unless (process-live-p proc)
    (let* ((job (process-get proc 'narrate-job))
           (title (narrate-job-title job))
           (pending (process-get proc 'narrate-pending)))
      (when (and pending (not (string-empty-p pending)))
        (narrate--handle-line job pending))
      (setq narrate--jobs (delq job narrate--jobs))
      (when-let* ((id (process-get proc 'narrate-record)))
        (if (eql (process-exit-status proc) 0)
            (narrate--forget-record id)
          (narrate--update-record
           id `((state . ,(cond ((eq (process-status proc) 'signal) "interrupted")
                                ((eql (process-exit-status proc) 75) "paused at the daily quota")
                                ((eql (process-exit-status proc) 130) "stopped")
                                (t "failed")))
                (done . ,(narrate-job-done job)) (total . ,(narrate-job-total job))
                (when . ,(format-time-string "%F %R"))))))
      (pcase (process-exit-status proc)
        (0 (if-let* ((out (narrate-job-output job))
                     (done (narrate-job-on-done job)))
               (funcall done out)
             (message "narrate: %s finished" title)))
        ((guard (eq (process-status proc) 'signal))
         (message "narrate: %s interrupted; M-x narrate-resume picks it up" title))
        (75 (message "narrate: %s paused at the daily quota; M-x narrate-resume picks it up" title))
        (130 (message "narrate: %s stopped; M-x narrate-resume picks it up from the cache" title))
        (_ (message "narrate: %s failed; see %s, then M-x narrate-resume" title
                    (buffer-name (narrate-job-log job)))))
      (force-mode-line-update t))))

(defun narrate--run (title kind args &optional on-done)
  "Start the backend with ARGS as a background job.
TITLE names the job, KIND is `render' or `sample', and ON-DONE is
called with the output path when it succeeds."
  (let* ((log (get-buffer-create (format "*narrate log: %s*" title)))
         (job (narrate--make-job :title title :kind kind :log log :on-done on-done))
         (process-environment (narrate--environment)))
    (with-current-buffer log
      (unless (derived-mode-p 'special-mode) (special-mode))
      (let ((inhibit-read-only t))
        (goto-char (point-max))
        (insert (format "\n%s  %s\n" (format-time-string "%F %T")
                        (string-join args " ")))))
    (let ((proc (make-process :name (format "narrate %s" title)
                              :command (apply #'narrate--command args)
                              :connection-type 'pipe
                              :noquery (not (eq kind 'render))
                              :filter #'narrate--filter
                              :sentinel #'narrate--sentinel)))
      (process-put proc 'narrate-job job)
      (setf (narrate-job-process job) proc))
    (push job narrate--jobs)
    (unless (memq 'narrate--mode-line global-mode-string)
      (setq global-mode-string (append global-mode-string '(narrate--mode-line))))
    job))

(defun narrate--read-job (prompt)
  "Read a running job with PROMPT."
  (unless narrate--jobs (user-error "No narrate jobs running"))
  (let ((names (mapcar (lambda (j) (cons (narrate-job-title j) j)) narrate--jobs)))
    (cdr (assoc (completing-read prompt names nil t) names))))

(defun narrate-cancel (job)
  "Stop JOB.  Finished chunks stay cached, so rendering again resumes."
  (interactive (list (narrate--read-job "Stop: ")))
  (interrupt-process (narrate-job-process job)))

(defun narrate-show-log ()
  "Show a narrate log buffer."
  (interactive)
  (let ((logs (cl-loop for b in (buffer-list)
                       when (string-prefix-p "*narrate log: " (buffer-name b))
                       collect (buffer-name b))))
    (unless logs (user-error "No narrate logs yet"))
    (pop-to-buffer (completing-read "Log: " logs nil t))))


;;;; GPU load

(defconst narrate--gpu-share-file
  (expand-file-name "narrate/gpu-share" (or (getenv "XDG_CACHE_HOME") "~/.cache/"))
  "Where `narrate-gpu-share' leaves its setting for the backend.")

(defun narrate-gpu-share (percent)
  "Let local renders keep the GPU busy PERCENT of the time.
After each chunk, narrate rests in proportion to the work, so 30 works
3 seconds and rests 7.  100 is full speed.  Renders already going pick
it up at their next chunk.  With a prefix argument, go back to the
`gpu_share' in the profiles file."
  (interactive
   (list (unless current-prefix-arg
           (read-number "Share of GPU time for local renders (%, 100 = full speed): "
                        (let ((n (and (file-exists-p narrate--gpu-share-file)
                                      (string-to-number
                                       (with-temp-buffer
                                         (insert-file-contents narrate--gpu-share-file)
                                         (buffer-string))))))
                          (if (and n (> n 0)) (round (* 100 n)) 30))))))
  (if (null percent)
      (progn
        (make-directory (file-name-directory narrate--gpu-share-file) t)
        (with-temp-file narrate--gpu-share-file (insert "profile\n"))
        (message "narrate: GPU share back to the profiles file's gpu_share"))
    (let ((share (/ (max 5 (min 100 percent)) 100.0)))
      (make-directory (file-name-directory narrate--gpu-share-file) t)
      (with-temp-file narrate--gpu-share-file (insert (format "%.2f\n" share)))
      (message "narrate: local renders now use about %d%% of GPU time" (round (* 100 share))))))


;;;; Playback

(defun narrate--play (file)
  "Play audio FILE with `narrate-player'."
  (if (not narrate-player)
      (message "narrate: no player found; audio is at %s" file)
    (narrate-stop-playback)
    (make-process :name "narrate-play"
                  :command (cons narrate-player
                                 (pcase (file-name-nondirectory narrate-player)
                                   ("mpv" (list "--no-video" "--really-quiet" file))
                                   ("ffplay" (list "-nodisp" "-autoexit" "-loglevel" "quiet" file))
                                   (_ (list file))))
                  :noquery t)
    (message "narrate: playing %s" (abbreviate-file-name file))))

(defun narrate-stop-playback ()
  "Stop an audition or preview that is playing."
  (interactive)
  (dolist (p (process-list))
    (when (string-prefix-p "narrate-play" (process-name p))
      (delete-process p))))


;;;; Unfinished renders

(defcustom narrate-mention-unfinished t
  "Non-nil means say so after startup when renders were left unfinished."
  :type 'boolean)

(defun narrate--jobs-file ()
  "Where renders are recorded until they finish."
  (expand-file-name "jobs.json" (narrate--xdg "XDG_CACHE_HOME" ".cache")))

(defun narrate--read-records ()
  "Unfinished renders, newest first, as alists."
  (let ((f (narrate--jobs-file)))
    (when (file-exists-p f)
      (condition-case nil
          (with-temp-buffer
            (insert-file-contents f)
            (json-parse-buffer :object-type 'alist :array-type 'list
                               :null-object nil :false-object nil))
        (json-parse-error nil)))))

(defun narrate--write-records (records)
  "Save RECORDS to `narrate--jobs-file'."
  (let ((f (narrate--jobs-file)))
    (make-directory (file-name-directory f) t)
    (with-temp-file f
      (insert (json-serialize
               (vconcat (mapcar (lambda (r)
                                  (mapcar (lambda (kv)
                                            (if (eq (car kv) 'args) (cons 'args (vconcat (cdr kv))) kv))
                                          r))
                                records)))))))

(defun narrate--record-job (job args)
  "Remember JOB, started with ARGS, until it finishes."
  (let ((id (format-time-string "%Y%m%d-%H%M%S-%N")))
    (process-put (narrate-job-process job) 'narrate-record id)
    (narrate--write-records
     (cons `((id . ,id) (title . ,(narrate-job-title job)) (args . ,args)
             (state . "running") (done . 0) (total . 0) (when . ,(format-time-string "%F %R")))
           (narrate--read-records)))
    job))

(defun narrate--update-record (id fields)
  "Update the record ID with FIELDS, an alist."
  (narrate--write-records
   (mapcar (lambda (r)
             (if (equal (alist-get 'id r) id)
                 (append fields (cl-remove-if (lambda (kv) (assq (car kv) fields)) r))
               r))
           (narrate--read-records))))

(defun narrate--forget-record (id)
  "Drop the record ID."
  (narrate--write-records
   (cl-remove-if (lambda (r) (equal (alist-get 'id r) id)) (narrate--read-records))))

(defun narrate--running-record-ids ()
  "Record ids of renders running in this Emacs."
  (delq nil (mapcar (lambda (j) (and (narrate-job-process j)
                                     (process-get (narrate-job-process j) 'narrate-record)))
                    narrate--jobs)))

(defun narrate--describe-record (r)
  "One line about record R for choosing among them."
  (let-alist r
    (format "%s  (%s%s, %s)" .title
            (if (equal .state "running") "interrupted" .state)
            (if (and .total (> .total 0)) (format " at %d%%" (floor (* 100 .done) .total)) "")
            .when)))

(defun narrate--unfinished ()
  "Unfinished renders that aren't running here now."
  (let ((here (narrate--running-record-ids)))
    (cl-remove-if (lambda (r) (member (alist-get 'id r) here)) (narrate--read-records))))

;;;###autoload
(defun narrate-resume (record &optional forget)
  "Pick up an unfinished render RECORD where it left off.
Renders are remembered from the moment they start until they finish,
so one stopped by `narrate-cancel', a daily quota, a crash, or closing
Emacs can be resumed; chunks already voiced come from the cache.  With
a prefix argument FORGET, drop the record instead of resuming it."
  (interactive
   (let ((records (narrate--unfinished)))
     (unless records (user-error "No unfinished renders"))
     (let* ((choices (mapcar (lambda (r) (cons (narrate--describe-record r) r)) records))
            (pick (completing-read (if current-prefix-arg "Forget render: " "Resume render: ")
                                   choices nil t nil nil (caar choices))))
       (list (cdr (assoc pick choices)) current-prefix-arg))))
  (unless record (user-error "No render to resume"))
  (let-alist record
    (if forget
        (progn (narrate--forget-record .id)
               (message "narrate: forgot %s" .title))
      (let ((job (narrate--run .title 'render .args #'narrate--render-finished)))
        (process-put (narrate-job-process job) 'narrate-record .id)
        (narrate--update-record .id '((state . "running")))
        (message "narrate: resuming %s; chunks already voiced come from the cache" .title)))))

(defun narrate--mention-unfinished ()
  "Say so when renders were left unfinished."
  (when-let* ((narrate-mention-unfinished)
              (records (narrate--unfinished)))
    (message "narrate: %d unfinished render%s (%s); M-x narrate-resume picks up where it stopped"
             (length records) (if (cdr records) "s" "")
             (mapconcat (lambda (r) (alist-get 'title r)) records ", "))))

(add-hook 'emacs-startup-hook
          (lambda () (run-with-idle-timer 2 nil #'narrate--mention-unfinished)))


;;;; Samples: auditions and designed voices

(defun narrate--scratch-file (name)
  "Path NAME inside `narrate-scratch-directory', creating the directory."
  (make-directory narrate-scratch-directory t)
  (expand-file-name name narrate-scratch-directory))

(defun narrate-audition (profile &optional text)
  "Hear PROFILE read TEXT, the active region, or a stock passage."
  (interactive (list (narrate--read-profile "Audition")
                     (when (use-region-p)
                       (buffer-substring-no-properties (region-beginning) (region-end)))))
  (let ((out (narrate--scratch-file (format "audition-%s.wav" profile)))
        (args (list "audition" "--profile" profile)))
    (when text
      (narrate--confirm-cloud profile "the selected text")
      (let ((f (narrate--scratch-file "audition.txt")))
        (with-temp-file f (insert text))
        (setq args (append args (list "--text-file" f)))))
    (narrate--run (format "audition %s" profile) 'sample
                  (append args (list "--out" out)) #'narrate--play)
    (message "narrate: rendering a sample with %s…" profile)))

(defun narrate-design-voice (profile &optional refresh)
  "Create PROFILE's designed voice and play its preview.
For Gemini profiles, prefix arg REFRESH designs a new voice even if
one is cached.  For Breeze profiles, change the profile's seed for a
different take."
  (interactive (list (narrate--read-profile "Design voice for") current-prefix-arg))
  (let ((out (narrate--scratch-file (format "design-%s.wav" profile))))
    (narrate--run (format "design %s" profile) 'sample
                  (append (list "design" "--profile" profile "--out" out)
                          (when refresh (list "--refresh")))
                  #'narrate--play)
    (message "narrate: designing a voice for %s…" profile)))


(defun narrate--read-designs ()
  "Ask where a tryout's voice descriptions come from; nil means the profile's own."
  (let* ((file (narrate--designs-file))
         (n (if (file-exists-p file)
                (with-temp-buffer
                  (insert-file-contents file)
                  (cl-count-if (lambda (l) (not (or (string-blank-p l) (string-prefix-p "#" l))))
                               (split-string (buffer-string) "\n")))
              0))
         (from-file (format "%s (%d descriptions)" (file-name-nondirectory file) n))
         (own "the profile's own design line")
         (other "another file…")
         (pick (completing-read (format "Voice descriptions from (default %s): " from-file)
                                (list from-file own other) nil t nil nil from-file)))
    (cond ((equal pick own) nil)
          ((equal pick other) (read-file-name "Descriptions file: " (file-name-directory file) nil t))
          (t file))))

;;;###autoload
(defun narrate-tryout (profile designs takes)
  "Hear several designed voices for PROFILE, TAKES takes of each.
DESIGNS is a file of descriptions (see `narrate-tryout-designs-file');
empty means PROFILE's own `design' line.  PROFILE supplies the engine, filter,
tempo, and fallbacks; the descriptions supply who's speaking.  Every
take reads the same
passage at the profile's tempo and filter.  When the batch is done,
its index opens and all.wav plays every take in order.  To save
one, put the cursor on its row and run `narrate-keep'."
  (interactive
   (list (narrate--read-profile "Try voices for")
         (narrate--read-designs)
         (read-number "Takes of each: " 3)))
  (let ((out (narrate--scratch-file
              (format "tryout-%s-%s" profile (format-time-string "%m%d-%H%M%S")))))
    (narrate--run (format "tryout %s" profile) 'render
                  (append (list "tryout" "--profile" profile "--out" out
                                "--takes" (number-to-string takes))
                          (when designs (list "--designs" (expand-file-name designs))))
                  (lambda (index)
                    (find-file index)
                    (narrate--play (expand-file-name "all.wav" (file-name-directory index)))))
    (message "narrate: trying voices for %s…" profile)))


;;;###autoload
(defun narrate-keep (dir take name)
  "Save TAKE from the tryout in DIR as a new profile called NAME.
In a tryout's index.org, TAKE defaults to the row at point, and in
Dired to the file at point.  The profile is added to the end of
`narrate-profiles-file' with everything it needs to sound like the
take: for Breeze, the take's reference clip copied into
breeze-voices/; for Gemini, the designed voice's id.  The kept voice
plays when it's saved."
  (interactive
   (let* ((here (and (or (file-exists-p (expand-file-name "tryout.json" default-directory))
                         (file-exists-p (expand-file-name "index.org" default-directory)))
                     default-directory))
          (dir (or here (read-directory-name "Tryout folder: " narrate-scratch-directory nil t)))
          (at-point
           (or (save-excursion
                 (beginning-of-line)
                 (when (re-search-forward "\\[\\[file:\\([0-9]+-[0-9]+\\)\\.wav\\]"
                                          (line-end-position) t)
                   (match-string-no-properties 1)))
               (when-let* (((derived-mode-p 'dired-mode))
                           (f (dired-get-filename 'no-dir t))
                           ((string-match "\\`\\([0-9]+-[0-9]+\\)\\.wav\\'" f)))
                 (match-string 1 f))))
          (takes (mapcar #'file-name-sans-extension
                         (directory-files dir nil "\\`[0-9]+-[0-9]+\\.wav\\'")))
          (take (or at-point (completing-read "Take: " takes nil t))))
     (list dir take (read-string (format "Save take %s as profile named: " take)))))
  (narrate--run (format "keep %s" name) 'sample
                (list "keep" (expand-file-name dir) "--take" take "--as" name)
                (lambda (preview)
                  (narrate--play preview)
                  (message "narrate: kept %s as profile %s; pick it with p in an outline, or in any profile prompt"
                           take name)))
  (message "narrate: saving take %s as %s…" take name))


;;;; Outline

(defvar-local narrate--book nil "Outline data for this buffer's book.")
(defvar-local narrate--profile nil "Profile this outline renders with.")
(defvar-local narrate--marks nil "Chapter numbers marked for rendering.")
(defvar-local narrate--split nil
  "How this outline divides parts: \"parts\" or \"top\".
nil uses `split' from the profiles file, which defaults to \"parts\".")

(defvar-keymap narrate-outline-mode-map
  :parent tabulated-list-mode-map
  "m" #'narrate-outline-mark
  "u" #'narrate-outline-unmark
  "t" #'narrate-outline-toggle-all
  "RET" #'narrate-outline-read
  "a" #'narrate-outline-audition
  "p" #'narrate-outline-set-profile
  "r" #'narrate-outline-render
  "S" #'narrate-outline-toggle-split
  "E" #'narrate-outline-export
  "o" #'narrate-open-output)

(define-derived-mode narrate-outline-mode tabulated-list-mode "Narrate"
  "Chapters of a book to narrate.

Marked chapters (*) are rendered; all start marked.

\\{narrate-outline-mode-map}"
  (setq tabulated-list-format [("" 1 nil) ("#" 4 nil :right-align t)
                               ("Chapter" 40 nil) ("Min" 6 nil :right-align t)
                               ("Opening" 0 nil)]
        tabulated-list-padding 1)
  (setq-local tabulated-list-use-header-line nil)
  (setq header-line-format '(:eval (narrate--outline-header)))
  (face-remap-add-relative 'header-line 'narrate-header)
  (add-hook 'tabulated-list-revert-hook #'narrate--outline-reload nil t)
  (tabulated-list-init-header))

(defun narrate--chapters ()
  "Return the chapters of this outline's book."
  (alist-get 'chapters narrate--book))

(defun narrate--outline-header ()
  "Header line: book, profile, marked length, keys."
  (let* ((chs (narrate--chapters))
         (marked (cl-remove-if-not (lambda (c) (memq (alist-get 'n c) narrate--marks)) chs))
         (minutes (apply #'+ 0 (mapcar (lambda (c) (alist-get 'minutes c)) marked))))
    (concat
     (format "  %s%s   ·   " (alist-get 'title narrate--book)
             (let ((a (alist-get 'author narrate--book)))
               (if (string-empty-p (or a "")) "" (concat " — " a))))
     (propertize narrate--profile 'face 'narrate-header-profile)
     (if (ignore-errors (narrate--cloud-where narrate--profile))
         (propertize "  (cloud)" 'face 'narrate-header-key)
       "  (local)")
     (format "   ·   %d/%d chapters, about %dh %02dm        "
             (length marked) (length chs) (floor minutes 60) (mod (round minutes) 60))
     (mapconcat (lambda (k) (concat (propertize (car k) 'face 'narrate-header-key) " " (cdr k)))
                '(("m/u/t" . "mark") ("RET" . "read") ("a" . "audition") ("p" . "profile")
                  ("r" . "render") ("S" . "parts") ("E" . "export"))
                "     "))))

(defun narrate--outline-refresh ()
  "Redraw the outline."
  (setq tabulated-list-entries
        (mapcar (lambda (c)
                  (let-alist c
                    (list .n (vector (if (memq .n narrate--marks) "*" " ")
                                     (number-to-string .n)
                                     .title
                                     (format "%.1f" .minutes)
                                     (replace-regexp-in-string "\n" " " (or .preview ""))))))
                (narrate--chapters)))
  (tabulated-list-print t))

(defun narrate--split-args ()
  "Backend arguments for this outline's way of dividing parts."
  (when narrate--split (list "--split" narrate--split)))

(defun narrate-outline-toggle-split ()
  "Switch between a chapter mark per part and one per chapter inside parts.
For books whose parts hold headed chapters, one per part makes long
chapters; splitting gives each inner chapter its own mark, titled
with its part.  Every chapter is marked again afterwards."
  (interactive)
  (setq narrate--split (if (equal (or narrate--split (alist-get 'split (narrate--profiles)) "parts")
                                  "top")
                           "parts" "top"))
  (narrate--outline-reload)
  (setq narrate--marks (mapcar (lambda (c) (alist-get 'n c)) (narrate--chapters)))
  (narrate--outline-refresh)
  (message "narrate: %d chapters, %s" (length (narrate--chapters))
           (if (equal narrate--split "top") "one per part" "chapters inside parts split out")))

(defun narrate--outline-reload ()
  "Read the book again after editing its source, keeping each chapter's mark."
  (let ((book (apply #'narrate--json "outline" (alist-get 'file narrate--book) "--json" "--text"
                     (narrate--split-args))))
    (setq narrate--book book
          narrate--marks (cl-remove-if-not
                          (lambda (n) (<= n (length (alist-get 'chapters book))))
                          narrate--marks))
    (narrate--outline-refresh)))

;;;###autoload
(defun narrate-file (file &optional profile)
  "Open FILE's chapter outline for narration.
With prefix arg, choose PROFILE first."
  (interactive (list (read-file-name "Narrate: " nil nil t nil #'narrate--supported-p)
                     (when current-prefix-arg (narrate--read-profile))))
  (let* ((file (expand-file-name file))
         (book (narrate--json "outline" file "--json" "--text"))
         (buf (get-buffer-create (format "*narrate: %s*" (file-name-nondirectory file)))))
    (with-current-buffer buf
      (narrate-outline-mode)
      (setq narrate--book book
            narrate--profile (or profile (alist-get 'default (narrate--profiles)))
            narrate--marks (mapcar (lambda (c) (alist-get 'n c)) (alist-get 'chapters book)))
      (narrate--outline-refresh)
      (goto-char (point-min))
      (while (and (not (tabulated-list-get-id)) (zerop (forward-line 1)))))
    (pop-to-buffer-same-window buf)))

;;;###autoload
(defun narrate-dired ()
  "Narrate the file at point in Dired."
  (interactive)
  (narrate-file (dired-get-filename nil t)))

(defun narrate--source-for (beg end)
  "Return a file holding the text from BEG to END in a format narrate can read."
  (let ((whole (and (= beg (point-min)) (= end (point-max)))))
    (if (and whole buffer-file-name (not (buffer-modified-p))
             (narrate--supported-p buffer-file-name))
        buffer-file-name
      (let* ((ext (cond ((derived-mode-p 'org-mode) "org")
                        ((derived-mode-p 'markdown-mode) "md")
                        (t "txt")))
             (base (replace-regexp-in-string
                    "[^[:alnum:] _-]" ""
                    (file-name-base (or buffer-file-name (buffer-name)))))
             (file (narrate--scratch-file
                    (format "%s%s.%s" (if (string-empty-p base) "buffer" base)
                            (if whole "" "-region") ext))))
        (write-region beg end file nil 'silent)
        file))))

;;;###autoload
(defun narrate-buffer (&optional profile)
  "Narrate the current buffer: its file when saved, else a scratch copy.
With prefix arg, choose PROFILE first."
  (interactive (list (when current-prefix-arg (narrate--read-profile))))
  (narrate-file (narrate--source-for (point-min) (point-max)) profile))

;;;###autoload
(defun narrate-region (beg end &optional profile)
  "Narrate the text between BEG and END.
With prefix arg, choose PROFILE first."
  (interactive (list (region-beginning) (region-end)
                     (when current-prefix-arg (narrate--read-profile))))
  (narrate-file (narrate--source-for beg end) profile))

(defun narrate-outline-mark ()
  "Mark this chapter for rendering."
  (interactive)
  (when-let* ((n (tabulated-list-get-id)))
    (cl-pushnew n narrate--marks)
    (narrate--outline-refresh)
    (forward-line 1)))

(defun narrate-outline-unmark ()
  "Leave this chapter out of the render."
  (interactive)
  (when-let* ((n (tabulated-list-get-id)))
    (setq narrate--marks (delq n narrate--marks))
    (narrate--outline-refresh)
    (forward-line 1)))

(defun narrate-outline-toggle-all ()
  "Mark every chapter, or none if all are marked."
  (interactive)
  (let ((all (mapcar (lambda (c) (alist-get 'n c)) (narrate--chapters))))
    (setq narrate--marks (if (= (length narrate--marks) (length all)) nil all))
    (narrate--outline-refresh)))

(defun narrate--chapter-at-point ()
  "Return the chapter on this outline line."
  (let ((n (or (tabulated-list-get-id) (user-error "No chapter here"))))
    (cl-find n (narrate--chapters) :key (lambda (c) (alist-get 'n c)))))

(defun narrate-outline-read ()
  "Show the text narrate extracted for this chapter."
  (interactive)
  (let* ((c (narrate--chapter-at-point))
         (buf (get-buffer-create "*narrate text*")))
    (with-current-buffer buf
      (let ((inhibit-read-only t))
        (erase-buffer)
        (insert (alist-get 'title c) "\n\n" (alist-get 'text c) "\n")
        (goto-char (point-min)))
      (visual-line-mode 1)
      (view-mode 1))
    (pop-to-buffer buf)))

(defun narrate-outline-audition ()
  "Hear the current profile read the opening of this chapter."
  (interactive)
  (let* ((c (narrate--chapter-at-point))
         (text (alist-get 'text c)))
    (narrate-audition narrate--profile
                      (substring text 0 (min (length text) narrate-audition-length)))))

(defun narrate-outline-set-profile (profile)
  "Render this book with PROFILE."
  (interactive (list (narrate--read-profile)))
  (setq narrate--profile profile)
  (force-mode-line-update))

(defun narrate--ranges (numbers)
  "Compress NUMBERS into a spec like \"1-4,7\"."
  (let (out start prev)
    (dolist (n (sort (copy-sequence numbers) #'<))
      (if (and prev (= n (1+ prev)))
          (setq prev n)
        (when start (push (if (= start prev) (format "%d" start) (format "%d-%d" start prev)) out))
        (setq start n prev n)))
    (when start (push (if (= start prev) (format "%d" start) (format "%d-%d" start prev)) out))
    (string-join (nreverse out) ",")))

(defun narrate--render-finished (output)
  "Announce OUTPUT and run `narrate-finished-hook'."
  (message "narrate: wrote %s" (abbreviate-file-name output))
  (run-hook-with-args 'narrate-finished-hook output))

(defun narrate-outline-render ()
  "Render the marked chapters in the background."
  (interactive)
  (unless narrate--marks (user-error "No chapters marked"))
  (narrate--confirm-cloud narrate--profile "this book's text")
  (let* ((title (alist-get 'title narrate--book))
         (args (append (list "render" (alist-get 'file narrate--book)
                             "--profile" narrate--profile
                             "--out" (narrate--output-directory))
                       (unless (= (length narrate--marks) (length (narrate--chapters)))
                         (list "--chapters" (narrate--ranges narrate--marks)))
                       (when narrate-format (list "--format" narrate-format))
                       (list "--split" (or narrate--split (alist-get 'split (narrate--profiles)) "parts")))))
    (narrate--record-job (narrate--run title 'render args #'narrate--render-finished) args)
    (message "narrate: rendering %s with %s; progress shows in the mode line"
             title narrate--profile)))

(defun narrate-outline-export (file)
  "Write the extracted text to an Org FILE for cleanup, then visit it.
Narrating the edited Org file afterwards reads your corrections."
  (interactive
   (let ((src (alist-get 'file narrate--book)))
     (list (read-file-name "Export text to: " (file-name-directory src) nil nil
                           (concat (file-name-base src) ".narrate.org")))))
  (let ((ev (apply #'narrate--json "extract" (alist-get 'file narrate--book) (expand-file-name file)
                   (narrate--split-args))))
    (find-file (alist-get 'output ev))))


;;;; Odds and ends

(defun narrate-edit-profiles ()
  "Visit the voice profiles file."
  (interactive)
  (find-file narrate-profiles-file))

(defun narrate-open-output ()
  "Open the audiobook output directory in Dired."
  (interactive)
  (let ((dir (narrate--output-directory)))
    (make-directory dir t)
    (dired dir)))

;;;###autoload (autoload 'narrate-command-map "narrate" nil t 'keymap)
(defvar-keymap narrate-command-map
  :doc "Prefix map for narrate commands."
  :prefix 'narrate-command-map
  "f" #'narrate-file
  "b" #'narrate-buffer
  "r" #'narrate-region
  "a" #'narrate-audition
  "d" #'narrate-design-voice
  "t" #'narrate-tryout
  "K" #'narrate-keep
  "g" #'narrate-gpu-share
  "p" #'narrate-edit-profiles
  "o" #'narrate-open-output
  "l" #'narrate-show-log
  "k" #'narrate-cancel
  "R" #'narrate-resume
  "s" #'narrate-stop-playback)

(provide 'narrate)
;;; narrate.el ends here
