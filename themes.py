"""Semantic terminal colors. Catppuccin colors: github.com/catppuccin/palette."""

import curses

THEMES = {
    "catppuccin-mocha": {
        "base": "1e1e2e",
        "text": "cdd6f4",
        "muted": "a6adc8",
        "border": "585b70",
        "accent": "cba6f7",
        "blue": "89b4fa",
        "green": "a6e3a1",
        "red": "f38ba8",
        "yellow": "f9e2af",
        "surface": "313244",
    },
    "catppuccin-latte": {
        "base": "eff1f5",
        "text": "4c4f69",
        "muted": "6c6f85",
        "border": "9ca0b0",
        "accent": "8839ef",
        "blue": "1e66f5",
        "green": "40a02b",
        "red": "d20f39",
        "yellow": "df8e1d",
        "surface": "ccd0da",
    },
    "terminal": None,
}
DEFAULT = "catppuccin-mocha"


def rgb(value):
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def nearest(value, count):
    basic = [
        (0, 0, 0),
        (205, 0, 0),
        (0, 205, 0),
        (205, 205, 0),
        (0, 0, 238),
        (205, 0, 205),
        (0, 205, 205),
        (229, 229, 229),
        (127, 127, 127),
        (255, 0, 0),
        (0, 255, 0),
        (255, 255, 0),
        (92, 92, 255),
        (255, 0, 255),
        (0, 255, 255),
        (255, 255, 255),
    ]
    levels = [0, 95, 135, 175, 215, 255]
    colors = basic + [(r, g, b) for r in levels for g in levels for b in levels]
    colors += [(v, v, v) for v in range(8, 239, 10)]
    target = rgb(value)
    return min(
        range(min(count, len(colors))),
        key=lambda i: sum((a - b) ** 2 for a, b in zip(target, colors[i])),
    )


class Colors:
    def __init__(self):
        self.styles = {}

    def apply(self, name):
        self.styles = {}
        try:
            if not curses.has_colors():
                return
            curses.start_color()
            curses.use_default_colors()
            palette = THEMES[name]
            if palette is None:
                values = dict(
                    base=-1,
                    text=-1,
                    muted=curses.COLOR_WHITE,
                    border=curses.COLOR_BLUE,
                    accent=curses.COLOR_MAGENTA,
                    blue=curses.COLOR_CYAN,
                    green=curses.COLOR_GREEN,
                    red=curses.COLOR_RED,
                    yellow=curses.COLOR_YELLOW,
                    surface=-1,
                )
            else:
                # RGB pair values need ncurses extended-color support in this Python build.
                direct = (
                    curses.COLORS >= 16777216
                    and getattr(curses, "has_extended_color_support", lambda: False)()
                )
                values = {}
                for role, value in palette.items():
                    # Never reprogram a user's terminal palette. Direct-color terminfo
                    # accepts RGB indices; other terminals use the nearest existing color.
                    values[role] = int(value, 16) if direct else nearest(value, curses.COLORS)
            roles = {
                "text": ("text", "base"),
                "muted": ("muted", "base"),
                "border": ("border", "base"),
                "accent": ("accent", "base"),
                "selected": ("base", "accent"),
                "green": ("green", "base"),
                "red": ("red", "base"),
                "yellow": ("yellow", "base"),
                "blue": ("blue", "base"),
            }
            for pair, (role, (fg, bg)) in enumerate(roles.items(), 1):
                curses.init_pair(pair, values[fg], values[bg])
                self.styles[role] = curses.color_pair(pair)
        except (curses.error, ValueError, OverflowError):
            self.styles = {}

    def get(self, role):
        return self.styles.get(role, curses.A_REVERSE if role == "selected" else 0)
