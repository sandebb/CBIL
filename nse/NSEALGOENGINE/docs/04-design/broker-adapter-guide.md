> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Broker adapter guide

## The contract (`brokers/base.BrokerAdapter`)

| Method | Must |
|---|---|
| `login(interactive)` | Authenticate; cache the day's token with `save_session` (mode 0600) |
| `_fetch_master()` | Return the symbol master as a DataFrame with the columns in `brokers/symbols.COLS`; keys are neutral (`NSE:SBIN`) |
| `get_quotes(keys)` | Return `Quote`s; batch by `quote_batch` |
| `get_history(key, interval, start, end)` | Return OHLCV via `candles_df`; respect the broker's per-request span |
| `place_order(req, client_id)` | Send the order with `client_id` as the broker tag; return the broker ID |
| `modify_order(broker_id, …)`, `cancel_order(broker_id)` | As named |
| `get_orders()` | Return `BrokerOrder`s with the **tag as you sent it** (strip broker prefixes) |
| `get_positions()` | Return `BrokerPosition`s; map products to MIS / CNC |

**Optional:**
- **Streaming:** `stream` / `stop_stream` (set `supports_streaming`, `stream_capacity`).
- **Funds and auctions:** `get_funds`, `auction_snapshot`.
- **Deep books:** `set_depth_focus`, if the broker has deep books.

## Rules

1. **Statuses:** map every broker status into `Status` through `STATUS_MAP`. Unknown states must not become FILLED or REJECTED.
2. **Rejection vs uncertainty:** raise `BrokerError` for a refusal (final). Raise `OrderUncertain` when the order may have reached the broker: timeouts, no acknowledgement, or SDK-swallowed errors ([ADR-0013](../03-architecture/adr/ADR-0013-uncertain-orders.md)).
3. **Rate limits:** set `min_intervals` per endpoint and `api_limits = (per_second, per_window)` for account-wide limits. Call `self.throttle(kind)` before every API call, including second calls inside one method.
4. **Static IP:** route REST traffic through `transport.BrokerTransport`:
   - `route_sdk(module, transport)` for SDKs that call `requests.post` directly;
   - `transport.as_session()` for SDKs that keep their own session.
   
   Call `transport.verify()` before login.
5. **Tags:** respect the broker's tag rules; OMS client IDs are uppercase letters and digits, ≤20 characters.
6. **Vendor notes:** record what you rely on from the vendor docs, with page references, like [Fyers API notes](../05-reference/FYERS_API_NOTES.md).

## Registering

- **Registry:** add the class to `brokers/registry.py`, with its install command and feed info (free data, stream capacity, login style).
- **Install extra:** add an optional extra in `pyproject.toml`.
- **Config:** add a commented `broker.brokers.<name>` entry in `config/system.yaml`.

## Testing

- **Unit tests:** use a fake SDK client (see `tests/unit/test_adapters.py`, `test_fyers_docs.py`). Cover master parsing, order mapping, statuses, tags, uncertain outcomes and throttling.
- **Transport:** the tests in `test_transport.py` run against a real local server and proxy.
- **Real account:** `nsealgo smoke-test <broker>` runs an end-to-end check against your account; read its prompts.
