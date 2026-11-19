# Security policy

## Reporting a vulnerability

While this repository is private, report vulnerabilities in an issue visible only to
repository collaborators. If the repository becomes public, enable GitHub private
vulnerability reporting before publication and use that channel for security reports.
Include reproduction steps, operating system, Python/Homebrew versions, and the relevant
code path. Review and redact private package metadata and local paths before sharing.

## Scope and data handling

This tool can uninstall software as your user. Incorrect selection, command injection,
or misleading/incomplete removal records are security-relevant bugs.
Homebrew packages and cask uninstall scripts run under Homebrew's normal trust model.

The tool sends no telemetry and stores history locally. Homebrew itself may contact
its normal services during reinstall. The history contains package names, versions,
installation metadata, and local paths; keep it private and outside source control.

All package commands use subprocess argument arrays. Shell quoting is limited to fzf's
preview command. Do not run this tool as root or against an untrusted Homebrew installation.
See [PLAN.md](PLAN.md) for the mutation boundary and recovery design.
