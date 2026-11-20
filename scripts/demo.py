"""Isolated README demo: uses the real browser with an in-memory fake Homebrew backend."""

import copy
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import package_browser  # noqa: E402

AUTOMATIC = {"libgit2", "openssl@3", "ca-certificates", "pcre2"}  # Installed as dependencies.


def package(name, desc, dependencies=(), kind="formula"):
    return dict(
        name=name,
        kind=kind,
        versions=["1.0.0"],
        desc=desc,
        homepage="https://brew.sh",
        metadata=dict(
            dependencies=list(dependencies),
            installed=[dict(installed_on_request=name not in AUTOMATIC)],
        ),
    )


PACKAGES = {
    "formula:bat": package(
        "bat", "A cat clone with syntax highlighting and Git integration", ["libgit2"]
    ),
    "formula:curl": package("curl", "Get a file from an HTTP, HTTPS or FTP server", ["openssl@3"]),
    "formula:fd": package("fd", "A simple, fast and friendly alternative to find"),
    "formula:fzf": package("fzf", "A command-line fuzzy finder"),
    "formula:jq": package("jq", "Lightweight and flexible command-line JSON processor"),
    "formula:libgit2": package("libgit2", "C library for Git", ["openssl@3"]),
    "formula:openssl@3": package(
        "openssl@3", "Cryptography and SSL/TLS toolkit", ["ca-certificates"]
    ),
    "formula:ca-certificates": package("ca-certificates", "Mozilla CA certificate store"),
    "formula:pcre2": package("pcre2", "Perl compatible regular expressions library (left over)"),
    "formula:ripgrep": package(
        "ripgrep", "Search tool that recursively searches directories for a regex pattern"
    ),
    "formula:wget": package("wget", "Internet file retriever", ["openssl@3"]),
    "cask:rectangle": package(
        "rectangle", "Move and resize windows using keyboard shortcuts", kind="cask"
    ),
}


def commands(verb, packages):
    return [
        [verb, "--" + kind, *[p["name"] for p in packages if p["kind"] == kind]]
        for kind in ("cask", "formula")
        if any(p["kind"] == kind for p in packages)
    ]


class DemoActions:
    def __init__(self):
        self.packages = copy.deepcopy(PACKAGES)
        self.transactions = []

    def refresh(self):
        return copy.deepcopy(self.packages)

    def save_theme(self, name):
        pass  # Demo preferences never touch the user's configuration.

    def history(self):
        return list(reversed(self.transactions))

    def prepare(self, action, selected):
        if action == "remove":
            chosen = {key: self.packages[key] for key in selected}
            steps = commands("uninstall", chosen.values()) + [["autoremove"]]
            note = "DEMO: simulated packages only; nothing on this Mac changes."
        else:
            record = next(r for r in self.transactions if r["id"] == selected)
            chosen = {key: p for key, p in record["removed"].items() if key not in self.packages}
            steps = [["install", "--" + p["kind"], p["name"]] for p in chosen.values()]
            note = "DEMO: simulated reinstall."
        return dict(
            action=action, before=self.refresh(), selected=chosen, commands=steps, note=note
        )

    def execute(self, plan, runner, emit):
        if plan["action"] == "remove":
            graphs = package_browser.dependency_graph(self.packages)
            _, autoremoved = package_browser.removal_impact(
                list(plan["selected"]), self.packages, graphs
            )
            removed = {}
            for command in plan["commands"][:-1]:
                emit("$ brew " + " ".join(command))
                time.sleep(0.5)
                for name in command[2:]:
                    key = command[1][2:] + ":" + name
                    removed[key] = self.packages.pop(key)
                    emit(f"Uninstalling {name}... done")
                    time.sleep(0.25)
            emit("$ brew autoremove")
            time.sleep(0.5)
            for key in autoremoved:
                removed[key] = self.packages.pop(key)
                emit(f"Uninstalling {key.split(':', 1)[1]}... (unused dependency)")
                time.sleep(0.25)
            self.transactions.append(
                dict(
                    id=f"20261119T21000{len(self.transactions)}Z-demo",
                    action="remove",
                    status="complete",
                    started_at="2026-11-19T21:00:00+00:00",
                    finished_at="2026-11-19T21:00:09+00:00",
                    removed=removed,
                )
            )
            emit(f"Removed {len(removed)} packages. Saved to history: press F3 to undo.")
        else:
            for command in plan["commands"]:
                emit("$ brew " + " ".join(command))
                time.sleep(0.4)
                emit(f"==> Installing {command[2]}... done")
            self.packages.update(plan["selected"])
            emit("Reinstalled. The packages are back.")
        emit("DEMO ONLY: no installed Homebrew packages changed.")


def files(package):
    if package["kind"] == "cask":
        return ["/Applications/Rectangle.app (App)"]
    root = "/opt/homebrew/Cellar/" + package["name"] + "/1.0.0/"
    return [
        root + path
        for path in [
            "INSTALL_RECEIPT.json",
            "bin/" + package["name"],
            "share/man/man1/" + package["name"] + ".1",
            "README.md",
            "LICENSE",
        ]
    ]


if __name__ == "__main__":
    package_browser.select(PACKAGES, files, actions=DemoActions())
