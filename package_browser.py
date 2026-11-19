"""Terminal package browser with independently scrollable detail boxes."""

import curses
import textwrap
from concurrent.futures import ThreadPoolExecutor


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
        label = key + (" [not installed]" if key not in packages else "")
        repeated = key in seen
        lines.append(
            prefix + ("└─ " if last else "├─ ") + label + (" [shown above]" if repeated else "")
        )
        if repeated:
            continue
        seen.add(key)
        children = sorted(graph.get(key, []))
        stack.extend(
            (child, prefix + ("   " if last else "│  "), index == len(children) - 1)
            for index, child in reversed(list(enumerate(children)))
        )
    return lines or ["  (none)"]


def dependency_lines(key, graphs, packages):
    return (
        ["DEPENDS ON (runtime)"]
        + tree_lines(key, graphs[0], packages)
        + ["", "REQUIRED BY (installed packages)"]
        + tree_lines(key, graphs[1], packages)
    )


def matches(query, text):
    """Case-insensitive subsequence matching, like a simple fuzzy finder."""
    chars = iter(text.casefold())
    return all(any(char == candidate for candidate in chars) for char in query.casefold())


def clean(text):
    return "".join(char if char.isprintable() else " " for char in text)


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


class Browser:
    def __init__(self, packages, load_files):
        self.packages = packages
        self.load_files = load_files
        self.graphs = dependency_graph(packages)
        self.keys = sorted(packages, key=lambda key: (packages[key]["name"], key))
        self.query = ""
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
        self.filtered = [key for key in self.keys if matches(self.query, key)]
        self.cursor = 0

    def toggle(self):
        if self.filtered:
            key = self.filtered[self.cursor]
            self.marked.symmetric_difference_update({key})

    def put(self, screen, y, x, text, width, attr=0):
        try:
            screen.addnstr(y, x, clean(text), max(0, width), attr)
        except curses.error:
            pass  # A resize or a full bottom-right cell can race with a redraw.

    def box(self, screen, y, x, height, width, title, lines, offset, focused):
        window = screen.derwin(height, width, y, x)
        window.erase()
        window.box()
        self.put(
            window,
            0,
            2,
            " " + title + " ",
            width - 4,
            curses.A_BOLD | (curses.A_REVERSE if focused else 0),
        )
        offset = max(0, min(offset, max(0, len(lines) - (height - 2))))
        for row, line in enumerate(lines[offset : offset + height - 2], 1):
            self.put(window, row, 1, line, width - 2)
        if len(lines) > height - 2:
            self.put(
                window,
                height - 1,
                2,
                f" {offset + 1}-{min(len(lines), offset + height - 2)}/{len(lines)} ",
                width - 4,
            )
        return offset

    def run(self, screen):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.keypad(True)
        screen.timeout(100)
        # At most one brew process runs; rapid navigation does not queue stale work.
        with ThreadPoolExecutor(max_workers=1) as pool:
            while True:
                height, width = screen.getmaxyx()
                screen.erase()
                if height < 24 or width < 80:
                    self.put(
                        screen, 0, 0, "Resize terminal to at least 80x24. Esc cancels.", width - 1
                    )
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
                left = max(26, width * 35 // 100)
                right_width = width - left - 1
                available = height - 3
                description_height = max(6, available // 4)
                files_height = (available - description_height - 2) // 2
                dependency_height = available - description_height - files_height - 2
                self.put(
                    screen, 0, 0, f"Search: {self.query}  | {len(self.marked)} selected", width - 1
                )
                start = max(0, self.cursor - (available - 3))
                rows = [
                    f"{'[x]' if item in self.marked else '[ ]'} {self.packages[item]['name']} ({self.packages[item]['kind']})"
                    for item in self.filtered
                ]
                self.box(screen, 1, 0, available, left, "Packages", rows, start, self.focus == 0)
                if key:
                    self.put(
                        screen,
                        2 + self.cursor - start,
                        1,
                        rows[self.cursor],
                        left - 2,
                        curses.A_REVERSE,
                    )
                    package = self.packages[key]
                    description = [
                        f"{package['name']} ({package['kind']})",
                        "Installed: " + ", ".join(package["versions"]),
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
                    "Space: mark  Enter: review  Esc: cancel  Tab: focus box  Up/Down/PgUp/PgDn: scroll",
                    width - 1,
                )
                self.put(
                    screen,
                    height - 1,
                    0,
                    "Type: filter  Backspace: erase  Ctrl-U: clear filter  Ctrl-A/D: mark/clear matches",
                    width - 1,
                )
                screen.refresh()
                try:
                    event = screen.get_wch()
                except curses.error:
                    continue
                if event in ("\x1b", "\x03"):
                    return []
                if event in ("\n", "\r", curses.KEY_ENTER):
                    return self.selected()
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


def select(packages, load_files):
    return curses.wrapper(Browser(packages, load_files).run)
