"""Regenerate every generated document, then check links. Run after changing the CLI, config, tests or modules."""
from __future__ import annotations

import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import check_links  # noqa: E402
import gen_cli_reference  # noqa: E402
import gen_config_reference  # noqa: E402
import gen_module_reference  # noqa: E402
import gen_test_catalogue  # noqa: E402

GENERATORS = [gen_cli_reference, gen_config_reference, gen_module_reference, gen_test_catalogue]

if __name__ == "__main__":
    for g in GENERATORS:
        g.main()
        print("wrote", g.OUT.relative_to(g.ROOT if hasattr(g, "ROOT") else g.OUT.parents[2]))
    bad = check_links.broken()
    print("\n".join(bad) or "all links resolve")
    sys.exit(1 if bad else 0)
