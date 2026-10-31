"""Keep the visible CLI choices in sync with their dispatch branches."""
import ast
import inspect
import re
import textwrap
from collections import Counter


def test_main_menu_choices_match_dispatch(mod):
    tree = ast.parse(textwrap.dedent(inspect.getsource(mod.main_menu_loop)))
    labels = []
    choices = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                match = re.match(r"^  ([A-Z0-9~])  \S", node.args[0].value)
                if match:
                    labels.append(match.group(1))
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "choice":
            if len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq) and isinstance(node.comparators[0], ast.Constant):
                choices.append(node.comparators[0].value)
    assert labels
    assert all(count == 1 for count in Counter(labels).values())
    assert all(count == 1 for count in Counter(choices).values())
    assert set(labels) == set(choices)


def test_menu_descriptions_match_export_names_and_indicators(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "dashboard", lambda *args: None)
    monkeypatch.setattr(mod, "input", lambda prompt="": "0", raising=False)
    mod.main_menu_loop({}, ["DAY"])
    output = capsys.readouterr().out
    assert "Williams%%R" not in output
    assert "Williams %R" in output
    for name in ("master_report_<timestamp>.txt", "master_export_<timestamp>.csv", "master_report_<timestamp>.html"):
        assert name in output
