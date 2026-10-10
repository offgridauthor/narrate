EMACS ?= emacs
PYTHON ?= python3

.PHONY: all test test-python test-emacs info compile lint clean

all: test info

test: test-python test-emacs

test-python:
	$(PYTHON) -m pytest -q tests

test-emacs:
	$(EMACS) -Q --batch -L . -l tests/narrate-test.el -f ert-run-tests-batch-and-exit

# The Info manual is generated from README.org.
info: narrate.info

narrate.texi: README.org
	$(EMACS) -Q --batch --eval '(progn (require (quote ox-texinfo)) (find-file "README.org") (org-export-to-file (quote texinfo) "narrate.texi"))'

narrate.info: narrate.texi
	makeinfo --no-split narrate.texi -o narrate.info
	install-info --info-dir=. narrate.info

compile:
	$(EMACS) -Q --batch --eval '(setq byte-compile-error-on-warn t)' -f batch-byte-compile narrate.el

lint:
	$(EMACS) -Q --batch --eval '(progn (require (quote checkdoc)) (checkdoc-file "narrate.el"))'

clean:
	rm -f narrate.elc narrate.info dir
