"""docs/05-reference/configuration-reference.md from config.py DEFAULTS and the comments in config/system.yaml."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import json
import re
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from _common import DOCS, ROOT, cell, header  # noqa: E402

OUT = DOCS / "05-reference" / "configuration-reference.md"
KEY = re.compile(r"^(\s*)([A-Za-z0-9_]+):(.*)$")


def yaml_comments(text: str) -> dict[str, str]:
    """Dotted key path -> the trailing comment on its line (block-style keys only)."""
    out, stack = {}, []
    for line in text.splitlines():
        m = KEY.match(line)
        if not m or line.lstrip().startswith("#"):
            continue
        indent, key, rest = len(m.group(1)), m.group(2), m.group(3)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        path = ".".join(k for _, k in stack)
        if "#" in rest:
            out[path] = rest.split("#", 1)[1].strip()
    return out


def flatten(d, prefix="", depth=0, out=None):
    out = {} if out is None else out
    for k, v in d.items():
        p = f"{prefix}{k}"
        if isinstance(v, dict) and v and depth < 2:
            flatten(v, p + ".", depth + 1, out)
        else:
            out[p] = v
    return out


def fmt(v) -> str:
    if v is None:
        return ""
    s = json.dumps(v, default=str, ensure_ascii=False) if not isinstance(v, str) else v
    return f"`{s}`" if len(s) <= 70 else f"`{s[:67]}…`"


def render() -> str:
    import yaml
    from nsealgo.config import DEFAULTS
    text = (ROOT / "config" / "system.yaml").read_text(encoding="utf-8")
    shipped = yaml.safe_load(text) or {}
    notes = yaml_comments(text)
    sections = list(dict.fromkeys(list(DEFAULTS) + list(shipped)))
    out = [header("gen_config_reference.py"), "# Configuration reference", "",
           "Settings come from `config.py` defaults, overridden by `config/system.yaml`; `${VAR}` and `${VAR:-default}`",
           "are filled from the environment (`.env`). This table lists each setting's default, the value shipped in",
           "`system.yaml`, and the explanation written next to it there.", "",
           "Sections: " + " · ".join(f"[`{s}`](#{s})" for s in sections)]
    for s in sections:
        d, y = DEFAULTS.get(s), shipped.get(s)
        out += ["", f"## {s}", ""]
        if s in notes:
            out += [notes[s], ""]
        dflat = flatten(d, s + ".") if isinstance(d, dict) else ({s: d} if d is not None else {})
        yflat = flatten(y, s + ".") if isinstance(y, dict) else ({s: y} if y is not None else {})
        keys = list(dict.fromkeys(list(dflat) + list(yflat)))
        if not keys:
            continue
        out += ["| Setting | Default | In system.yaml | Notes |", "|---|---|---|---|"]
        for k in keys:
            out.append(f"| `{k}` | {fmt(dflat.get(k))} | {fmt(yflat.get(k)) if k in yflat else ''} | {cell(notes.get(k, ''))} |")
    return "\n".join(out) + "\n"


def main() -> None:
    OUT.write_text(render(), encoding="utf-8")


if __name__ == "__main__":
    main()
