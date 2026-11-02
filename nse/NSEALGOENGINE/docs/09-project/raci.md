> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# RACI

One person holds every role today. Keeping the roles distinct is still useful: it says which hat to wear for each decision, and it lets the work be shared later.

**R** responsible · **A** accountable · **C** consulted · **I** informed

| Activity | Owner / trader | Product owner | Architect | Developer | Tester | Operator | PM |
|---|---|---|---|---|---|---|---|
| Decide what to build (PRD) | C | **A/R** | C | C | I | I | C |
| Architecture decisions (ADRs) | I | C | **A/R** | C | I | I | I |
| Implement changes | I | I | C | **A/R** | C | I | I |
| Test and sign off a release | **A** | C | I | C | **R** | C | I |
| Release and deploy | **A** | I | I | **R** | C | C | C |
| Daily operation | **A** | I | I | I | I | **R** | I |
| Promote a strategy to live | **A/R** | C | I | I | C | I | I |
| Kill switch and incidents | **A** | I | I | C | I | **R** | I |
| Compliance review | **A/R** | C | C | C | I | I | I |
| Keep documentation current | **A** | C | R (architecture) | R (design, reference) | R (testing) | R (operations) | **R** (index, plan) |
