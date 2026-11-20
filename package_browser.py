"""Terminal package browser with independently scrollable detail boxes."""

import curses
import os
import signal
import textwrap
from concurrent.futures import ThreadPoolExecutor

import themes
from ui_dialogs import Dialogs


def dependency_graph(packages):
    """Use installed runtime receipts for formulae and declared cask dependencies."""
    aliases = {}
    for key, package in packages.items():
        kind, name = package["kind"], package["name"]
        for alias in [name, name.rsplit("/", 1)[-1], *package["metadata"].get("aliases", [])]:
            aliases.setdefault((kind, alias), set()).add(key)

    def resolve(kind, name):
        exact = f"{kind}:{name}"
        if exact in packages:
            return exact
        matches = aliases.get((kind, name), set())
        return next(iter(matches)) if len(matches) == 1 else exact

    forward = {key: set() for key in packages}
    reverse = {key: set() for key in packages}
    for key, package in packages.items():
        meta = package["metadata"]
        if package["kind"] == "formula":
            declared = set(
                meta.get("dependencies", [])
                + meta.get("recommended_dependencies", [])
                + meta.get("optional_dependencies", [])
            )
            receipts = meta.get("installed", [])
            if receipts and all("runtime_dependencies" in receipt for receipt in receipts):
                dependencies = [
                    ("formula", dep["full_name"])
                    for receipt in receipts
                    for dep in receipt["runtime_dependencies"]
                    if dep.get(
                        "declared_directly",
                        dep["full_name"] in declared
                        or dep["full_name"].rsplit("/", 1)[-1] in declared,
                    )
                ]
            else:
                dependencies = [("formula", name) for name in meta.get("dependencies", [])]
        else:
            depends = meta.get("depends_on") or {}
            dependencies = [
                (kind, name) for kind in ("formula", "cask") for name in depends.get(kind, [])
            ]
        for kind, name in dependencies:
            target = resolve(kind, name)
            forward[key].add(target)
            reverse.setdefault(target, set()).add(key)
    return forward, reverse


def label(key, packages=None):
    """Human name for a package key: casks are marked, formulae are the default."""
    kind, sep, name = key.partition(":")
    if not sep:
        kind, name = "formula", key
    name = ((packages or {}).get(key) or {}).get("name", name)
    return name + " (cask)" if kind == "cask" else name


def requested(package):
    """True/False from install receipts; None when Homebrew did not say."""
    if package["kind"] == "cask":
        return True
    receipts = package["metadata"].get("installed", [])
    flags = [r.get("installed_on_request") for r in receipts if isinstance(r, dict)]
    if any(flags):
        return True
    return False if flags and all(flag is False for flag in flags) else None


def removal_impact(selected, packages, graphs):
    """Estimate what removing `selected` means before Homebrew is asked.

    Returns (blockers, autoremoved): installed packages outside the selection that
    still need a selected package (Homebrew will refuse those uninstalls), and the
    unused dependencies `brew autoremove` is expected to take afterwards.
    """
    forward, reverse = graphs
    chosen = set(selected)
    blockers = {
        key: sorted(dependent for dependent in reverse.get(key, ()) if dependent not in chosen)
        for key in chosen
    }
    blockers = {key: needed for key, needed in blockers.items() if needed}
    gone = set(chosen)
    while True:
        orphans = {
            key
            for key, package in packages.items()
            if key not in gone
            and package["kind"] == "formula"
            and requested(package) is False
            and set(reverse.get(key, ())) <= gone
        }
        if not orphans:
            return blockers, sorted(gone - chosen)
        gone |= orphans


VIEWS = (
    ("all", "All packages"),
    ("formula", "Formulae"),
    ("cask", "Casks"),
    ("requested", "Installed by you"),
    ("dependency", "Dependencies"),
    ("unused", "Unused dependencies"),
)


def in_view(view, key, packages, reverse):
    package = packages[key]
    if view in ("formula", "cask"):
        return package["kind"] == view
    if view == "requested":
        return requested(package) is True
    if view == "dependency":
        return requested(package) is False
    if view == "unused":  # What `brew autoremove` would take right now.
        return requested(package) is False and not reverse.get(key)
    return True


def tree_lines(root, graph, packages):
    lines = []
    seen = {root}
    stack = [
        (child, "", index == len(children) - 1)
        for children in [sorted(graph.get(root, []))]
        for index, child in reversed(list(enumerate(children)))
    ]
    while stack:
        key, prefix, last = stack.pop()
        text = label(key, packages) + (" — not installed" if key not in packages else "")
        repeated = key in seen
        lines.append(
            prefix + ("└─ " if last else "├─ ") + text + (" (shown above)" if repeated else "")
        )
        if repeated:
            continue
        seen.add(key)
        children = sorted(graph.get(key, []))
        stack.extend(
            (child, prefix + ("   " if last else "│  "), index == len(children) - 1)
            for index, child in reversed(list(enumerate(children)))
        )
    return lines or ["   nothing"]


HEADINGS = ("Uses:", "Used by:")


def dependency_lines(key, graphs, packages):
    return (
        [HEADINGS[0] + " (runtime dependencies)"]
        + tree_lines(key, graphs[0], packages)
        + ["", HEADINGS[1] + " (installed packages that need it)"]
        + tree_lines(key, graphs[1], packages)
    )


def matches(query, text):
    """Case-insensitive subsequence matching, like a simple fuzzy finder."""
    chars = iter(text.casefold())
    return all(any(char == candidate for candidate in chars) for char in query.casefold())


def clean(text):
    # Homebrew metadata can be null; a missing value must never crash the browser.
    return "".join(char if char.isprintable() else " " for char in str(text or ""))


def wrapped(lines, width):
    return [
        part
        for line in lines
        for part in (
            textwrap.wrap(
                clean(line), max(1, width), replace_whitespace=False, drop_whitespace=False
            )
            or [""]
        )
    ]


class Browser(Dialogs):
    def __init__(self, packages, load_files, actions=None, dry_run=False, theme=themes.DEFAULT):
        self.actions = actions
        self.dry_run = dry_run
        self.theme = theme
        self.colors = themes.Colors()
        self.packages = packages
        self.load_files = load_files
        self.graphs = dependency_graph(packages)
        self.keys = sorted(packages, key=lambda key: (packages[key]["name"], key))
        self.query = ""
        self.view = 0  # Index into VIEWS.
        self.filtered = self.keys[:]
        self.cursor = 0
        self.marked = set()
        self.focus = 0
        self.offsets = [0, 0, 0]
        self.files = {}
        self.pending = None
        self.current = None

    def selected(self):
        return sorted(self.marked) or self.filtered[self.cursor : self.cursor + 1]

    def filter(self):
        view = VIEWS[self.view][0]
        self.filtered = [
            key
            for key in self.keys
            if matches(self.query, self.packages[key]["name"])
            and in_view(view, key, self.packages, self.graphs[1])
        ]
        self.cursor = 0

    def toggle(self):
        if self.filtered:
            key = self.filtered[self.cursor]
            self.marked.symmetric_difference_update({key})

    def row(self, key, width):
        package = self.packages[key]
        tag = "cask" if package["kind"] == "cask" else "dep" if requested(package) is False else ""
        text = ("[x] " if key in self.marked else "[ ] ") + package["name"]
        room = max(1, width - len(tag) - 1)
        text = text if len(text) <= room else text[: room - 1] + "…"
        return text.ljust(room) + " " + tag if tag else text

    def put(self, screen, y, x, text, width, attr=0):
        try:
            screen.addnstr(y, x, clean(text), max(0, width), attr or self.colors.get("text"))
        except curses.error:
            pass  # A resize or a full bottom-right cell can race with a redraw.

    def box(self, screen, y, x, height, width, title, lines, offset, focused):
        window = screen.derwin(height, width, y, x)
        window.bkgd(" ", self.colors.get("text"))
        window.erase()
        window.attrset(self.colors.get("accent" if focused else "border"))
        window.box()
        window.attrset(self.colors.get("text"))
        self.put(
            window,
            0,
            2,
            " " + title + " ",
            width - 4,
            curses.A_BOLD | self.colors.get("accent" if focused else "blue"),
        )
        offset = max(0, min(offset, max(0, len(lines) - (height - 2))))
        for row, line in enumerate(lines[offset : offset + height - 2], 1):
            role = (
                "green"
                if line.startswith("[x]")
                else "blue"
                if line.startswith(HEADINGS)
                else "text"
            )
            self.put(window, row, 1, line, width - 2, self.colors.get(role))
        if len(lines) > height - 2:
            self.put(
                window,
                height - 1,
                2,
                f" {offset + 1}-{min(len(lines), offset + height - 2)}/{len(lines)} ",
                width - 4,
            )
        return offset

    def draw(self, screen):
        """Paint the browser; popups call this so they always sit on a clean frame."""
        height, width = screen.getmaxyx()
        screen.bkgd(" ", self.colors.get("text"))
        screen.erase()
        if height < 24 or width < 80:
            self.put(screen, 0, 0, "Resize terminal to at least 80x24. Esc quits.", width - 1)
            return
        key = self.filtered[self.cursor] if self.filtered else None
        left = max(26, width * 35 // 100)
        right_width = width - left - 1
        available = height - 3
        description_height = max(8, available // 4)
        files_height = (available - description_height - 2) // 2
        dependency_height = available - description_height - files_height - 2
        status = f"{len(self.filtered)} of {len(self.keys)} shown · {len(self.marked)} marked"
        status += "  DRY RUN" if self.dry_run else ""
        search = f"Show: {VIEWS[self.view][1]} (F4)  ·  Filter: " + (
            self.query + "▏" if self.query else "type to filter"
        )
        self.put(screen, 0, 0, search, width - 1, self.colors.get("accent") | curses.A_BOLD)
        x = max(len(search) + 2, width - len(status) - 1)
        self.put(
            screen,
            0,
            x,
            status,
            width - x - 1,
            self.colors.get("yellow" if self.dry_run else "muted") | curses.A_BOLD,
        )
        start = max(0, self.cursor - (available - 3))
        rows = [self.row(item, left - 2) for item in self.filtered]
        self.box(screen, 1, 0, available, left, "Packages", rows, start, self.focus == 0)
        if key:
            self.put(
                screen,
                2 + self.cursor - start,
                1,
                rows[self.cursor],
                left - 2,
                self.colors.get("selected"),
            )
            package = self.packages[key]
            origin = {
                True: "you installed it",
                False: "installed automatically as a dependency",
                None: "install reason unknown",
            }[requested(package)]
            description = [
                f"{package['name']} — {package['kind']} {', '.join(package['versions'])}",
                origin[0].upper() + origin[1:] + ".",
                "",
                package["desc"],
                "",
                package["homepage"],
            ]
            contents = [
                description,
                self.files.get(key, ["Loading files…"]),
                dependency_lines(key, self.graphs, self.packages),
            ]
        else:
            contents = [["No matching packages."], [], []]
        y = 1
        for index, (title, pane_height, lines) in enumerate(
            zip(
                ("Description", "Files / cask artifacts", "Dependencies"),
                (description_height, files_height, dependency_height),
                contents,
            )
        ):
            self.offsets[index] = self.box(
                screen,
                y,
                left + 1,
                pane_height,
                right_width,
                title,
                wrapped(lines, right_width - 2),
                self.offsets[index],
                self.focus == index + 1,
            )
            y += pane_height + 1
        self.put(
            screen,
            height - 2,
            0,
            "Space mark · Enter review removal · Tab next box · Esc "
            + ("show all" if self.query or self.view else "quit"),
            width - 1,
        )
        self.put(
            screen,
            height - 1,
            0,
            "F1/? help · F2 theme · F3 history & undo · F4 show type · F5 refresh",
            width - 1,
            self.colors.get("muted"),
        )

    def run(self, screen):
        try:
            curses.raw()
            curses.curs_set(0)
            curses.set_escdelay(50)  # Esc responds at once instead of after curses' 1 s wait.
        except curses.error:
            pass
        screen.keypad(True)
        screen.timeout(100)
        self.colors.apply(self.theme)
        if self.actions:
            self.refresh_inventory(screen)
        # At most one brew process runs; rapid navigation does not queue stale work.
        with ThreadPoolExecutor(max_workers=1) as pool:
            while True:
                height, width = screen.getmaxyx()
                if height < 24 or width < 80:
                    self.draw(screen)
                    screen.refresh()
                    if screen.getch() in (27, 3):
                        return []
                    continue
                key = self.filtered[self.cursor] if self.filtered else None
                if key != self.current:
                    self.current = key
                    self.offsets = [0, 0, 0]
                if self.pending and self.pending[1].done():
                    pending_key, future = self.pending
                    try:
                        self.files[pending_key] = future.result() or ["(no files reported)"]
                    except Exception as exc:
                        self.files[pending_key] = [f"Unable to list files: {exc}"]
                    self.pending = None
                if key and key not in self.files and not self.pending:
                    self.pending = (key, pool.submit(self.load_files, self.packages[key]))
                self.draw(screen)
                screen.refresh()
                try:
                    event = screen.get_wch()
                except curses.error:
                    continue
                if event == "\x03":
                    return []
                if event == "\x1b":
                    if self.query or self.view:
                        self.query, self.view = "", 0
                        self.filter()
                    elif not self.marked or self.ask(
                        screen,
                        "Quit?",
                        [f"{len(self.marked)} marked packages will not be removed."],
                        "Quit",
                    ):
                        return []
                    continue
                if event in (curses.KEY_F4, curses.KEY_F16):  # F16 is Shift-F4 in many terminals.
                    step = -1 if event == curses.KEY_F16 else 1
                    self.view = (self.view + step) % len(VIEWS)
                    self.filter()
                    continue
                if event in (curses.KEY_F1, "?"):
                    self.help(screen)
                    continue
                if event == "\x1a":
                    # Raw mode delivers Ctrl-Z as a key; suspend like a normal job.
                    curses.endwin()
                    os.kill(os.getpid(), signal.SIGTSTP)
                    screen.refresh()
                    continue
                if event in ("\n", "\r", curses.KEY_ENTER):
                    if not self.actions:
                        return self.selected()
                    self.perform(screen, "remove", self.selected())
                    continue
                if event == curses.KEY_F2:
                    self.choose_theme(screen)
                    continue
                if self.actions and event == curses.KEY_F3:
                    self.show_history(screen)
                    continue
                if self.actions and event == curses.KEY_F5:
                    self.refresh_inventory(screen)
                    continue
                if event == "\t":
                    self.focus = (self.focus + 1) % 4
                elif event == curses.KEY_BTAB:
                    self.focus = (self.focus - 1) % 4
                elif event == " ":
                    self.toggle()
                elif event in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE):
                    delta = -1 if event in (curses.KEY_UP, curses.KEY_PPAGE) else 1
                    delta *= 5 if event in (curses.KEY_PPAGE, curses.KEY_NPAGE) else 1
                    if self.focus:
                        self.offsets[self.focus - 1] = max(0, self.offsets[self.focus - 1] + delta)
                    else:
                        self.cursor = max(0, min(len(self.filtered) - 1, self.cursor + delta))
                elif event == "\x01":
                    self.marked.update(self.filtered)
                elif event == "\x04":
                    self.marked.difference_update(self.filtered)
                elif event in (curses.KEY_BACKSPACE, "\x7f", "\b", "\x15"):
                    self.query = "" if event == "\x15" else self.query[:-1]
                    self.filter()
                elif isinstance(event, str) and event.isprintable():
                    self.query += event
                    self.filter()


def select(packages, load_files, **options):
    browser = Browser(packages, load_files, **options)

    return curses.wrapper(browser.run)
