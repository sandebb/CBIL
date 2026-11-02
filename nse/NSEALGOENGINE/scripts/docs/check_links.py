"""Find broken relative links in the documentation (README, CHANGELOG and everything under docs/)."""
from __future__ import annotations

import re
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from _common import DOCS, ROOT  # noqa: E402

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def broken() -> list[str]:
    files = [ROOT / "README.md", ROOT / "CHANGELOG.md"] + sorted(DOCS.rglob("*.md"))
    bad = []
    for f in files:
        text = re.sub(r"```.*?```", "", f.read_text(encoding="utf-8"), flags=re.S)   # ignore code blocks
        for target in LINK.findall(text):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path = target.split("#", 1)[0]
            if path and not (f.parent / path).resolve().exists():
                bad.append(f"{f.relative_to(ROOT).as_posix()}: {target}")
    return bad


if __name__ == "__main__":
    problems = broken()
    print("\n".join(problems) or "all links resolve")
    sys.exit(1 if problems else 0)
