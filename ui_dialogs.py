"""Curses dialogs; background work never draws to the terminal directly."""

import curses
import datetime as dt
import queue
import re
import shlex
from concurrent.futures import ThreadPoolExecutor

import themes
from popup_process import CommandRunner

ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[()][0-9A-Za-z]")


class Dialogs:
    def popup(self, screen, title, lines, footer, offset=0, selected=None, role="accent", fit=True):
        height, width = screen.getmaxyx()
        if height < 8 or width < 30:
            screen.erase()
            self.put(screen, 0, 0, "Resize terminal to continue.", width - 1)
            screen.refresh()
            return 0
        w = min(width - 4, 100)
        # Wrapping is done here for prose/output; menus keep one item per row.
        from package_browser import wrapped

        display = lines if selected is not None else wrapped(lines, w - 4)
        # Short messages get short boxes; streaming output keeps a stable size.
        h = min(height - 2, 26, max(8, len(display) + 5) if fit else 26)
        if hasattr(self, "draw"):
            self.draw(screen)  # A clean frame, so a smaller box leaves no stale border.
        window = screen.derwin(h, w, (height - h) // 2, (width - w) // 2)
        window.bkgd(" ", self.colors.get("text"))
        window.erase()
        window.attrset(self.colors.get(role))
        window.box()
        window.attrset(self.colors.get("text"))
        self.put(window, 0, 2, " " + title + " ", w - 4, self.colors.get(role) | curses.A_BOLD)
        visible = h - 4
        if selected is not None:
            offset = max(0, selected - visible + 1)
        offset = max(0, min(offset, max(0, len(display) - visible)))
        for row, line in enumerate(display[offset : offset + visible], 2):
            index = offset + row - 2
            style = "selected" if index == selected else "text"
            self.put(window, row, 2, line.ljust(w - 4), w - 4, self.colors.get(style))
        self.put(window, h - 2, 2, footer, w - 4, self.colors.get("yellow") | curses.A_BOLD)
        if len(display) > visible:
            self.put(
                window,
                h - 1,
                w - 18,
                f" {offset + 1}/{len(display)} ",
                16,
                self.colors.get("muted"),
            )
        screen.touchwin()
        screen.refresh()
        return offset

    def read_key(self, screen):
        try:
            return screen.get_wch()
        except curses.error:
            return None

    def message(self, screen, title, lines):
        offset = 0
        while True:
            offset = self.popup(screen, title, lines, "Enter/Esc close · ↑/↓ scroll", offset)
            event = self.read_key(screen)
            if event in ("\n", "\r", "\x1b", "\x03"):
                return
            offset += -1 if event == curses.KEY_UP else 1 if event == curses.KEY_DOWN else 0

    def ask(self, screen, title, lines, label, role="red"):
        """Yes/no popup. Enter on the default (Cancel) button does nothing."""
        approved, offset = False, 0
        while True:
            buttons = f"  Cancel   [ {label} ]" if approved else f"[ Cancel ]   {label}  "
            offset = self.popup(
                screen,
                title,
                lines,
                buttons + "   Y yes · N/Esc no · ←/→ choose",
                offset,
                role=role,
            )
            event = self.read_key(screen)
            if event in ("\x1b", "\x03", "n", "N"):
                return False
            if event in ("y", "Y"):
                return True
            if event in ("\n", "\r"):
                return approved
            if event in ("\t", curses.KEY_LEFT, curses.KEY_RIGHT, curses.KEY_BTAB):
                approved = not approved
            offset += -1 if event == curses.KEY_UP else 1 if event == curses.KEY_DOWN else 0

    def plan_lines(self, plan):
        from package_browser import label, removal_impact

        selected = plan["selected"]
        keys = list(selected)

        def describe(key):
            chosen = selected.get(key) if isinstance(selected, dict) else None
            package = chosen or self.packages.get(key)
            versions = " " + ", ".join(package["versions"]) if package else ""
            return "  • " + label(key, self.packages) + versions

        count = f"{len(keys)} package" + ("" if len(keys) == 1 else "s")
        heading = {
            "remove": f"Remove {count}:",
            "undo": f"Reinstall {count} (current versions from Homebrew):",
            "recover": f"Record {count} as removed by this transaction:",
        }.get(plan["action"], plan["action"].capitalize() + ":")
        lines = [heading, *map(describe, keys)]
        if plan["action"] == "remove":
            blockers, autoremoved = removal_impact(keys, self.packages, self.graphs)
            if blockers:
                lines += ["", "Homebrew will refuse these while other packages still need them:"]
                lines += [
                    f"  • {label(key, self.packages)} — needed by "
                    + ", ".join(label(other, self.packages) for other in needed)
                    for key, needed in sorted(blockers.items())
                ]
            if autoremoved:
                lines += [
                    "",
                    f"Then brew autoremove should also remove {len(autoremoved)} unused "
                    + ("dependency:" if len(autoremoved) == 1 else "dependencies:"),
                    *map(describe, autoremoved),
                ]
            else:
                lines += ["", "brew autoremove runs afterwards; no unused dependencies expected."]
        if plan["commands"]:
            lines += ["", "Commands:"]
            lines += ["  " + shlex.join(["brew", *command]) for command in plan["commands"]]
        if plan.get("note"):
            lines += ["", plan["note"]]
        return lines

    def confirm_plan(self, screen, plan):
        lines = self.plan_lines(plan)
        if self.dry_run:
            self.message(screen, "Dry run — nothing will change", lines)
            return False
        label = {"remove": "Remove", "undo": "Reinstall", "recover": "Record"}.get(
            plan["action"], plan["action"].capitalize()
        )
        return self.ask(screen, lines[0].rstrip(":") + "?", lines[1:], label)

    def help(self, screen):
        self.message(
            screen,
            "Keys",
            [
                "Finding packages",
                "  Type             filter by name (letters in order, e.g. 'ossl')",
                "  Backspace        erase one character · Ctrl-U clear filter",
                "  ↑/↓ PgUp/PgDn    move in the list, or scroll the focused box",
                "  Tab / Shift-Tab  move focus between the list and the three boxes",
                "  F4 / Shift-F4    show: all, formulae, casks, installed by you,",
                "                   dependencies, or unused dependencies",
                "",
                "Choosing",
                "  Space            mark or unmark the highlighted package",
                "  Ctrl-A / Ctrl-D  mark / unmark every package matching the filter",
                "  Enter            review removing the marked packages",
                "                   (or the highlighted one when nothing is marked)",
                "",
                "Other",
                "  F3               history: see past removals and undo them",
                "  F2               color theme · F5 reload installed packages",
                "  Esc              clear the filter, then quit · Ctrl-Z suspend",
                "",
                "Tip: F4 to 'Unused dependencies', then Ctrl-A, marks everything",
                "brew autoremove would clean up anyway.",
                "",
                "In the list, 'dep' marks a formula that was installed automatically",
                "as a dependency; 'cask' marks a macOS app. Nothing is removed until",
                "you confirm the review.",
            ],
        )

    def progress(self, screen, title, task, auto_close=False):
        messages = queue.Queue()
        runner = CommandRunner(messages.put)
        output, offset, follow = "", 0, True
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(task, runner, messages.put)
            result, ok, finished = None, False, False
            while True:
                while not messages.empty():
                    output += messages.get_nowait()
                # Bounded, memory-only output. Password input is never separately echoed or saved.
                output = output[-200000:]
                # PTYs emit CRLF; only a bare CR (progress redraw) starts a new line.
                text = ANSI.sub("", output).replace("\r\n", "\n").replace("\r", "\n")
                lines = text.splitlines() or ["Working…"]
                if future.done() and not finished:
                    finished = True
                    try:
                        result = future.result()
                        ok = True
                        output += "\nCompleted.\n"
                    except Exception as exc:
                        output += "\nFailed: " + str(exc) + "\n"
                    if ok and auto_close:
                        return True, result
                    continue
                footer = (
                    "Enter/Esc back to the list · ↑/↓ scroll"
                    if finished
                    else "Working… · type to answer prompts · Ctrl-C interrupt · ↑/↓ scroll"
                )
                offset = self.popup(
                    screen,
                    title + (" — complete" if ok else " — failed" if finished else ""),
                    lines,
                    footer,
                    10**9 if follow else offset,
                    role="green" if ok else "red" if finished else "blue",
                )
                event = self.read_key(screen)
                if finished and event in ("\n", "\r", "\x1b", "\x03"):
                    return ok, result
                if event in (curses.KEY_UP, curses.KEY_PPAGE):
                    follow = False
                    offset -= 5 if event == curses.KEY_PPAGE else 1
                elif event in (curses.KEY_DOWN, curses.KEY_NPAGE):
                    follow = False
                    offset += 5 if event == curses.KEY_NPAGE else 1
                elif event == curses.KEY_END:
                    follow = True
                elif not finished and event == "\x03":
                    runner.cancelled.set()
                    output += (
                        "\nInterrupt requested; waiting for Homebrew and the final snapshot…\n"
                    )
                elif not finished and isinstance(event, str) and event != "\x1b":
                    runner.input.put(event)
                elif not finished and event == curses.KEY_BACKSPACE:
                    runner.input.put("\x7f")

    def refresh_inventory(self, screen):
        ok, packages = self.progress(
            screen,
            "Refreshing installed packages",
            lambda runner, emit: self.actions.refresh(),
            auto_close=True,
        )
        if ok:
            self.packages = packages
            from package_browser import dependency_graph

            self.graphs = dependency_graph(packages)
            self.keys = sorted(packages, key=lambda key: (packages[key]["name"], key))
            self.marked.intersection_update(packages)
            self.files.clear()
            self.pending = None
            self.current = None
            self.filter()

    def perform(self, screen, action, selected):
        if action == "remove" and not selected:
            self.message(screen, "Nothing selected", ["No package matches the current filter."])
            return
        ok, plan = self.progress(
            screen,
            "Preparing " + action,
            lambda runner, emit: self.actions.prepare(action, selected),
            auto_close=True,
        )
        if not ok:
            return
        if not plan["selected"] and action == "undo":
            self.message(
                screen, "Already restored", ["All recorded packages are already installed."]
            )
            return
        if not self.confirm_plan(screen, plan):
            return
        self.progress(
            screen,
            action.capitalize() + " packages",
            lambda runner, emit: self.actions.execute(plan, runner, lambda text: emit(text + "\n")),
        )
        self.refresh_inventory(screen)

    def choose_theme(self, screen):
        names = list(themes.THEMES)
        index = names.index(self.theme)
        while True:
            self.popup(
                screen,
                "Color theme",
                names,
                "↑/↓ choose · Enter apply and save · Esc cancel",
                selected=index,
            )
            event = self.read_key(screen)
            if event in ("\x1b", "\x03"):
                return
            if event in ("\n", "\r"):
                self.theme = names[index]
                self.colors.apply(self.theme)
                if self.actions:
                    try:
                        self.actions.save_theme(self.theme)
                    except OSError as exc:
                        self.message(
                            screen,
                            "Theme applied for this session",
                            ["Could not save preference: " + str(exc)],
                        )
                return
            index = max(
                0,
                min(
                    len(names) - 1,
                    index
                    + (-1 if event == curses.KEY_UP else 1 if event == curses.KEY_DOWN else 0),
                ),
            )

    def show_history(self, screen):
        ok, records = self.progress(
            screen, "Loading history", lambda runner, emit: self.actions.history(), auto_close=True
        )
        if not ok:
            return
        if not records:
            self.message(screen, "History", ["No transactions yet."])
            return
        index = 0
        while True:
            record = records[index]
            recoverable = record["action"] == "remove" and "removed" not in record
            footer = "Enter details · U undo" + (" · R recover" if recoverable else "")
            self.popup(
                screen,
                "History — newest first",
                [history_row(r) for r in records],
                footer + " · Esc close",
                selected=index,
            )
            event = self.read_key(screen)
            if event in ("\x1b", "\x03"):
                return
            if event in ("u", "U") or (recoverable and event in ("r", "R")):
                self.perform(screen, "undo" if event.lower() == "u" else "recover", record["id"])
                return
            if event in ("\n", "\r"):
                self.message(screen, history_row(record).split("  ")[0], history_details(record))
            index = max(
                0,
                min(
                    len(records) - 1,
                    index
                    + (-1 if event == curses.KEY_UP else 1 if event == curses.KEY_DOWN else 0),
                ),
            )


def local_time(stamp):
    """ISO UTC timestamp -> local 'YYYY-MM-DD HH:MM' (Python 3.9 cannot parse 'Z')."""
    try:
        moment = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return "unknown time"
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


STATUS = {
    "complete": "",
    "recovered": "",
    "failed": "failed part way",
    "interrupted": "interrupted",
    "running": "did not finish",
    "needs-recovery": "needs recovery (R)",
}


def history_row(record):
    from package_browser import label

    undo = record["action"] == "undo"
    packages = record.get("added" if undo else "removed")
    if packages is None:
        names, count = "", "?"
    else:
        names = ", ".join(label(key) for key in sorted(packages))
        count = str(len(packages))
    status = STATUS.get(record.get("status"), record.get("status", ""))
    if record["action"] == "remove" and "removed" not in record:
        status = STATUS["needs-recovery"]
    verb = "Reinstalled" if undo else "Removed"
    row = f"{local_time(record.get('started_at'))}  {verb:<11} {count:>3}  {names}"
    return row + (f"  [{status}]" if status else "")


def history_details(record):
    from package_browser import label

    lines = [
        ("Reinstall" if record["action"] == "undo" else "Removal")
        + f" started {local_time(record.get('started_at'))}",
    ]
    if record.get("finished_at"):
        lines.append("Finished " + local_time(record["finished_at"]))
    status = STATUS.get(record.get("status"), record.get("status", ""))
    if status:
        lines.append("Status: " + status)
    for title, field in (("Removed", "removed"), ("Installed", "added")):
        if record.get(field):
            lines += ["", f"{title} ({len(record[field])}):"]
            lines += ["  • " + label(key) for key in sorted(record[field])]
    if record.get("source"):
        lines += ["", "Undoes transaction " + record["source"]]
    if record.get("error"):
        lines += ["", "Error: " + record["error"]]
    lines += ["", "Transaction ID: " + record["id"], "Press U in the list to reinstall these."]
    return lines
