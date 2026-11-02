> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Documentation maintenance

## Owners and review triggers

| Documents | Owner role | Review when |
|---|---|---|
| 00 Overview | Project manager | Scope or capabilities change; each MINOR or MAJOR release |
| 01 Business | Business owner | Costs, gates or regulation change; any incident |
| 02 Requirements | Product owner | A feature is added, changed or removed (keep IDs stable) |
| 03 Architecture and ADRs | Architect | New component, process, store, endpoint or decision |
| 04 Design | Developer | Public APIs (Strategy, Context, BrokerAdapter), lifecycle or standards change |
| 05 Reference (generated) | Developer | Regenerate on any CLI, config, module or test change |
| 05 Reference (hand-written) | Developer | Monitor API or vendor behaviour changes |
| 06 Testing | Tester | New tests or test levels; requirement changes (traceability) |
| 07 Operations | Operator | Install steps, schedule, incident learnings |
| 08 User | Product owner | Anything a user sees: commands, desk, settings |
| 09 Project | Project manager | Each release (plan); process changes |

## Change-type checklist

| If you change… | Update |
|---|---|
| A CLI command | Docstring; regenerate the CLI reference; user/ops docs that mention it |
| A config setting | `config.py` default plus a `system.yaml` comment; regenerate the config reference |
| A strategy | Strategy catalogue; traceability if tests change |
| A broker adapter | Broker adapter guide (if the contract changed); compliance (limits); vendor notes |
| The desk | Desk guide plus screenshots in `docs/assets/`; Monitor API if endpoints changed |
| Safety behaviour | Compliance, risk register, runbook, an ADR |
| Anything | CHANGELOG |

## Generated documents

`python scripts/docs/build_all.py` regenerates:
- the [CLI reference](../05-reference/cli-reference.md);
- the [Configuration reference](../05-reference/configuration-reference.md);
- the [Module reference](../04-design/module-reference.md);
- the [Test catalogue](../06-testing/test-catalogue.md).

It also checks every relative link. `tests/unit/test_docs.py` fails if a generated document is stale or a link is broken, so the test suite enforces this.

## Style

- **Plain language:** short sentences; tables for comparisons; examples that work as written.
- **Units:** IST for times; INR for money, with lakh/crore grouping in user-facing text.
- **Links:** relative, and checked by the link checker.
- **Sources:** cite them (papers, vendor documentation pages) where the docs make factual claims about outside systems.

## Adding a document

Put it in the right section, give it an owner row above, link it from [docs/README.md](../README.md), and run the link checker.
