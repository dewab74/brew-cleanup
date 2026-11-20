import curses
import unittest

import package_browser as ui


def package(name, **metadata):
    return dict(
        name=name,
        kind="formula",
        metadata=metadata,
        versions=["1"],
        desc="Description",
        homepage="",
    )


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.packages = {
            "formula:app": package(
                "app",
                installed=[
                    dict(
                        runtime_dependencies=[
                            dict(full_name="dep", declared_directly=True),
                            dict(full_name="leaf", declared_directly=False),
                        ]
                    )
                ],
            ),
            "formula:dep": package("dep", dependencies=["leaf"]),
            "formula:leaf": package("leaf"),
        }

    def test_direct_runtime_edges_and_reverse_tree(self):
        forward, reverse = ui.dependency_graph(self.packages)
        self.assertEqual(forward["formula:app"], {"formula:dep"})
        self.assertEqual(reverse["formula:leaf"], {"formula:dep"})
        self.assertEqual(
            ui.tree_lines("formula:leaf", reverse, self.packages),
            ["└─ dep", "   └─ app"],
        )

    def test_cask_dependencies_and_tapped_formula_resolution(self):
        self.packages["formula:org/tap/tool"] = package("org/tap/tool")
        self.packages["cask:app"] = dict(
            kind="cask",
            name="app",
            metadata=dict(depends_on=dict(formula=["tool"], cask=["missing"])),
        )
        forward, reverse = ui.dependency_graph(self.packages)
        self.assertEqual(forward["cask:app"], {"formula:org/tap/tool", "cask:missing"})
        self.assertIn("cask:app", reverse["formula:org/tap/tool"])
        self.assertIn(
            "missing (cask) — not installed",
            "\n".join(ui.tree_lines("cask:app", forward, self.packages)),
        )

    def test_empty_receipt_does_not_invent_current_dependencies(self):
        self.packages["formula:app"]["metadata"] = dict(
            dependencies=["dep"], installed=[dict(runtime_dependencies=[])]
        )
        self.assertEqual(ui.dependency_graph(self.packages)[0]["formula:app"], set())

    def test_cycles_terminate(self):
        graph = {"a": {"b"}, "b": {"a"}}
        self.assertEqual(len(ui.tree_lines("a", graph, {"a": {}, "b": {}})), 2)
        self.assertIn("(shown above)", ui.tree_lines("a", graph, {})[-1])

    def test_marks_survive_filter_and_space_toggles(self):
        browser = ui.Browser(self.packages, lambda p: [])
        browser.toggle()
        browser.query = "leaf"
        browser.filter()
        browser.toggle()
        self.assertEqual(browser.selected(), ["formula:app", "formula:leaf"])
        browser.toggle()
        self.assertEqual(browser.selected(), ["formula:app"])

    def test_no_match_enter_is_empty(self):
        browser = ui.Browser(self.packages, lambda p: [])
        browser.query = "no-such-package"
        browser.filter()
        self.assertEqual(browser.selected(), [])

    def test_keyboard_selection_focus_and_cancel(self):
        class Screen:
            def __init__(self, events):
                self.events = iter(events)

            def getmaxyx(self):
                return 30, 100

            def get_wch(self):
                return next(self.events)

            def derwin(self, *args):
                return self

            def __getattr__(self, name):
                return lambda *args: None

        browser = ui.Browser(self.packages, lambda p: ["file"])
        self.assertEqual(
            browser.run(Screen([" ", curses.KEY_DOWN, " ", "\t", curses.KEY_DOWN, "\n"])),
            ["formula:app", "formula:dep"],
        )
        self.assertEqual(browser.focus, 1)
        # Quitting with packages still marked asks first; Y confirms.
        self.assertEqual(browser.run(Screen(["\x1b", "y"])), [])


def receipt(on_request):
    return [dict(installed_on_request=on_request, runtime_dependencies=[])]


class CleanupGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.packages = {
            "formula:app": package("app", installed=receipt(True), dependencies=["lib"]),
            "formula:lib": package("lib", installed=receipt(False), dependencies=["base"]),
            "formula:base": package("base", installed=receipt(False)),
            "formula:stale": package("stale", installed=receipt(False)),
            "formula:other": package("other", installed=receipt(True), dependencies=["base"]),
            "cask:tool": dict(
                kind="cask", name="tool", versions=["2"], desc="", homepage="", metadata={}
            ),
        }
        for key in ("formula:app", "formula:lib", "formula:other"):
            deps = self.packages[key]["metadata"]["dependencies"]
            self.packages[key]["metadata"]["installed"][0]["runtime_dependencies"] = [
                dict(full_name=name, declared_directly=True) for name in deps
            ]
        self.graphs = ui.dependency_graph(self.packages)

    def view(self, name):
        return sorted(
            key for key in self.packages if ui.in_view(name, key, self.packages, self.graphs[1])
        )

    def test_type_views(self):
        self.assertEqual(self.view("cask"), ["cask:tool"])
        self.assertEqual(len(self.view("formula")), 5)
        self.assertEqual(self.view("requested"), ["cask:tool", "formula:app", "formula:other"])
        self.assertEqual(self.view("dependency"), ["formula:base", "formula:lib", "formula:stale"])
        self.assertEqual(self.view("unused"), ["formula:stale"])

    def test_f4_cycles_views_and_esc_resets(self):
        browser = ui.Browser(self.packages, lambda p: [])
        browser.filter()
        browser.view = 2
        browser.filter()
        self.assertEqual(browser.filtered, ["cask:tool"])

    def test_removal_impact_predicts_autoremove_and_blockers(self):
        blockers, autoremoved = ui.removal_impact(["formula:app"], self.packages, self.graphs)
        self.assertEqual(blockers, {})
        # lib becomes unused; base is still needed by other; stale was already unused.
        self.assertEqual(autoremoved, ["formula:lib", "formula:stale"])
        blockers, _ = ui.removal_impact(["formula:base"], self.packages, self.graphs)
        self.assertEqual(blockers, {"formula:base": ["formula:lib", "formula:other"]})

    def test_labels_hide_internal_keys(self):
        self.assertEqual(ui.label("formula:app", self.packages), "app")
        self.assertEqual(ui.label("cask:tool", self.packages), "tool (cask)")
