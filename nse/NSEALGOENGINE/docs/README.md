> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# nsealgo documentation

Documentation for **nsealgo 3.11**, a broker-agnostic system for automated intraday and swing trading of NSE cash-market stocks.

The documents are Markdown files kept with the code, so they change in the same commits as the behaviour they describe. Some reference documents are **generated from the code** and are marked as such. `scripts/docs/build_all.py` regenerates them, and `tests/unit/test_docs.py` fails when they are stale or a link is broken.

## Start here

| You are… | Read first | Then |
|---|---|---|
| **Business owner** | [Business case](01-business/business-case.md) | [Project charter](00-overview/project-charter.md), [Risk register](01-business/risk-register.md), [Compliance](01-business/compliance.md) |
| **End user (trader)** | [User guide](08-user/user-guide.md) | [Desk guide](08-user/desk-guide.md), [Strategy catalogue](05-reference/STRATEGY_CATALOGUE.md) |
| **Operator** | [Installation guide](07-operations/installation-guide.md) | [Runbook](07-operations/runbook.md), [Troubleshooting](07-operations/troubleshooting.md) |
| **Project manager** | [Project plan](09-project/project-plan.md) | [RACI](09-project/raci.md), [Release process](09-project/release-process.md) |
| **Architect** | [Architecture](03-architecture/architecture.md) | [Decision records](03-architecture/adr/README.md), [Data architecture](03-architecture/data-architecture.md), [Security](03-architecture/security-architecture.md) |
| **Developer** | [Developer guide](04-design/developer-guide.md) | [Module reference](04-design/module-reference.md), [Engine lifecycle](04-design/engine-lifecycle.md), [Strategy guide](04-design/strategy-developer-guide.md) |
| **Tester** | [Test strategy](06-testing/test-strategy.md) | [Test plan](06-testing/test-plan.md), [Acceptance tests](06-testing/acceptance-tests.md), [Test catalogue](06-testing/test-catalogue.md) |

## The hierarchy

| Section | Documents | Owner role |
|---|---|---|
| **00 Overview** | [Project charter](00-overview/project-charter.md) · [System overview](00-overview/system-overview.md) · [Glossary](00-overview/glossary.md) | Project manager |
| **01 Business** | [Business case](01-business/business-case.md) · [Compliance](01-business/compliance.md) · [Risk register](01-business/risk-register.md) | Business owner |
| **02 Requirements** | [Product requirements (PRD)](02-requirements/product-requirements.md) · [Software requirements (SRS)](02-requirements/srs.md) | Product owner |
| **03 Architecture** | [Architecture](03-architecture/architecture.md) · [Data architecture](03-architecture/data-architecture.md) · [Security architecture](03-architecture/security-architecture.md) · [Decision records](03-architecture/adr/README.md) · [PX13 hardware plan](03-architecture/PX13.md) · [Order rates, scalping and multiple brokers](03-architecture/MICROSTRUCTURE.md) · [Performance](03-architecture/PERFORMANCE.md) | Architect |
| **04 Design** | [Developer guide](04-design/developer-guide.md) · [Module reference](04-design/module-reference.md) · [Engine lifecycle](04-design/engine-lifecycle.md) · [Strategy developer guide](04-design/strategy-developer-guide.md) · [Broker adapter guide](04-design/broker-adapter-guide.md) · [Coding standards](04-design/coding-standards.md) · [Statistical strategy research](04-design/STRATEGY_RESEARCH.md) | Developer |
| **05 Reference** | [CLI reference](05-reference/cli-reference.md) *(generated)* · [Configuration reference](05-reference/configuration-reference.md) *(generated)* · [Monitor API](05-reference/monitor-api.md) · [Strategy catalogue](05-reference/STRATEGY_CATALOGUE.md) · [Fyers API notes](05-reference/FYERS_API_NOTES.md) | Developer |
| **06 Testing** | [Test strategy](06-testing/test-strategy.md) · [Test plan](06-testing/test-plan.md) · [Acceptance tests](06-testing/acceptance-tests.md) · [Traceability](06-testing/traceability.md) · [Test catalogue](06-testing/test-catalogue.md) *(generated)* | Tester |
| **07 Operations** | [Installation guide](07-operations/installation-guide.md) · [Windows 11 setup](07-operations/WINDOWS.md) · [Runbook](07-operations/runbook.md) · [Monitoring and alerts](07-operations/monitoring-and-alerts.md) · [Backup and recovery](07-operations/backup-and-recovery.md) · [Troubleshooting](07-operations/troubleshooting.md) | Operator |
| **08 User** | [User guide](08-user/user-guide.md) · [Desk guide](08-user/desk-guide.md) | End user |
| **09 Project** | [Project plan](09-project/project-plan.md) · [RACI](09-project/raci.md) · [Release process](09-project/release-process.md) · [Documentation maintenance](09-project/documentation-maintenance.md) · [Review history](09-project/history/REVIEW_RESPONSE.md) | Project manager |

The [CHANGELOG](../CHANGELOG.md) is the release-by-release record. The top-level [README](../README.md) is the quick start.

## Conventions

- **Requirement IDs:** `FR-…` and `NFR-…` in the SRS, referenced from the traceability matrix and tests.
- **Decision records:** `ADR-NNNN`, never edited after acceptance; a later ADR supersedes an earlier one.
- **Times** are IST unless stated. **Money** is INR, written with lakh/crore grouping in user-facing text.
- **Generated files** carry a header saying so; change the generator, not the file.
- **Maintenance:** every document has an owner role and review triggers, listed in [Documentation maintenance](09-project/documentation-maintenance.md).
