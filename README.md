# brew-cleanup

[![CI](https://github.com/dewab74/brew-cleanup/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/dewab74/brew-cleanup/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/github/license/dewab74/brew-cleanup)](LICENSE)

A terminal interface for removing installed Homebrew packages, with a searchable
package list, three separate detail boxes on the right, and a timestamped
history you can use to reinstall what was removed.

Supports formulae and casks on macOS, and formulae on Linux. Requires Python 3.9+
(standard library with curses) and Homebrew on `PATH`. No fzf or pip packages are needed.
The terminal must be at least 80 columns by 24 rows.

## Run

```sh
git clone https://github.com/dewab74/brew-cleanup.git
cd brew-cleanup
./brew-cleanup --dry-run
./brew-cleanup
```

Run the executable directly. `brew cleanup` is Homebrew's own cache-cleaning
command, and is unrelated to this tool. Keep the launcher alongside `brew_cleanup.py`.

- Type to filter installed packages by name or type.
- Use **Up/Down** to move through packages and **Space** to mark or unmark the current package.
- **Tab / Shift-Tab** cycles focus through the package list and the three right-hand boxes.
- Space is reserved for selection rather than entering spaces in the search query.
- **Ctrl-A** selects all matches; **Ctrl-D** clears matching selections.
- Marks persist when filtering; **Ctrl-U** clears the filter.
- **Up/Down** and **Page Up/Down** scroll the focused box independently.
- **Enter** reviews marked packages, or the current package if none are marked.
- **Esc** cancels. The final prompt defaults to **No**.

The left pane shows package names and types only. Three independently bordered boxes
on the right show the highlighted package's **Description**, **Files / cask artifacts**,
and **Dependencies**. Each box has its own scroll position. File lists load in the
background and are cached for the browser session.

The dependency box contains two trees: **Depends on** and **Required by**. Formula edges
come from direct runtime dependencies in installed receipts, with declared dependencies
as a fallback for older metadata. Casks use their declared formula/cask dependencies.
Reverse dependencies include installed packages only; build/test-only dependencies and
OS requirements are excluded. Shared branches and cycles are marked instead of repeated.
This is an inventory snapshot; Homebrew's uninstall checks remain authoritative.

For casks, Homebrew lists installed artifacts rather than every file inside an app bundle.

After confirmation, the tool runs typed `brew uninstall --cask` / `--formula` commands,
then `brew autoremove`. Homebrew's normal dependency checks remain enabled; the tool
never uses `--force`, `--ignore-dependencies`, or `--zap`. An uninstall failure stops
execution before autoremove and records any partial removals.

**Autoremove removes all unused dependencies**, including ones orphaned before this
session. They are included in the history. `--dry-run` displays commands without running
them; it does not predict the exact dependencies that will become unused.

## History and undo

```sh
./brew-cleanup history
./brew-cleanup undo 20261119T210000Z-0123abcd  # use an actual ID from history
```

Each removal saves the complete installed inventory before and after, selected packages,
commands, UTC timestamps, status, and the packages actually removed, including dependencies.
Undo reinstalls missing packages from that transaction and skips those already installed.
Undo attempts also have their own history entries. If a reinstall fails, fix the cause and
retry the original removal ID; already restored packages will be skipped.

History lives in `$XDG_STATE_HOME/brew-cleanup/history`, or
`~/.local/state/brew-cleanup/history` by default. JSON records are atomically replaced
with owner-only file permissions. They stay outside this repository. Back up this directory
if you want a durable removal ledger. The timestamp is the operation's start/end time,
not a separate clock reading for each removed dependency.

If the process is killed before its final snapshot, inspect the record and run:

```sh
./brew-cleanup recover TRANSACTION_ID
./brew-cleanup undo TRANSACTION_ID
```

Recovery compares the saved inventory with the current installation and asks you to approve
that comparison. Packages removed by unrelated commands since then may appear in the result.
Avoid running other Homebrew operations during cleanup or recovery. The tool locks out other
instances of itself, but cannot lock out independent `brew` commands.

### What undo means

Undo is a **best-effort reinstall**, not a system rollback:

- It installs the versions currently available from Homebrew, not historical binaries.
- Removed/renamed/disabled packages or unavailable taps can require manual intervention.
- It does not restore application data, configuration, services, pins, install options,
  or the original dependency/requested-install flags. Reinstalled dependencies may become
  explicitly requested packages and therefore remain after later autoremove runs.
- A package with another version still installed is not considered removed. Before/after
  snapshots retain version details, but undo does not restore individual missing kegs.
- Cask uninstall scripts can have their own effects; not using `--zap` is not a backup.

## Development

```sh
brew install pre-commit
make hooks
make check
```

`make test` runs isolated tests without changing real Homebrew packages. `make lint` runs
Ruff, Markdown and shell linting, repository hygiene, gitleaks, and detect-secrets.
Hooks run locally before commits; GitHub Actions repeats them as a backstop and tests
Python 3.9 on Ubuntu 22.04 and Python 3.13 on current macOS and Ubuntu runners.
Hook and action versions are pinned.

See [PLAN.md](PLAN.md) for design decisions, [TODO.md](TODO.md) for verification and
future work, and [SECURITY.md](SECURITY.md) for security reporting.

## References

The command behavior follows the [Homebrew manual](https://docs.brew.sh/Manpage);
the browser uses Python's built-in curses terminal UI.

## License

[MIT](LICENSE).
