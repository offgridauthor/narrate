;;; narrate-test.el --- Tests for narrate.el  -*- lexical-binding: t; -*-

;; Run with: emacs -Q --batch -L . -l tests/narrate-test.el -f ert-run-tests-batch-and-exit

;;; Code:

(require 'ert)
(require 'narrate)

(defmacro narrate-test--with-profiles (rows &rest body)
  "Run BODY with the profile list stubbed to ROWS."
  (declare (indent 1))
  `(cl-letf (((symbol-function 'narrate--profiles)
              (lambda () (list (cons 'default "a") (cons 'profiles ,rows)))))
     ,@body))

(ert-deftest narrate-test-command-map ()
  (dolist (pair '(("f" . narrate-file) ("t" . narrate-tryout) ("K" . narrate-keep)
                  ("g" . narrate-gpu-share) ("k" . narrate-cancel)))
    (should (eq (keymap-lookup narrate-command-map (car pair)) (cdr pair)))))

(ert-deftest narrate-test-outline-keys ()
  (should (eq (keymap-lookup narrate-outline-mode-map "S") 'narrate-outline-toggle-split))
  (with-temp-buffer
    (should-not (narrate--split-args))
    (setq narrate--split "top")
    (should (equal (narrate--split-args) '("--split" "top")))))

(ert-deftest narrate-test-cloud-where ()
  (narrate-test--with-profiles
      '(((name . "here") (engine . "breeze") (remote . nil))
        ((name . "away") (engine . "gemini") (remote . "a service")))
    (should-not (narrate--cloud-where "here"))
    (should (equal (narrate--cloud-where "away") "a service"))))

(ert-deftest narrate-test-confirm-cloud-refuses ()
  (narrate-test--with-profiles '(((name . "away") (remote . "a service")))
    (cl-letf (((symbol-function 'yes-or-no-p) (lambda (_) nil)))
      (should-error (narrate--confirm-cloud "away" "text") :type 'user-error))
    (let ((narrate-confirm-cloud nil))
      (should-not (narrate--confirm-cloud "away" "text")))))

(ert-deftest narrate-test-mode-line-escapes-percent ()
  (let* ((proc (make-process :name "narrate-test" :command '("sleep" "5") :noquery t))
         (job (narrate--make-job :title "t" :kind 'render :process proc))
         (narrate--jobs (list job)))
    (unwind-protect
        (progn
          (narrate--handle-event job '((event . "progress") (done . 14) (total . 1400) (gpu . 0.3)))
          (should (equal (narrate--mode-line-string)
                         " [narrate 1%% · 14/1400 chunks · GPU 30%%]")))
      (delete-process proc))))

(ert-deftest narrate-test-header-tags-local-and-cloud ()
  (narrate-test--with-profiles '(((name . "here") (remote . nil)) ((name . "away") (remote . "x")))
    (with-temp-buffer
      (setq narrate--book '((title . "T") (author . "") (chapters . (((n . 1) (minutes . 2.0)))))
            narrate--marks '(1))
      (setq narrate--profile "here")
      (should (string-match-p "(local)" (narrate--outline-header)))
      (setq narrate--profile "away")
      (should (string-match-p "(cloud)" (narrate--outline-header))))))

(ert-deftest narrate-test-designs-file-follows-profiles-file ()
  (let ((narrate-tryout-designs-file nil)
        (narrate-profiles-file "/somewhere/conf/profiles.toml"))
    (should (equal (narrate--designs-file) "/somewhere/conf/tryout-designs.txt"))))

(ert-deftest narrate-test-render-records ()
  (let* ((dir (make-temp-file "narrate-test" t))
         (process-environment (cons (concat "XDG_CACHE_HOME=" dir) process-environment))
         (proc (make-process :name "narrate-test" :command '("sleep" "5") :noquery t))
         (job (narrate--make-job :title "A Book" :kind 'render :process proc))
         (narrate--jobs (list job)))
    (unwind-protect
        (progn
          (narrate--record-job job '("render" "/x/book.md" "--profile" "p"))
          ;; running here, so not offered for resuming
          (should-not (narrate--unfinished))
          (narrate--handle-event job '((event . "progress") (done . 40) (total . 100)))
          (should (= (alist-get 'done (car (narrate--read-records))) 40))
          (setq narrate--jobs nil)
          ;; as after Emacs closed mid-render
          (let ((r (car (narrate--unfinished))))
            (should (equal (alist-get 'args r) '("render" "/x/book.md" "--profile" "p")))
            (should (string-match-p "A Book +(interrupted at 40%" (narrate--describe-record r)))
            (narrate-resume r t))
          (should-not (narrate--read-records)))
      (delete-process proc)
      (delete-directory dir t))))

(ert-deftest narrate-test-resume-key ()
  (should (eq (keymap-lookup narrate-command-map "R") 'narrate-resume)))

(provide 'narrate-test)
;;; narrate-test.el ends here
