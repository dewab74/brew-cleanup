# TODO

## Implemented

- [x] Installed formula/cask selection with fzf multi-select.
- [x] Right-hand description, version, homepage, and installed-files preview.
- [x] Command review, default-no confirmation, and non-mutating dry-run.
- [x] Explicit uninstall/autoremove with dependency checks retained.
- [x] Timestamped before/after journal and partial-failure accounting.
- [x] Undo missing packages, with recorded retries and explicit recovery.
- [x] Local operation lock and private atomic journal writes.
- [x] README, PLAN, security policy, MIT license, and development Makefile.
- [x] Pinned pre-commit linting, two secret scanners, CI matrix, and Dependabot.

## Validation

- [x] Isolated tests of removal, autoremove, failure, interrupt, recovery, and undo.
- [x] Read-only installed-package inventory on macOS.
- [x] Real fzf selection, cask file preview, and dry-run command review on macOS.
- [x] All local pre-commit lint and secret-scanning hooks.
- [ ] Real uninstall and undo on a disposable Homebrew installation.
- [ ] Manual Linux terminal interaction.

## Follow-up ideas

- [ ] Interactive history selection and selecting a subset of a transaction to restore.
- [ ] Display reverse dependencies and estimated disk savings in preview.
- [ ] Optional mode to skip autoremove.
- [ ] Restore original install-on-request flags where supported.
- [ ] Release packaging and a Homebrew tap formula after initial user testing.
