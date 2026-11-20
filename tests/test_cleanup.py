import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import brew_cleanup as app
from popup_process import CommandRunner


def package(name, kind="formula"):
    return dict(name=name, kind=kind, versions=["1.0"], desc="Example", homepage="", metadata={})


class Transactions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name})
        self.state.start()
        self.addCleanup(self.state.stop)
        self.before = {"formula:app": package("app"), "formula:dep": package("dep")}

    def record(self):
        return json.loads(app.records()[0].read_text())

    def test_removal_includes_autoremoved_dependencies(self):
        def run(*args):
            self.assertEqual(self.record()["status"], "running")
            self.assertEqual(self.record()["before"], self.before)

        with (
            patch.object(app, "brew", side_effect=run),
            patch.object(app, "inventory", return_value={}),
        ):
            app.transact(
                "remove",
                self.before,
                ["formula:app"],
                [["uninstall", "--formula", "app"], ["autoremove"]],
            )
        record = self.record()
        self.assertEqual(record["removed"], self.before)
        self.assertEqual(record["status"], "complete")
        self.assertEqual(len(record["commands"]), 2)
        self.assertIn("finished_at", record)

    def test_partial_failure_is_recorded_and_skips_autoremove(self):
        with patch.object(app, "brew", side_effect=subprocess.CalledProcessError(1, "brew")) as run:
            with patch.object(app, "inventory", return_value={"formula:dep": package("dep")}):
                with self.assertRaises(subprocess.CalledProcessError):
                    app.transact("remove", self.before, [], [["uninstall"], ["autoremove"]])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(self.record()["status"], "failed")
        self.assertEqual(list(self.record()["removed"]), ["formula:app"])

    def test_interrupt_preserves_history(self):
        with (
            patch.object(app, "brew", side_effect=KeyboardInterrupt),
            patch.object(app, "inventory", return_value={}),
        ):
            with self.assertRaises(KeyboardInterrupt):
                app.transact("remove", self.before, [], [["uninstall"]])
        self.assertEqual(self.record()["status"], "interrupted")
        self.assertEqual(self.record()["removed"], self.before)

    def test_ui_cancel_is_recorded_as_interrupted(self):
        runner = CommandRunner(lambda text: None)
        runner.cancelled.set()
        with patch.object(app, "inventory", return_value={}):
            with self.assertRaises(subprocess.CalledProcessError):
                app.transact("remove", self.before, [], [["uninstall"]], run_command=runner)
        self.assertEqual(self.record()["status"], "interrupted")

    def test_successful_removal_summary_is_printed_after_browser(self):
        def browse(packages, actions, **options):
            plan = actions.prepare("remove", ["formula:app"])
            actions.execute(plan, lambda *args: None, lambda text: None)

        with (
            patch.object(app, "select", side_effect=browse),
            patch.object(app, "inventory", side_effect=[self.before, self.before, {}]),
            patch("builtins.print") as stdout,
        ):
            app.remove(theme="terminal")
        stdout.assert_called_once()
        self.assertIn("Undo: brew-cleanup undo " + self.record()["id"], stdout.call_args.args[0])

    def test_snapshot_failure_can_be_recovered(self):
        with (
            patch.object(app, "brew"),
            patch.object(app, "inventory", side_effect=RuntimeError("offline")),
        ):
            with self.assertRaisesRegex(RuntimeError, "Final inventory failed"):
                app.transact("remove", self.before, [], [["uninstall"]])
        record = self.record()
        self.assertEqual(record["status"], "needs-recovery")
        with (
            patch.object(app, "inventory", return_value={}),
            patch.object(app, "confirm", return_value=True),
        ):
            app.recover(record["id"])
        self.assertEqual(self.record()["removed"], self.before)
        self.assertEqual(self.record()["status"], "recovered")

    def test_undo_skips_installed_and_preserves_kind(self):
        record = dict(
            id="test", action="remove", removed={**self.before, "cask:app": package("app", "cask")}
        )
        app.save(app.state_dir() / "history/test.json", record)
        with (
            patch.object(app, "inventory", return_value=self.before),
            patch.object(app, "confirm", return_value=True),
        ):
            with patch.object(app, "transact") as transact:
                app.undo("test")
        self.assertEqual(transact.call_args.args[3], [["install", "--cask", "app"]])
        self.assertEqual(transact.call_args.kwargs, {"source": "test"})

    def test_browser_receives_dry_run_and_theme(self):
        with patch.object(app, "select") as select:
            app.remove(dry_run=True, theme="catppuccin-latte")
        self.assertTrue(select.call_args.kwargs["dry_run"])
        self.assertEqual(select.call_args.kwargs["theme"], "catppuccin-latte")
        self.assertIsInstance(select.call_args.kwargs["actions"], app.UIActions)

    def test_review_does_not_mutate_or_write_history(self):
        with patch.object(app, "inventory", return_value=self.before):
            plan = app.UIActions().prepare("remove", ["formula:app"])
        self.assertEqual(plan["commands"], [["uninstall", "--formula", "app"], ["autoremove"]])
        self.assertEqual(app.records(), [])

    def test_changed_inventory_blocks_execution(self):
        with patch.object(app, "inventory", return_value=self.before):
            plan = app.UIActions().prepare("remove", ["formula:app"])
        with patch.object(app, "inventory", return_value={}):
            with self.assertRaisesRegex(RuntimeError, "changed since review"):
                app.UIActions().execute(plan, lambda *args: self.fail("Unexpected command"), print)
        self.assertEqual(app.records(), [])

    def test_ui_runner_is_journaled_without_printing(self):
        with patch.object(app, "inventory", return_value=self.before):
            plan = app.UIActions().prepare("remove", ["formula:app"])
        commands, output = [], []
        with patch.object(app, "inventory", side_effect=[self.before, {}]):
            with patch("builtins.print") as stdout:
                app.UIActions().execute(plan, lambda *args: commands.append(args), output.append)
        stdout.assert_not_called()
        self.assertEqual(commands, [("uninstall", "--formula", "app"), ("autoremove",)])
        self.assertEqual(self.record()["removed"], self.before)
        self.assertTrue(output)

    def test_lock_rejects_concurrent_operation(self):
        with app.locked(), self.assertRaises(RuntimeError), app.locked():
            pass

    def test_inventory_separates_formula_and_cask(self):
        data = dict(
            formulae=[dict(name="app", full_name="org/tap/app", installed=[dict(version="1")])],
            casks=[dict(token="app", installed="2")],
        )
        with patch.object(
            app, "brew", return_value=subprocess.CompletedProcess([], 0, json.dumps(data))
        ):
            result = app.inventory()
        self.assertEqual(set(result), {"formula:org/tap/app", "cask:app"})
        self.assertEqual(result["cask:app"]["versions"], ["2"])

    def test_brew_disables_implicit_autoremove(self):
        with patch.object(subprocess, "run") as run:
            app.brew("uninstall", "--formula", "app")
        self.assertEqual(run.call_args.kwargs["env"]["HOMEBREW_NO_AUTOREMOVE"], "1")
        self.assertEqual(run.call_args.args[0], ["brew", "uninstall", "--formula", "app"])

    def test_unknown_record_cannot_escape_history_directory(self):
        with self.assertRaises(RuntimeError):
            app.load_record("../../anything")

    def test_save_is_private(self):
        path = Path(self.tmp.name) / "record.json"
        app.save(path, {"ok": True})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
