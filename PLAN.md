# Plan

## Goal

Make it easy to inspect and remove installed Homebrew packages interactively, while keeping
an auditable record of what disappeared and a practical way to reinstall it.
The implementation checklist and known verification gaps live in [TODO.md](TODO.md).

## Requirements

| Requirement | Implementation |
| --- | --- |
| Installed packages only | `brew info --json=v2 --installed`; formula and cask identities stay distinct. |
| Mark one or more packages | fzf multi-select with searchable names and descriptions. |
| Right-hand details | Cached description/version/homepage plus on-demand `brew list`. |
| Uninstall and autoremove | Review commands, confirm, uninstall by kind, then explicit autoremove. |
| Track what and when | Before/after inventories, UTC timestamps, command progress, actual removals. |
| Undo | Reinstall missing packages from a removal transaction; journal each attempt. |
| Interrupted operation | Save before mutation; final snapshot on failure; explicit recovery after a hard kill. |
| Repo standards | README, PLAN, TODO, MIT license, Makefile, hooks, secret scanning, pinned CI, Dependabot. |

## Decisions

### Python standard library and fzf

Python handles structured Homebrew JSON, atomic history writes, subprocess argument arrays,
and tests without runtime dependencies. fzf provides selection and preview without building
a terminal renderer. The shell launcher only locates the Python script.

Descriptions are fetched once per selection session. File lists are queried only when a
package is highlighted. A second inventory is taken after selection, before removal.

### Explicit mutation boundary

Preview and dry-run do not mutate packages. Review precedes a default-no confirmation.
Commands use argument arrays, not shell interpolation; the fzf preview shell command uses
quoted interpreter/script/cache paths and fzf's quoted field placeholder.
Formulae and casks are explicitly typed, including packages with the same short name.

Disable Homebrew's implicit autoremove and install cleanup. Run autoremove once explicitly
so its failure is visible in the journal. Keep dependency protections and normal uninstall
semantics. Stop on command failure, preserving partial results for undo.

### Journal and recovery

Create a unique transaction file before invoking a mutation. Update command progress and
record a final inventory even after ordinary exceptions or Ctrl-C. Compare package identities
to identify actual removals instead of parsing Homebrew's human-readable output.

History contains full metadata, including original versions and installation options, for
inspection. Undo deliberately guarantees only a reinstall attempt at current versions.
It does not promise restoration of application state or historical package versions.

An advisory lock serializes this tool's mutations. Atomic, flushed JSON writes prevent
partially written records; files have mode 0600 and newly created state directories 0700.
A hard kill can leave a running record. Recovery is explicit because later unrelated package
changes cannot reliably be attributed to the original operation.

### Repository conventions

Adapt the conventions in `hide-my-email` to a small Python project: pinned lint and
credential-scanning hooks run locally, CI acts as a backstop, Makefile exposes common
checks, and documentation distinguishes verified behavior from future work.
No runtime package manager or build step is needed. Release binaries and tap packaging
are deferred until the source-based workflow has been exercised.

## Validation

Test selection/cancellation, dry-run, typed commands, autoremove accounting, partial failures,
interruptions, failed snapshots/recovery, undo idempotence, and concurrent invocation.
Use mocked Homebrew for mutations. Read-only inventory and real fzf dry-run can be exercised
against the host without uninstalling anything. CI covers macOS and Linux.
