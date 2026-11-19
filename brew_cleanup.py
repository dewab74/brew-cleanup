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


def select(packages):
    def load_files(package):
        return brew(
            "list", "--" + package["kind"], package["name"], capture=True, timeout=15
        ).stdout.splitlines()

    return package_browser.select(packages, load_files)


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


def transact(action, before, selected, commands, source=None):
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
    print(f"History: {path}", flush=True)
    try:
        for command in commands:
            step = dict(argv=command, started_at=now(), status="running")
            record["commands"].append(step)
            save(path, record)
            brew(*command)
            step.update(status="complete", finished_at=now())
            save(path, record)
        record["status"] = "complete"
    except BaseException as exc:
        record.update(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", error=str(exc)
        )
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


def remove(dry_run=False):
    packages = inventory()
    if not packages:
        print("No installed packages.")
        return
    selected = select(packages)
    if not selected:
        print("Nothing selected.")
        return
    with locked():
        before = inventory()
        if any(key not in before for key in selected):
            raise RuntimeError("Installed packages changed. Please run again.")
        commands = commands_for([before[k] for k in selected], "uninstall") + [["autoremove"]]
        print("\nSelected packages:")
        for key in selected:
            print("  " + key)
        print("\nCommands:")
        for command in commands:
            print("  " + shlex.join(["brew", *command]))
        print("Autoremove also removes ALL currently unused dependencies, including older orphans.")
        if dry_run:
            print("Dry run: no packages changed and no history written.")
            return
        if confirm():
            record = transact("remove", before, selected, commands)
            print(
                f"Removed {len(record.get('removed', {}))} packages. Undo: ./brew-cleanup undo {record['id']}"
            )


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
    parser.add_argument(
        "--dry-run", action="store_true", help="select and review without changing packages"
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
        remove(args.dry_run)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        sys.exit(130)
