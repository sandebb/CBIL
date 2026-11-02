> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Security architecture

## Assets

Trading capital; the ability to place orders; broker credentials and daily tokens; the static-IP proxy credentials; the strategy code and research; records.

## Threats and controls

| Threat | Control |
|---|---|
| Credentials leak from the repo or logs | Secrets live only in `.env` (not committed), referenced as `${VAR}`. `netcheck` redacts proxy credentials. Tokens are cached with mode 0600 |
| A stolen token used from elsewhere | The broker accepts orders only from the registered static IP. nsealgo also checks its own egress IP before login |
| Orders from the wrong network (proxy down or misconfigured) | **Fail closed:** no login and no orders unless the egress IP equals `static_ip` ([ADR-0006](adr/ADR-0006-static-ip-fail-closed.md)) |
| System proxy variables hijack order traffic | The transport sets `trust_env = False`; routing is explicit per broker |
| Another machine reads the session's data | The monitor binds to 127.0.0.1 only |
| Another local process reads it or triggers the kill switch | A per-session random token in `monitor.json`, inside the user's run folder; constant-time comparison |
| The GUI interferes with trading | The desk is a separate process with read-only data. Its only action is the kill switch, which can only stop trading |
| Runaway or duplicate orders | Token-bucket order budgets; uncertain orders confirmed by tag; self-trade guard; kill switch |
| An accidentally enabled broker trades | Dormant brokers (`orders: false`) are refused on every path |
| Supply chain (broker SDKs) | Optional extras per broker; SDK behaviour checked against vendor docs ([Fyers notes](../05-reference/FYERS_API_NOTES.md)); the SDK's HTTP is routed through nsealgo's own transport where possible |
| TLS interception | HTTPS to brokers with certificate verification; the proxy only tunnels (CONNECT) |
| Repudiation, or "what happened?" | Journal, `state.db` events, logs, reports |

## Windows hardening (PX13)

- **BitLocker** on the system drive.
- **Standard user account** for daily use.
- **Defender on:** a trusted Dev Drive gives fast scans without exclusions.
- **Windows Update** outside market hours.
- **Restricted `.env`:** the owner only (`icacls`).

Details: [Windows 11 setup](../07-operations/WINDOWS.md).

## Reviews

Review this document when adding a broker, a network endpoint, a desk action, or a new secret.
