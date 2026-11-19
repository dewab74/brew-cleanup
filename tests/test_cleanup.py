import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import brew_cleanup as app


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

    def test_dry_run_never_mutates(self):
        with (
            patch.object(app, "inventory", return_value=self.before),
            patch.object(app, "select", return_value=["formula:app"]),
        ):
            with patch.object(app, "brew") as brew, patch.object(app, "confirm") as confirm:
                app.remove(dry_run=True)
        brew.assert_not_called()
        confirm.assert_not_called()
        self.assertEqual(app.records(), [])

    def test_cancel_never_mutates(self):
        with (
            patch.object(app, "inventory", return_value=self.before),
            patch.object(app, "select", return_value=[]),
        ):
            with patch.object(app, "brew") as brew:
                app.remove()
        brew.assert_not_called()
        self.assertEqual(app.records(), [])

    def test_declined_confirmation_never_mutates(self):
        with (
            patch.object(app, "inventory", return_value=self.before),
            patch.object(app, "select", return_value=["formula:app"]),
        ):
            with (
                patch.object(app, "confirm", return_value=False),
                patch.object(app, "transact") as transact,
            ):
                app.remove()
        transact.assert_not_called()

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

    def test_fzf_selection_and_cancel(self):
        result = subprocess.CompletedProcess([], 0, "formula:app\tapp (formula)\tExample\n")
        with (
            patch.object(app.shutil, "which", return_value="fzf"),
            patch.object(subprocess, "run", return_value=result) as run,
        ):
            self.assertEqual(app.select(self.before), ["formula:app"])
        self.assertIn("--multi", run.call_args.args[0])
        self.assertIn("--preview-window=right:60%:wrap", run.call_args.args[0])
        result.returncode = 130
        with (
            patch.object(app.shutil, "which", return_value="fzf"),
            patch.object(subprocess, "run", return_value=result),
        ):
            self.assertEqual(app.select(self.before), [])

    def test_save_is_private(self):
        path = Path(self.tmp.name) / "record.json"
        app.save(path, {"ok": True})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
