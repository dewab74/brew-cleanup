#!/usr/bin/env python3
"""Select installed Homebrew packages in a terminal browser and journal removals."""

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import package_browser
import themes

__version__ = "0.1.0"


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def state_dir():
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "brew-cleanup"


def brew(*args, capture=False, timeout=None):
    env = dict(
        os.environ,
        HOMEBREW_NO_AUTO_UPDATE="1",
        HOMEBREW_NO_INSTALL_CLEANUP="1",
        HOMEBREW_NO_AUTOREMOVE="1",
    )
    return subprocess.run(
        ["brew", *args],
        env=env,
        text=True,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        timeout=timeout,
    )


def inventory():
    data = json.loads(brew("info", "--json=v2", "--installed", capture=True).stdout)
    packages = {}
    for kind, field in (("formula", "formulae"), ("cask", "casks")):
        for item in data[field]:
            if not item.get("installed"):
                continue
            name = (
                (item.get("full_name") or item["name"])
                if kind == "formula"
                else (item.get("full_token") or item["token"])
            )
            versions = (
                [v["version"] for v in item["installed"]]
                if kind == "formula"
                else [item["installed"]]
            )
            packages[f"{kind}:{name}"] = dict(
                kind=kind,
                name=name,
                versions=versions,
                desc=item.get("desc") or "No description available.",
                homepage=item.get("homepage", ""),
                metadata=item,
            )
    return packages


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as out:
        temp = Path(out.name)
        try:
            json.dump(data, out, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


@contextlib.contextmanager
def locked():
    root = state_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another brew-cleanup operation is running.") from exc
        yield


def select(packages, **options):
    def load_files(package):
        return brew(
            "list", "--" + package["kind"], package["name"], capture=True, timeout=15
        ).stdout.splitlines()

    return package_browser.select(packages, load_files, **options)


def confirm():
    return input("Continue? [y/N] ").strip().lower() in ("y", "yes")


def commands_for(packages, verb):
    return [
        [verb, "--" + kind, *[p["name"] for p in packages if p["kind"] == kind]]
        for kind in ("cask", "formula")
        if any(p["kind"] == kind for p in packages)
    ]


def records():
    return sorted((state_dir() / "history").glob("*.json"), reverse=True)


def load_record(identifier):
    matches = [p for p in records() if p.stem == identifier]
    if not matches:
        raise RuntimeError(f"Unknown transaction: {identifier}")
    path = matches[0]
    return path, json.loads(path.read_text())


def transact(action, before, selected, commands, source=None, run_command=None, emit=print):
    identifier = (
        dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    )
    path = state_dir() / "history" / (identifier + ".json")
    record = dict(
        id=identifier,
        action=action,
        started_at=now(),
        status="running",
        before=before,
        selected=selected,
        commands=[],
        source=source,
    )
    save(path, record)  # Persist recovery information before any mutation.
    emit(f"History: {path}")
    try:
        for command in commands:
            step = dict(argv=command, started_at=now(), status="running")
            record["commands"].append(step)
            save(path, record)
            (run_command or brew)(*command)
            step.update(status="complete", finished_at=now())
            save(path, record)
        record["status"] = "complete"
    except BaseException as exc:
        interrupted = isinstance(exc, KeyboardInterrupt) or getattr(exc, "interrupted", False)
        record.update(status="interrupted" if interrupted else "failed", error=str(exc))
        if record["commands"]:
            record["commands"][-1].update(status=record["status"], finished_at=now())
        raise
    finally:
        try:
            after = inventory()
            record.update(
                after=after,
                removed={k: v for k, v in before.items() if k not in after},
                added={k: v for k, v in after.items() if k not in before},
            )
        except BaseException as exc:
            record["snapshot_error"] = str(exc)
            record["status"] = "needs-recovery"
        record["finished_at"] = now()
        save(path, record)
    if "snapshot_error" in record:
        raise RuntimeError(f"Final inventory failed. Run recover {identifier} before undo.")
    return record


def config_path():
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "brew-cleanup/config.json"
    )


def configured_theme():
    try:
        name = json.loads(config_path().read_text()).get("theme", themes.DEFAULT)
    except (OSError, ValueError, AttributeError):
        name = themes.DEFAULT
    return name if name in themes.THEMES else themes.DEFAULT


class UIActions:
    def __init__(self):
        self.summary = []  # Printed after the browser closes so IDs stay in scrollback.

    def refresh(self):
        return inventory()

    def save_theme(self, name):
        path = config_path()
        try:
            config = json.loads(path.read_text())
        except (OSError, ValueError):
            config = {}
        if not isinstance(config, dict):
            config = {}
        save(path, {**config, "theme": name})  # Keep any other settings.

    def history(self):
        return [json.loads(path.read_text()) for path in records()]

    def prepare(self, action, selected):
        before = inventory()
        source = None
        if action == "remove":
            missing = set(selected) - before.keys()
            if missing:
                raise RuntimeError("Installed packages changed. Refresh the browser with F5.")
            chosen = {key: before[key] for key in selected}
            commands = commands_for(list(chosen.values()), "uninstall") + [["autoremove"]]
            note = "Autoremove also removes ALL unused dependencies, including older orphans."
        else:
            source = selected
            _, record = load_record(source)
            if record["action"] != "remove":
                raise RuntimeError("Select a removal transaction to undo or recover.")
            if action == "recover":
                if "removed" in record:
                    raise RuntimeError("This transaction already has a final snapshot.")
                chosen = {key: p for key, p in record["before"].items() if key not in before}
                commands = []
                note = "Record this comparison? Unrelated removals since this transaction may be included."
            else:
                if "removed" not in record:
                    raise RuntimeError("Recover this transaction first (R in the history popup).")
                chosen = {key: p for key, p in record["removed"].items() if key not in before}
                commands = [["install", "--" + p["kind"], p["name"]] for p in chosen.values()]
                note = "Reinstall current available versions. Existing packages are skipped."
        return dict(
            action=action,
            before=before,
            selected=chosen,
            commands=commands,
            source=source,
            note=note,
        )

    def execute(self, plan, runner, emit):
        with locked():
            before = inventory()

            def signature(packages):
                return {key: p["versions"] for key, p in packages.items()}

            if signature(before) != signature(plan["before"]):
                raise RuntimeError(
                    "Installed packages changed since review. Refresh and review again."
                )
            if plan["action"] == "recover":
                path, record = load_record(plan["source"])
                record.update(
                    after=before, removed=plan["selected"], status="recovered", recovered_at=now()
                )
                save(path, record)
                emit("Recovery saved. Use history to undo this removal.")
                self.summary.append(
                    f"Recovered {plan['source']}. Undo: brew-cleanup undo {plan['source']}"
                )
                return
            record = transact(
                plan["action"],
                before,
                list(plan["selected"]),
                plan["commands"],
                source=plan["source"],
                run_command=runner,
                emit=emit,
            )
            emit(f"Completed. Removed {len(record['removed'])}; installed {len(record['added'])}.")
            emit("Transaction: " + record["id"])
            if plan["action"] == "remove":
                self.summary.append(
                    f"Removed {len(record['removed'])} packages. "
                    f"Undo: brew-cleanup undo {record['id']}"
                )
            else:
                self.summary.append(
                    f"Reinstalled {len(record['added'])} packages ({record['id']})."
                )


def remove(dry_run=False, theme=None):
    actions = UIActions()
    select({}, actions=actions, dry_run=dry_run, theme=theme or configured_theme())
    for line in actions.summary:
        print(line)


def undo(identifier):
    with locked():
        _, record = load_record(identifier)
        if record["action"] != "remove":
            raise RuntimeError("Only removal transactions can be undone.")
        if "removed" not in record:
            raise RuntimeError("This transaction needs recovery: run recover ID first.")
        before = inventory()
        missing = {k: p for k, p in record["removed"].items() if k not in before}
        if not missing:
            print("All recorded packages are already installed.")
            return
        # Install separately so one unavailable package does not lose the record of successes.
        commands = [["install", "--" + p["kind"], p["name"]] for p in missing.values()]
        print("Reinstall current available versions (existing packages will be skipped):")
        for command in commands:
            print("  " + shlex.join(["brew", *command]))
        if confirm():
            transact("undo", before, list(missing), commands, source=identifier)
            print("Reinstall complete.")


def recover(identifier):
    with locked():
        path, record = load_record(identifier)
        if "removed" in record:
            print("This transaction already has a final snapshot.")
            return
        after = inventory()
        missing = {k: v for k, v in record["before"].items() if k not in after}
        print("Missing since the initial snapshot: " + (", ".join(missing) or "none"))
        print("Other brew changes since this transaction may be included. Record this comparison?")
        if confirm():
            record.update(after=after, removed=missing, status="recovered", recovered_at=now())
            save(path, record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    parser.add_argument(
        "--dry-run", action="store_true", help="select and review without changing packages"
    )
    parser.add_argument(
        "--theme", choices=list(themes.THEMES), help="override the saved color theme"
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("history", help="list transactions and removed packages")
    for name in ("undo", "recover"):
        cmd = sub.add_parser(name)
        cmd.add_argument("id")
    args = parser.parse_args()
    if args.dry_run and args.command:
        parser.error("--dry-run is only supported for package selection")
    if args.command == "history":
        paths = records()
        if not paths:
            print("No history yet.")
        for path in paths:
            record = json.loads(path.read_text())
            print(f"{record['id']}  {record['action']}  {record['status']}  {record['started_at']}")
            for key in record.get("removed", {}):
                print("  removed " + key)
        return
    if not shutil.which("brew"):
        raise RuntimeError("Homebrew is required and must be on PATH.")
    if args.command == "undo":
        undo(args.id)
    elif args.command == "recover":
        recover(args.id)
    else:
        remove(args.dry_run, args.theme)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        sys.exit(130)
