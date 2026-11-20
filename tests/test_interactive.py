import curses
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import brew_cleanup as app
import package_browser as ui
import themes
import ui_dialogs
from popup_process import CommandRunner


class Screen:
    def __init__(self, events=()):
        self.events = iter(events)

    def getmaxyx(self):
        return 30, 100

    def get_wch(self):
        try:
            return next(self.events)
        except StopIteration:
            time.sleep(0.001)
            raise curses.error from None

    def derwin(self, *args):
        return self

    def __getattr__(self, name):
        return lambda *args: None


class InteractiveTests(unittest.TestCase):
    def browser(self):
        return ui.Browser({}, lambda p: [], actions=Mock())

    def test_confirmation_defaults_to_cancel(self):
        browser = self.browser()
        plan = dict(action="remove", note="Autoremove", selected=["formula:a"], commands=[])
        self.assertFalse(browser.confirm_plan(Screen(["\n"]), plan))
        self.assertFalse(browser.confirm_plan(Screen(["\x1b"]), plan))
        self.assertTrue(browser.confirm_plan(Screen(["\t", "\n"]), plan))

    def test_dry_run_cannot_confirm_removal(self):
        browser = self.browser()
        browser.dry_run = True
        with patch.object(browser, "message") as message:
            self.assertFalse(
                browser.confirm_plan(None, dict(action="remove", note="", selected=[], commands=[]))
            )
        message.assert_called_once()

    def test_cancelled_review_does_not_execute(self):
        browser = self.browser()
        plan = dict(selected=["formula:a"])
        with patch.object(browser, "progress", return_value=(True, plan)) as progress:
            with patch.object(browser, "confirm_plan", return_value=False):
                browser.perform(None, "remove", ["formula:a"])
        self.assertEqual(progress.call_count, 1)
        browser.actions.execute.assert_not_called()

    def test_removal_refreshes_after_success_or_failure(self):
        for success in (True, False):
            browser = self.browser()
            with patch.object(
                browser, "progress", side_effect=[(True, dict(selected=["a"])), (success, None)]
            ):
                with (
                    patch.object(browser, "confirm_plan", return_value=True),
                    patch.object(browser, "refresh_inventory") as refresh,
                ):
                    browser.perform(None, "remove", ["a"])
            refresh.assert_called_once()

    def test_browser_stays_open_after_review(self):
        browser = self.browser()
        with (
            patch.object(browser, "refresh_inventory"),
            patch.object(browser, "perform") as perform,
        ):
            self.assertEqual(browser.run(Screen(["\n", "\n", "\x1b"])), [])
        self.assertEqual(perform.call_count, 2)

    def test_progress_captures_error_and_returns_to_ui(self):
        browser = self.browser()

        def fail(runner, emit):
            emit("some output\n")
            raise RuntimeError("test failure")

        with (
            patch.object(browser, "read_key", return_value="\n"),
            patch.object(browser, "popup", return_value=0) as popup,
        ):
            ok, result = browser.progress(None, "Test", fail)
        self.assertFalse(ok)
        self.assertIsNone(result)
        self.assertIn("test failure", "\n".join(popup.call_args.args[2]))

    def test_theme_selection_and_persistence(self):
        browser = self.browser()
        with patch.object(browser.colors, "apply") as apply:
            browser.choose_theme(Screen([curses.KEY_DOWN, "\n"]))
        apply.assert_called_once_with("catppuccin-latte")
        browser.actions.save_theme.assert_called_once_with("catppuccin-latte")
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            app.UIActions().save_theme("catppuccin-latte")
            self.assertEqual(app.configured_theme(), "catppuccin-latte")
            app.config_path().write_text('{"theme": "terminal", "other": 1}')
            app.UIActions().save_theme("catppuccin-latte")
            self.assertEqual(json.loads(app.config_path().read_text())["other"], 1)
            app.config_path().write_text("invalid")
            self.assertEqual(app.configured_theme(), "catppuccin-mocha")

    def test_pty_crlf_output_is_not_double_spaced(self):
        browser = self.browser()

        def task(runner, emit):
            emit("one\r\ntwo\r\n")

        with (
            patch.object(browser, "read_key", return_value="\n"),
            patch.object(browser, "popup", return_value=0) as popup,
        ):
            browser.progress(None, "Test", task)
        lines = popup.call_args.args[2]
        self.assertEqual(lines[:2], ["one", "two"])

    def test_direct_color_without_extended_support_does_not_crash(self):
        colors = themes.Colors()
        with patch.multiple(
            curses,
            has_colors=Mock(return_value=True),
            start_color=Mock(),
            use_default_colors=Mock(),
            has_extended_color_support=Mock(return_value=False),
            COLORS=16777216,
            create=True,
        ):
            with (
                patch.object(curses, "init_pair") as init_pair,
                patch.object(curses, "color_pair", return_value=1),
            ):
                colors.apply(themes.DEFAULT)
        self.assertTrue(
            all(value < 256 for call in init_pair.call_args_list for value in call.args[1:])
        )

    def test_no_color_terminal_is_supported(self):
        colors = themes.Colors()
        with patch.object(curses, "has_colors", return_value=False):
            colors.apply("catppuccin-mocha")
        self.assertEqual(colors.get("text"), 0)
        self.assertEqual(colors.get("selected"), curses.A_REVERSE)

    def test_terminal_palette_is_never_reprogrammed(self):
        colors = themes.Colors()
        with patch.multiple(
            curses,
            has_colors=Mock(return_value=True),
            start_color=Mock(),
            use_default_colors=Mock(),
            COLORS=256,
            create=True,
        ):
            with patch.object(curses, "init_color") as init:
                with (
                    patch.object(curses, "init_pair"),
                    patch.object(curses, "color_pair", return_value=1),
                ):
                    colors.apply(themes.DEFAULT)
                    colors.apply("terminal")
        init.assert_not_called()


class HistoryAndReviewTests(unittest.TestCase):
    def test_history_rows_are_readable(self):
        record = dict(
            id="20261119T210000Z-abc",
            action="remove",
            status="complete",
            started_at="2026-11-19T21:00:00+00:00",
            removed={"formula:bat": {}, "cask:app": {}},
        )
        row = ui_dialogs.history_row(record)
        self.assertIn("Removed", row)
        self.assertIn("app (cask), bat", row)
        self.assertNotIn("20261119T", row)
        del record["removed"]
        self.assertIn("needs recovery", ui_dialogs.history_row(record))
        self.assertIn("Transaction ID: 20261119T210000Z-abc", ui_dialogs.history_details(record))

    def test_review_names_packages_and_autoremove(self):
        packages = {
            "formula:bat": dict(
                kind="formula",
                name="bat",
                versions=["1"],
                metadata=dict(installed=[dict(installed_on_request=True)]),
            ),
            "formula:old": dict(
                kind="formula",
                name="old",
                versions=["2"],
                metadata=dict(installed=[dict(installed_on_request=False)]),
            ),
        }
        browser = ui.Browser(packages, lambda p: [], actions=Mock())
        lines = browser.plan_lines(
            dict(
                action="remove",
                selected={"formula:bat": packages["formula:bat"]},
                commands=[["uninstall", "--formula", "bat"], ["autoremove"]],
                note="",
            )
        )
        self.assertEqual(lines[:2], ["Remove 1 package:", "  • bat 1"])
        self.assertIn("  • old 2", lines)


class TerminalProcessTests(unittest.TestCase):
    def test_private_terminal_accepts_prompt_input_and_captures_output(self):
        helper = Path(__file__).resolve().parents[1] / "terminal_process.py"
        result = subprocess.run(
            [
                sys.executable,
                str(helper),
                sys.executable,
                "-c",
                "import sys; print('TTY', sys.stdin.isatty()); print('REPLY', input('Prompt: '))",
            ],
            input="hello\n",
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("TTY True", result.stdout)
        self.assertIn("REPLY hello", result.stdout)

    def test_runner_streams_and_preserves_exit_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "brew"
            fake.write_text("#!/bin/sh\nprintf 'first output\\n'\nexit 7\n")
            fake.chmod(0o700)
            output = []
            with patch.dict(os.environ, {"PATH": tmp + os.pathsep + os.environ["PATH"]}):
                with self.assertRaises(subprocess.CalledProcessError) as raised:
                    CommandRunner(output.append)("uninstall", "--formula", "fake")
            self.assertEqual(raised.exception.returncode, 7)
            self.assertIn("first output", "".join(output))
