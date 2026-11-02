"""docs/06-testing/test-catalogue.md from the test sources (module docstrings and test names)."""
from __future__ import annotations

import ast
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from _common import DOCS, ROOT, cell, header  # noqa: E402

OUT = DOCS / "06-testing" / "test-catalogue.md"


def render() -> str:
    files = sorted((ROOT / "tests").rglob("test_*.py"))
    out, total, rows = [header("gen_test_catalogue.py"), "# Test catalogue", ""], 0, []
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        tests = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
        total += len(tests)
        rel = f.relative_to(ROOT).as_posix()
        doc = (ast.get_docstring(tree) or "").strip().split("\n\n")[0].replace("\n", " ")
        rows.append((rel, doc, tests))
    out += [f"{total} test functions in {len(files)} files (pytest counts parametrised functions once per case). "
            "Each test's name states the behaviour it checks.", "",
            "| File | Tests | What it covers |", "|---|---|---|"]
    for rel, doc, tests in rows:
        out.append(f"| [`{rel}`](../../{rel}) | {len(tests)} | {cell(doc)} |")
    for rel, doc, tests in rows:
        out += ["", f"## {rel}", ""]
        if doc:
            out += [doc, ""]
        out += [f"- {t[5:].replace('_', ' ')}" for t in tests]
    return "\n".join(out) + "\n"


def main() -> None:
    OUT.write_text(render(), encoding="utf-8")


if __name__ == "__main__":
    main()
