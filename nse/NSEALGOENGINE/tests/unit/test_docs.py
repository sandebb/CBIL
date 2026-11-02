"""Documentation stays current: generated references match the code, every relative link resolves, and every
document is listed in the documentation index."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "docs"


def load(name):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name", ["gen_cli_reference", "gen_config_reference", "gen_module_reference",
                                  "gen_test_catalogue"])
def test_generated_docs_are_current(name):
    g = load(name)
    assert g.OUT.read_text(encoding="utf-8") == g.render(), \
        f"{g.OUT.relative_to(ROOT)} is stale: run python scripts/docs/build_all.py"


def test_every_relative_link_resolves():
    assert load("check_links").broken() == []


def test_every_document_is_listed_in_the_index():
    docs = ROOT / "docs"
    index = (docs / "README.md").read_text(encoding="utf-8")
    missing = [p.relative_to(docs).as_posix() for p in docs.rglob("*.md")
               if p.name != "README.md" and "/adr/" not in f"/{p.relative_to(docs).as_posix()}"
               and p.relative_to(docs).as_posix() not in index]
    assert missing == [], f"add to docs/README.md: {missing}"
