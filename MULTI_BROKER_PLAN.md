# Multi-Broker, Multi-Account, Multi-Connection Feed Plan

**Status:** approved plan (owner decisions 2026-11-02)
**Repo:** `sandebb/CBIL` · local `D:\CBILTrader\OpenAlgo-Desktop`
**Reference code:** `nse/` (imported 2026-11-02 from `sandebb/NSEUIENGINE`
branch `refactor/monorepo-cleanup`, commit `f98338b`)

## 1. Owner decisions (2026-11-02)

| # | Decision |
|---|---|
| D1 | **Scope:** continuous multi-connection feeds + multi-broker, multi-account order placement. Automation surfaces (strategy/scalping/flow/agent) come later. |
| D2 | **Feed grid:** Upstox **5 sockets × 50 symbols at depth-30** (the `full_d30` per-connection limit, 250 symbols); Fyers **3 sockets × 5 symbols TBT** (15 symbols, 50-level depth per `nse/Fyerstbt/fyers_tbt_streamer.py`); Fyers **1-minute feed**. |
| D3 | **Orders:** place orders via multiple brokers **and multiple accounts** (multiple key sets per broker). This retires the "one broker / one account at a time" rule. |
| D4 | **Sandbox:** one shared sandbox engine for all brokers/accounts. |
| D5 | **Books & PnL:** aggregated views with broker + account columns and combined totals. |
| D6 | **Cadence:** land in phases, push to `sandebb/CBIL` after each phase is green. |
| D7 | **Performance:** target machine is 32 cores @ 5 GHz — use multithreading as much as possible; the tick path must not serialize on one core or one lock. |
| D8 | **NSE code:** `NSEUIENGINE` main branch is not to be touched; the `refactor/monorepo-cleanup` branch is copied into this repo under `nse/` (done, commit `710bc4d2`). Python stays reference/sidecar; the feed grid is implemented in Rust. |

## 2. What exists today (single-session architecture)

- `AppState.broker_session: RwLock<Option<BrokerSession>>` — **one** session
  (`state.rs`); `BrokerSession { broker_id, auth_token, feed_token, user_id,
  user_name, authenticated_at }`.
- `ServerConfig.active_broker: Option<String>` — one default broker
  (`config.rs`, settings table).
- `BrokerRuntime` (`services/broker_runtime.rs`) — one active broker:
  `order_ws` + `depth_ws` (one `WebSocketManager` each), one `JoinSet` of
  owned tasks, `activate()` tears down the previous session first,
  per-session generation counter for order updates (EV-02).
- `BrokerRegistry` (`ctx.brokers`) — **already a map** of all 38 adapters
  keyed by broker id.
- Stored credentials: one key set per broker (`db/sqlite/credentials.rs`,
  `catalog::login_fields`); stored session rows per broker
  (`db/sqlite/auth.rs::StoredBrokerSession`).
- 8765 feed server (`feed/`) — one source (`ctx.bridge` over the broker
  managers); web protocol unchanged (no broker dimension — stays that way).
- `services/live_owner.rs` — obligations already record broker + account;
  `services/session_account.rs` (NEW-TR-01) resolves the session's account.
- Upstox streaming (`brokers/upstox/streaming.rs`) — modes `ltpc` (5000
  keys), `full`, `full_d30` (**50 keys/connection**); combined limit 1500;
  protobuf decode (prost).
- Fyers streaming (`brokers/fyers/streaming/tbt.rs`) — TBT support exists;
  the NSE streamer (`nse/Fyerstbt/fyers_tbt_streamer.py`) is the working
  reference for 3 sockets × 5 symbols in **depth mode, 50 levels**,
  `tbtws` URL lookup (`/indus/home/tbtws`), `APPID:TOKEN` authorization
  header, `{"type":1, mode:"depth"}` subscribe, `ping` every 10 s, protobuf
  snapshot + positional diffs.
- Sandbox (`sandbox/`) — one engine, own `sandbox.db`, broker-agnostic.
- `~60` call sites of `get_broker_session()` across services, feed, strategy,
  scalping, flow, agent, API routes, tests.

## 3. Target architecture

```
                        ┌────────────────────────────────────────────┐
                        │              App (Tauri window)            │
                        │  broker/account grid · feed grid · books   │
                        └───────┬────────────────────────────────────┘
                                │ HTTP /api/v1 (default broker/account,
                                │  optional X-OpenAlgo-Broker + account)
 ┌──────────────┐   Socket.IO   ▼
 │ external     │ ──────────► 8765 feed server (web protocol, unchanged:
 │ SDK /        │            union of all live channels, deduped)
 │ TradingView  │            │
 └──────────────┘            ▼
                     FeedGrid (new)
        ┌───────────┬───────────┬───────────┬──────────┐
        │ Upstox    │ Upstox    │ Fyers TBT │ Fyers 1m │  ... up to
        │ d30 #1    │ d30 #2..5 │ #1..#3    │          │  grid limits
        │ 50 syms   │           │ 5 syms    │          │
        └─────┬─────┴─────┬─────┴─────┬─────┴─────┬────┘
              │ one tokio task + decode per channel (D7)
              ▼
        SessionStore (new): map (broker, account) -> BrokerSession
              │ per-session runtime: order feed, master contract,
              │ order updates (generation per session)
              ▼
        OrderRouter (new): (broker, account) -> adapter instance
              │ live  -> broker REST      analyzer mode -> Sandbox (D4)
              ▼
        Books: order/trade/position/holdings per (broker, account),
        aggregated views with columns + combined totals (D5)
```

### 3.1 Account model (D3)

- A **credential set** = broker + account label + keys (existing slots from
  `catalog::login_fields`). The `credentials` table gains an account
  dimension: `(broker_id, account_id)` unique, `account_id` = trader-chosen
  label (e.g. "A", "B") — the broker's own account id (when login names one,
  `session_account` NEW-TR-01) is stored alongside.
- A **session** = one credential set signed in. N sessions live at once.
- `BrokerSession` gains `account_id`; `ServerConfig.active_broker` becomes
  `default_broker` + `default_account` (the `/api/v1` default target;
  existing clients keep working unchanged).
- Per-session lifecycle stays as today (activate on login/resume; revoke on
  logout; daily boundary per broker; crypto exception) but **independent**:
  signing in to broker B never ends broker A's session; the
  `broker_switch` guard and "switch asks first" rule are retired for
  multi-session (a session still cannot end while *its own* live
  obligations depend on it — that check becomes per-session).
- Vault/security unchanged: keys still Argon2-wrapped in `key_vault`,
  AES-256-GCM per row with AAD binding — each account row is its own
  ciphertext. Logout/quit still wipes all sessions' keys.

### 3.2 Feed grid (D2, D7)

A **channel** is one broker socket with a fixed mode and symbol set:

| Grid slot | Broker | Mode | Symbols | Source of limit |
|---|---|---|---|---|
| `upstox-d30-1..5` | Upstox | `full_d30` | 50 each (250 total) | 50 keys/connection in `upstox/streaming.rs`; Upstox's own per-connection cap |
| `fyers-tbt-1..3` | Fyers | TBT depth (50 levels) | 5 each (15 total) | Fyers TBT: 5 symbols/socket, 3 sockets (per `nse/Fyerstbt` working reference) |
| `fyers-1m` | Fyers | 1-minute candles | configured set | Fyers candle feed (verify mode in Phase 2; fallback: 1-min history poller) |

Rules:

- Grid = stored config: per-slot broker, account, symbol list, enabled.
  Trader edits in the UI; validated against the slot's limit (50 / 5).
- Each channel runs in its **own tokio task** (connect, handshake,
  subscribe, ping loop, decode, publish). Channels are independent: one
  drop/reconnect never touches another. Bounded channels between decode and
  the market-data manager; `Lagged` handled (repo rule).
- **Parallel decode (D7):** protobuf decode (prost) and book-state
  application run on the blocking pool / dedicated worker threads so the
  8+ channels decode concurrently across cores; the tick path holds no
  global lock — per-channel book state, merged into the shared
  `MarketDataManager` with sharded writes (or lock-free per-symbol slots).
- 8765 feed server serves the **union** of channels; when two channels carry
  the same symbol, the grid config names the preferred slot (first match
  wins) — explicit, user-set, no hidden preference.
- Per-channel health (state, last tick age, drop count, reconnect backoff)
  is exposed on the Health page and the feed grid UI.
- Sandbox/analyzer mode is unaffected by the grid (data is data); orders
  are what the sandbox intercepts.

### 3.3 Order placement (D3)

- Every order request names its destination: `(broker, account)`.
  - **UI:** order dock / dashboard pick destination (default = configured
    default; explicit picker per order, remembered per page).
  - **API:** `/api/v1` unchanged for clients that send nothing (they get the
    default broker/account, exactly as today); new optional header
    `X-OpenAlgo-Broker` + `X-OpenAlgo-Account` select the destination.
    This is an additive desktop extension; the web contract stays intact.
- `OrderRouter` resolves destination → adapter instance for that
  (broker, account) session. The three-outcome model (accepted / refused /
  uncertain), claim-under-lock, `live_owner` accounting, and the order
  reconciler all apply **per destination** — obligations already record
  broker + account, so the machinery extends rather than rewrites.
- **Sandbox (D4):** one engine; in analyzer mode every order from any
  broker/account goes there; sandbox books gain a destination column so the
  trader can see which broker/account each simulated order would have used.
- Books & PnL (D5): per-(broker, account) rows; pages show a broker +
  account column and combined totals; PnL summed across accounts with
  per-account breakdown.

### 3.4 UI

- `/broker` card grid: **multiple Active** badges; each card shows account
  label(s), session state, sign-in/out per account.
- New **Feed Grid** page (and a compact widget on the Health page): slots,
  symbols, state, tick age, reconnects; enable/disable per slot; symbol
  lists per slot.
- New **Accounts** page (or tab on Broker Configuration): add/remove key
  sets per broker, label them, set default broker/account.
- AccountControls (navbar + terminal toolbar): broker/account switcher
  reflecting the default target.
- Books/PnL pages: broker + account columns, aggregate rows.

### 3.5 Out of scope for this plan (follow-up plan after v1)

- Strategy module / scalping / Flow / Agent runs trading across brokers
  (each run would bind a destination set on top of the Phase 1–3
  foundations — `live_owner` already carries broker + account).
- Running the Python engines (backtest/ML/strategies in `nse/`) as an app
  sidecar; they remain reference code (D8) until a concrete need is scoped.

## 4. Phases (D6 — each phase: tests in the same commit, full gates, push when green)

Gates per push: `cargo fmt --check` · `cargo clippy --all-targets --locked
-- -D warnings` · `cargo test --locked` (nextest CI profile) · `npx tsc -b`
· vitest · biome · gitleaks · `python3 scripts/ci/code_health.py`.

| Phase | Deliverable | Acceptance |
|---|---|---|
| **1. Multi-session core** | account dimension in credentials + sessions; `SessionStore` map; per-session runtimes; independent sign-in/resume/revoke/boundary; default broker/account config; `X-OpenAlgo-Broker`/`X-OpenAlgo-Account` header plumbing (no behavior change for single-session clients); per-session switch guard | two brokers signed in at once; logout of one leaves the other trading; `/api/v1` against default unchanged (contract tests green); all existing session tests pass re-targeted |
| **2. Feed grid** | channel manager (Upstox 5×d30, Fyers 3×TBT depth + 1-min slot); per-channel tasks + parallel decode; grid config + validation; 8765 union + dedup by grid preference; per-channel health + reconnect | 250 d30 + 15 TBT channels live simultaneously in a soak run; one channel's drop does not affect the others; tick path shows no global-lock stall on the 32-core box (measured); 8765 contract tests green |
| **3. Multi-destination orders + UI** | OrderRouter; destination in order dock/dashboard; books/PnL columns + aggregates; broker/account grid UI; feed grid UI; accounts page | manual orders land on the chosen (broker, account) incl. sandbox; aggregated books correct for 2 brokers × 2 accounts (tested); UI flows pass vitest + playwright |
| **4. (follow-up)** | strategy/scalping/flow/agent destination binding; optional NSE sidecar | separate plan |

## 5. Performance design (D7 — 32 cores @ 5 GHz)

1. **One task per channel**, never a shared reader loop: 8+ independent
   sockets decode concurrently. Tokio's worker threads already scale to
   core count; keep the tick path off the runtime's I/O threads.
2. **Decode on the blocking pool:** prost decode + book application per
   message batch (bounded batch, e.g. per ping cycle) — CPU work off the
   async runtime, parallel across channels. Measure: `runtime_busy` per
   channel; a channel that holds a runtime thread > 200 ms is a defect.
3. **No global lock on the hot path:** per-channel book state; merge into
   the shared market-data manager via per-symbol slots (sharded by symbol
   hash) instead of one mutex; the 8765 publisher reads per-symbol slots.
4. **Bounded queues everywhere** between decode and publish; overflow drops
   oldest with a counted + alerted `Lagged` (repo rule), never grows.
5. **Builds/tests:** `CARGO_BUILD_JOBS=16` for local iteration; nextest
   already runs one process per test — raise its default test threads to
   exploit the cores for the Phase 1–3 suites.
6. **Soak test for the grid (Phase 2 gate):** 8 channels × full symbol
   sets × 2+ hours on this box; report RSS growth (must be flat — repo
   leak rules), tick latency percentiles, and zero cross-channel stalls.

## 6. Reference pointers (in `nse/`)

- `nse/Fyerstbt/fyers_tbt_streamer.py` — the working Fyers TBT depth
  protocol reference (3×5, 50 levels, ping 10 s, snapshot + diffs).
- `nse/NSEALGOENGINE/src/nsealgo/brokers/{multi.py, routing.py, transport.py, registry.py, base.py, upstox.py, fyers.py}` —
  the owner's existing multi-broker routing + multi-account model to mirror.
- `nse/NSEALGOENGINE/src/nsealgo/data/` — minute archive, TBT recorder
  patterns (1-minute feed).
- `nse/MASTER_CONTEXT.md` — full NSE system context (data flow, timelines,
  broker roles: Shoonya orders / Fyers data).
