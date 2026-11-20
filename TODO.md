# TODO

## Implemented

- [x] Installed formula/cask selection with Space multi-select.
- [x] Right-hand description, version, homepage, and installed-files preview.
- [x] Command review, default-no confirmation, and non-mutating dry-run.
- [x] Explicit uninstall/autoremove with dependency checks retained.
- [x] Timestamped before/after journal and partial-failure accounting.
- [x] Undo missing packages, with recorded retries and explicit recovery.
- [x] Local operation lock and private atomic journal writes.
- [x] README, PLAN, security policy, MIT license, and development Makefile.
- [x] Pinned pre-commit linting, two secret scanners, CI matrix, and Dependabot.

- [x] Persistent interactive review, progress, results, history, undo, and recovery popups.
- [x] Catppuccin Mocha/Latte and terminal themes with saved preferences.
- [x] Live Homebrew output and prompt input inside a private pseudo-terminal.

## Validation

- [x] Isolated tests of removal, autoremove, failure, interrupt, recovery, and undo.
- [x] Read-only installed-package inventory on macOS.
- [x] Real terminal selection, cask file preview, and dry-run command review on macOS.
- [x] All local pre-commit lint and secret-scanning hooks.
- [ ] Real uninstall and undo on a disposable Homebrew installation.
- [ ] Manual Linux terminal interaction.

## Follow-up ideas

- [x] Interactive history selection.
- [ ] Select a subset of a transaction to restore.
- [x] Separate description, files, and dependency boxes with independent scrolling.
- [x] Display runtime dependencies and reverse dependencies as trees.
- [ ] Display estimated disk savings.
- [ ] Optional mode to skip autoremove.
- [ ] Restore original install-on-request flags where supported.
- [ ] Release packaging and a Homebrew tap formula after initial user testing.
