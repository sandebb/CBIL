> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Risk register

Scales: **likelihood** L (low) / M / H; **impact** L / M / H. Review at every release and after any incident.

| ID | Risk | Cat. | L | I | Mitigation | Owner | Status |
|---|---|---|---|---|---|---|---|
| R01 | Strategies fail live after costs (overfitting, decay) | Business | H | H | Promotion gates on live data; walk-forward ±20%; small live size; kill switch | Owner | Open (by nature) |
| R02 | Order sent but unacknowledged, then untracked (timeouts, Fyers 201/−99) | Technical | M | H | `OrderUncertain` keeps it UNKNOWN until the order book confirms it by tag; duplicate-safe failover | Developer | Mitigated (3.6.1, 3.7.0) |
| R03 | Broker rate limit exceeded → account blocked for the day (Fyers) | Operational | M | H | Global call budget at 90% of the documented limits; per-broker order budgets | Developer | Mitigated (3.6.1) |
| R04 | Orders leave from an unregistered IP → rejected, or a compliance breach | Compliance | M | H | Fail-closed egress check through the static-IP proxy before login; `netcheck` | Operator | Mitigated (3.1, 3.7) |
| R05 | Runaway orders from a bug | Technical | L | H | Token bucket ≤10/s per broker; risk limits on entries; self-trade guard; kill switch (CLI and desk) | Developer | Mitigated |
| R06 | Market-data outage or a stale feed mid-session | Operational | M | M | Feed mesh failover; stale detection; data-age warnings in the desk | Operator | Mitigated |
| R07 | Laptop sleep, power or internet loss during market hours | Operational | M | H | Keep-awake and power settings (`system.py`); scheduled start; positions re-synced at restart; the journal reloads the day | Operator | Partly mitigated: a UPS is recommended |
| R08 | Credentials or tokens leaked | Security | L | H | `.env` outside version control; token cache mode 0600; monitor on 127.0.0.1 with a token ([Security](../03-architecture/security-architecture.md)) | Operator | Mitigated |
| R09 | Broker API or SDK changes break an adapter | Technical | M | M | Adapter contract tests; `smoke-test`; vendor notes with page references ([Fyers notes](../05-reference/FYERS_API_NOTES.md)) | Developer | Open |
| R10 | Regulation changes (thresholds, tagging, static-IP rules) | Compliance | M | M | [Compliance](compliance.md) review triggers; limits are configuration | Owner | Open |
| R11 | Paid data (Upstox Plus, TBT) bought without benefit | Business | M | L | `nsealgo book-study` on recordings before subscribing | Owner | Open |
| R12 | Upstox enforces 50 deep instruments per user, not 250 | Technical | M | L | Detected automatically; capacity drops to 50 and focus re-ranks | Developer | Mitigated (3.6) |
| R13 | GPU path behaves differently on the real hardware | Technical | M | L | Per-kernel parity check against NumPy before use; any error falls back to the CPU; PyTorch test on the PX13 | Developer | Mitigated (3.3) |
| R14 | Single-person dependency (owner is developer, tester and operator) | Project | H | M | This documentation set; runbook; automated tests; ADRs | Project manager | Open |
| R15 | Gaps, circuit limits or auction-only sessions hurt fills | Market | M | M | Circuit-aware paper exchange; marketable-limit protection; exit chasing; CAS handling | Developer | Mitigated |
| R16 | Documentation drifts from the code | Project | M | M | Generated references; `test_docs.py` checks links and freshness; review triggers | Project manager | Mitigated (3.11) |
| R17 | Config edit silently disables something (e.g. the 3.5 Fyers entry) | Technical | L | H | A test loads the shipped config; `nsealgo doctor` | Developer | Mitigated (3.7.0) |
| R18 | Using family members' accounts for more data | Compliance | L | H | Declined by design; documented ([ADR-0011](../03-architecture/adr/ADR-0011-no-account-pooling.md)) | Owner | Closed |
