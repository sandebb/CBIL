"""docs/05-reference/cli-reference.md from the Typer app in src/nsealgo/cli.py."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from _common import DOCS, cell, header  # noqa: E402

OUT = DOCS / "05-reference" / "cli-reference.md"


def _default(p) -> str:
    d = p.default
    if d is None or d is False or callable(d) or (isinstance(d, (list, tuple)) and not d):
        return ""
    return f"`{d}`"


def render() -> str:
    import typer.main
    from nsealgo.cli import app
    cmd = typer.main.get_command(app)
    items = sorted(cmd.commands.items())
    out = [header("gen_cli_reference.py"), "# CLI reference", "",
           f"`nsealgo` has {len(items)} commands. Run `nsealgo <command> --help` for the same text.", "",
           "| Command | Purpose |", "|---|---|"]
    for name, c in items:
        out.append(f"| [`{name}`](#{name}) | {cell((c.help or '').strip().splitlines()[0] if c.help else '')} |")
    for name, c in items:
        out += ["", f"## {name}", "", (c.help or "").strip(), ""]
        kind = lambda p: getattr(p, "param_type_name", "")              # noqa: E731  (Typer may bundle its own click)
        args = [p for p in c.params if kind(p) == "argument"]
        opts = [p for p in c.params if kind(p) == "option" and p.name != "help"]
        usage = " ".join([f"nsealgo {name}"] + [f"<{a.name}>" for a in args] + (["[options]"] if opts else []))
        out += ["```", usage, "```"]
        if args or opts:
            out += ["", "| Argument / option | Default | Meaning |", "|---|---|---|"]
            for a in args:
                out.append(f"| `{a.name}` | {_default(a)} | {cell(getattr(a, 'help', '') or '')} |")
            for o in opts:
                names = ", ".join(f"`{x}`" for x in o.opts + o.secondary_opts)
                out.append(f"| {names} | {_default(o)} | {cell(o.help or '')} |")
    return "\n".join(out) + "\n"


def main() -> None:
    OUT.write_text(render(), encoding="utf-8")


if __name__ == "__main__":
    main()
