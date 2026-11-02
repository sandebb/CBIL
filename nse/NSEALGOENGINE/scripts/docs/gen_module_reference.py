"""docs/04-design/module-reference.md from module docstrings and public classes under src/nsealgo."""
from __future__ import annotations

import ast
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from _common import DOCS, ROOT, cell, header  # noqa: E402

OUT = DOCS / "04-design" / "module-reference.md"
PKG = ROOT / "src" / "nsealgo"


def render() -> str:
    out = [header("gen_module_reference.py"), "# Module reference", "",
           "Every module under `src/nsealgo`, with the first paragraph of its docstring and its public classes.",
           "To change a description, edit the module's docstring and regenerate."]
    groups = [("nsealgo", sorted(PKG.glob("*.py")))]
    groups += [(d.name, sorted(d.glob("*.py"))) for d in sorted(p for p in PKG.iterdir() if p.is_dir() and p.name != "__pycache__")]
    for name, files in groups:
        out += ["", f"## {name}", "", "| Module | Purpose | Public classes |", "|---|---|---|"]
        for f in files:
            tree = ast.parse(f.read_text(encoding="utf-8"))
            doc = (ast.get_docstring(tree) or "").strip().split("\n\n")[0].replace("\n", " ")
            classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef) and not n.name.startswith("_")]
            rel = f.relative_to(ROOT / "src").as_posix()
            out.append(f"| [`{rel}`](../../src/{rel}) | {cell(doc)} | {', '.join(f'`{c}`' for c in classes)} |")
    return "\n".join(out) + "\n"


def main() -> None:
    OUT.write_text(render(), encoding="utf-8")


if __name__ == "__main__":
    main()
