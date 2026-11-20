"""Record a reproducible, simulated usage demo with asciinema (no real brew calls)."""

import os
import pty
import select
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from brew_cleanup import __version__  # noqa: E402

# The release workflow checks this title, so the README demo is re-recorded for each version.
TITLE = f"brew-cleanup {__version__}: filter, review, remove and undo (simulated)"
F1, F2, F3, F4 = "\x1bOP", "\x1bOQ", "\x1bOR", "\x1bOS"
UP, DOWN, ESC, CLEAR = "\x1bOA", "\x1bOB", "\x1b", "\x15"
# (seconds from start, keys). Leave >= 1 s after a bare Esc so it is not read as Alt-<key>.
EVENTS = [
    (1.5, "?"),  # key help
    (5.5, ESC),
    (7, F4),  # show: formulae
    (8, F4),  # casks
    (9.5, F4),  # installed by you
    (11, F4),  # dependencies
    (12.5, F4),  # unused dependencies
    (14.5, ESC),  # back to all packages
    (16, "b"),
    (16.4, "a"),
    (17.4, " "),  # filter "ba", mark bat
    (18.4, CLEAR),
    (19, "f"),
    (19.4, "d"),
    (20.2, " "),  # filter "fd", mark fd
    (21, CLEAR),
    (22, "\r"),  # review: names, autoremove estimate, commands
    (26.5, "y"),  # remove
    (31, "\r"),  # close the result; the browser refreshes
    (33, F3),  # history
    (35, "\r"),  # details
    (38, ESC),
    (39.5, "u"),  # review the undo
    (42, "y"),
    (45.5, "\r"),
    (47, F2),
    (48, DOWN),
    (49, "\r"),  # Catppuccin Latte
    (51.5, F2),
    (52.5, UP),
    (53, "\r"),  # back to Mocha
    (55, ESC),  # quit
]
TIMEOUT = 75


def main():
    os.chdir(ROOT)
    os.environ["TERM"] = "xterm-256color"
    pid, master = pty.fork()
    if pid == 0:
        os.execvp(
            "asciinema",
            [
                "asciinema",
                "record",
                "--overwrite",
                "--return",
                "--output-format",
                "asciicast-v2",
                "--window-size",
                "110x34",
                "--title",
                TITLE,
                "--command",
                "python3 scripts/demo.py",
                "docs/demo.cast",
            ],
        )
    start, index = time.monotonic(), 0
    try:
        while time.monotonic() - start < TIMEOUT:
            elapsed = time.monotonic() - start
            if index < len(EVENTS) and elapsed >= EVENTS[index][0]:
                os.write(master, EVENTS[index][1].encode())
                index += 1
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    if not os.read(master, 65536):
                        break
                except OSError:
                    break
        else:
            os.kill(pid, 15)
            raise RuntimeError(f"Demo did not exit within {TIMEOUT} seconds")
    finally:
        os.close(master)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


if __name__ == "__main__":
    sys.exit(main())
