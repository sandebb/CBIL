# Phase 1 runtime — SessionStore & multi-session design

Status: **design, ready to implement.** DB layer + config slice are done and
pushed (commits `b8765620`, `df2f3929`); this document is the remaining
Phase 1 work: "two brokers signed in at once; logout of one leaves the
other trading; `/api/v1` against the default unchanged"
(`MULTI_BROKER_PLAN.md`, Phase 1 row).

## 1. What is done (do not redo)

- **DB:** migration `102_multi_account` — `auth` and `broker_credentials`
  key on `(broker_id, account_id)`; legacy rows are account `default` and
  keep the v1 AAD binding (`Aad::for_account` in
  `src/security/crypto.rs`, `DEFAULT_ACCOUNT = "default"`). `settings` has
  `active_account`. `credentials::` and `auth::` take the account as an
  explicit argument; `list_configured` returns `(broker, account)` pairs;
  new `credentials::accounts_for_broker`, `credentials::delete_account`,
  `auth::delete_account`, `auth::list_active`. Key-vault witness is
  account-aware.
- **Config:** `ServerConfig.default_account` (+ `ServerConfigUpdate`)
  round-trips `settings.active_account`. `None` = legacy single account.
- **Services:** all existing single-session paths pass `DEFAULT_ACCOUNT`;
  the resume path loads credentials for the stored session's own account.
- **Toolchain (this machine):** rustup default is now
  `1.99.1-x86_64-pc-windows-msvc`, with a directory override for this repo
  (`rustup override list`); MSVC linker at
  `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64`
  (must be on PATH for cargo); `cargo-nextest 0.9.111` + `gitleaks 8.30.1`
  in `%USERPROFILE%\.cargo\bin`; node in `C:\Program Files\nodejs`.
- **Gates (run from `src-tauri`, PATH as above):**
  `cargo fmt --check` · `cargo clippy --all-targets --locked -- -D warnings`
  · `cargo nextest run --locked --all-targets --config-file ../.github/nextest.toml --profile ci`
  · `cargo test --locked --doc` · `npx tsc -b` · `npx biome check`
  · `gitleaks detect --source . --no-git --config .github/gitleaks.toml`
  (findings in `src-tauri/target/` are gitignored build artifacts — clean
  source) · `python scripts/ci/code_health.py`.
  Last full run: **5118 passed / 15 skipped / 0 failed** (2026-11-02).

## 2. Today's single-session architecture (the facts that bind the design)

Read these before touching anything:

- `state.rs` — `AppState.broker_session: RwLock<Option<BrokerSession>>`;
  `get_broker_session()` (77 call sites) / `set_broker_session()` (23).
  Also global per-broker state: `symbols: SymbolResolver` (ONE master
  contract in memory; owner tracked in `symtoken.owner`),
  `bridge: BrokerBridge` (depth capability + deep levels, ONE),
  `websocket: Arc<WebSocketManager>` (the 8765 market-data server, ONE
  connected feed), `runtime: BrokerRuntime` (ONE: task JoinSet + order_ws +
  depth_ws + `active` broker + `started` time), `bus` (event bus with a
  session `generation` counter — `begin_session()` invalidates earlier
  order updates, EV-02), `revoke_pending` (SEC-05b marker),
  `session_switch` lock, `switch_guard`.
- `services/broker_runtime.rs` (331 lines) — `activate(ctx, session)` tears
  down the previous session first (`teardown_locked`), then starts:
  master contract (`master_contract_service::ensure` → writes the GLOBAL
  `ctx.symbols`), bridge depth capability, market feed onto
  `ctx.websocket`, depth feed onto `runtime.depth_ws`, order feed onto
  `runtime.order_ws` or a relay task. `BrokerRuntime` is already an
  independent instance (`BrokerRuntime::new()`), but it reads/writes the
  shared `ctx.websocket`, `ctx.bridge`, `ctx.symbols`, `ctx.bus`.
- `services/broker_auth_service.rs` (987 lines) — `authenticate_as`
  (sign-in) ends every other broker's session first (the "switch asks
  first" rule), `try_resume` resumes the single `latest_active` row,
  `expire_before` (daily boundary) tears down the one in-memory session if
  it predates the boundary, `persist` writes the auth row + sets
  `active_broker` + `set_broker_session(Some(s))` + `state.api_keys.clear()`.
  `session_lock` serializes all of it. `PendingRevoke` retries a failed
  revoke across restart (SEC-05b).
- `services/broker_switch.rs` — the "what still depends on the logged-in
  broker" guard asked before a switch ends it.
- `services/master_contract_service.rs` — smart download rule; claims are
  already per-broker (`DownloadClaims`); the result goes to the ONE
  `ctx.symbols`; `ctx.clear_symbol_cache()` on broker switch.
- `session/` — web session (browser) + `boundary` polling
  (`expire_if_crossed`) + `session_account` (NEW-TR-01, reads the
  session's saved keys).
- The 8765 server (`feed/`, `websocket/`) serves whatever feed
  `ctx.websocket` holds; the 8765 order stream consumes
  `Event::OrderUpdate` from the bus (generation-stamped).

## 3. Design (decisions + rationale)

**D-S1 — `SessionStore` in `state.rs`.**
```rust
pub struct SessionEntry {
    pub session: BrokerSession,          // gains `account_id: String`
    pub runtime: Arc<BrokerRuntime>,     // one instance per entry
    pub symbols: SymbolResolver,         // per-entry master contract
}
pub struct SessionStore {
    entries: RwLock<HashMap<(String, String), SessionEntry>>, // (broker, account)
}
```
`AppState.broker_session` is replaced by `sessions: Arc<SessionStore>`;
`AppState.get_broker_session()` stays as the **default resolver** (see
D-S4) so the 77 call sites keep working untouched in this phase;
`set_broker_session` becomes `sessions.upsert(...)`. `AppState::clone()`
copies the Arc. `BrokerSession` (state.rs:35) gains `account_id: String`
(every existing constructor gets `DEFAULT_ACCOUNT.into()`; the sign-in
paths will name the real account when the UI has one).

**D-S2 — Per-session runtime instances; the shared pieces move into the
entry.** `BrokerRuntime::new()` already builds its own `order_ws`,
`depth_ws`, `tasks`, `active`, `started` — instantiate one per entry.
What still reaches into globals must be parameterized:
- `start_session(ctx, ...)` → `start_session(ctx, entry, ...)`:
  `ctx.bridge.set_depth_capability(...)` and `ctx.websocket.connect(feed)`
  are the two global writes. **Phase 1 rule: the 8765 market-data server
  and the bridge state serve the DEFAULT entry's feed** (the default is
  what `/api/v1` and the existing UI contract expect — "no behavior change
  for single-session clients"). Non-default sessions still get: their own
  master contract, their own order relay (bus events are already
  generation-stamped and carry `broker`), their own depth socket (the
  depth feed is broker-internal; expose it only when that entry is the
  default). Document this in `broker_runtime.rs` and in the Health page
  copy in Phase 3. The true 8765 union of multiple feeds is Phase 2
  (D2 grid) — do not build a half-version of it now.
- `ctx.symbols` (master) → `entry.symbols`; `master_contract_service::ensure`
  takes the entry's resolver instead of `ctx.symbols` (it already
  parameterizes claims per broker). `ctx.clear_symbol_cache()` becomes
  `entry.symbols.clear()`. `SymbolResolver` is `Clone` + `Arc`-based —
  verify `sandbox_feed::MasterSymbols` usage (state.rs:303) still reads
  the DEFAULT entry's resolver.
- `ctx.bus.begin_session()` (generation bump) stays GLOBAL: it must
  invalidate order updates from any ending session (EV-02); generations
  already tag events per session.

**D-S3 — `BrokerAuthService` becomes multi-session; the "end the other
broker" rule is retired, the guard becomes per-session.**
- `authenticate_as(state, broker, account, ...)`: NO longer tears down
  other entries. It still: loads credentials for `(broker, account)`
  (already done in the DB layer), signs in, `upsert`s the auth row,
  activates that entry's runtime, publishes `BrokerConnected { broker,
  account }` (event gains the account — check `events.rs` consumers).
  `persist` sets `active_broker` + `default_account` in settings (the
  default target follows the latest sign-in, as today's `active_broker`
  does).
- The `switch_guard` check (`broker_switch::refusal`) now asks "does THIS
  entry's session have live obligations" — it already records broker+
  account on obligations (`live_owner`), so the filter is per
  (broker, account). Signing into broker B never touches broker A's
  obligations.
- `try_resume`: after a password sign-in, resume **every** active stored
  session (loop over `auth::list_active`), each into its own entry, each
  validated with its own funds call (parallelizable — 32-core box; use a
  bounded `JoinSet`, max 8 concurrent). A resume that fails revokes only
  its own row (already the per-row behavior of `list_active`).
- `expire_before` (SES-02): iterate entries; end each whose
  `authenticated_at` < boundary and whose broker has a daily boundary;
  keep crypto sessions. `runtime.teardown_started_before` already
  encodes that per entry.
- `revoke`/logout: per (broker, account); `PendingRevoke::write` already
  maps to the account-aware `auth::revoke_all` / `revoke_before`
  (signatures updated in the DB layer).
- `session_lock` stays (it serializes sign-in/resume/revoke handovers
  that still touch shared state: settings default, 8765 default feed,
  api_keys cache).

**D-S4 — Default target + headers.**
- `AppState.default_target() -> (String, Option<String>)`:
  `active_broker` + `default_account` from settings (both now exist),
  falling back to the newest entry. `get_broker_session()` resolves:
  entry for the default target, else the newest entry, else `None`.
  Single-session installs see exactly today's behavior (their one entry
  IS the default).
- Header plumbing (Phase 1 deliverable, no behavior change when absent):
  in the server request context, read optional `X-OpenAlgo-Broker` +
  `X-OpenAlgo-Account`; when present, routes that resolve "the" session
  (order placement, funds, positions) resolve that (broker, account)
  entry instead of the default; unknown pair → 404 with the existing
  "not signed in" shape. Add ONE helper (`ctx.session_for(headers)`) so
  routes do not each parse. Keep the web contract byte-identical when the
  headers are absent (contract tests must stay green).

**D-S5 — `api_keys.clear()` on sign-in** (persist) clears the cached API
key for the session; with N sessions the cache must key by
(broker, account) — inspect `ApiKeyCache` before deciding (it may already
be per-broker; if it is per-process, scope it per entry like `symbols`).

## 4. Implementation order (each step: tests in the same commit, full
gates, push when green)

1. **`BrokerSession.account_id` + `SessionStore` + default resolver shim.**
   No behavior change: one entry at a time exactly as today (sign-in
   still ends the other). Tests: existing suite green; new test: store
   upsert/lookup/resolve-default. This step is mostly type-level and
   de-risks the rest.
2. **Per-entry `SymbolResolver` + `master_contract_service` parameterized.**
   Behavior unchanged for one session; tests: master for broker A loads
   while a (faked) entry for broker B exists in the store.
3. **Second sign-in no longer ends the first** (retire the switch rule in
   `authenticate_as`; per-session guard; per-entry activate; both
   streaming — 8765 serves the default's feed per D-S2). Tests: two
   brokers signed in at once (the Phase 1 acceptance test), logout of one
   leaves the other's runtime running and orders flowing; boundary ends
   only stale entries; forged/second resume of one does not touch the
   other.
4. **`try_resume` restores all active sessions** (bounded parallel).
   Test: two stored sessions both resume after restart; one stale one is
   revoked and the other survives.
5. **`X-OpenAlgo-Broker`/`X-OpenAlgo-Account`** helper + the order/funds
   routes; contract tests for absent headers unchanged.
6. **Event/Health polish:** `BrokerConnected`/`BrokerSessionEnded` carry
   (broker, account); UI can show multiple Active badges (the frontend
   half lands with Phase 3's accounts page; only the event shape changes
   now).

## 5. Performance notes (D7 — 32 cores @ 5 GHz)

- Resume-all (step 4) and per-entry activation are embarrassingly
  parallel: bounded `JoinSet` (8), each entry on its own tasks; never
  serialize broker handshakes behind one lock.
- `SessionStore.entries` is a `parking_lot::RwLock<HashMap>` — read path
  (`get_broker_session`) takes only the read guard; do not hold it across
  `.await` (copy the `Arc<BrokerRuntime>`/`SymbolResolver` out first).
- Keep the order-update relay (bus publish) off the hot path of decode;
  per-entry `JoinSet` tasks already do this — two entries = two relays in
  parallel, no shared reader loop.
- Build/test: `CARGO_BUILD_JOBS=16`; nextest already one process per
  test — the CI profile's test threads can stay default (the suite
  finished in ~3 min of runtime on this box).

## 6. Pitfalls found while mapping this (save the re-discovery)

- `ctx.websocket` is BOTH the 8765 listener's source and a plain feed
  socket — do not "fix" it into multi-feed in Phase 1 (that is the Phase
  2 grid); the default-entry rule (D-S2) is the honest Phase 1 boundary.
- `state.rs:303` wraps `ctx.symbols` in `sandbox_feed::MasterSymbols` —
  the sandbox reads the global resolver; after D-S2 it must read the
  DEFAULT entry's, or sandbox books of a second broker break silently.
- `persist` writes `active_broker` in the SAME transaction as the auth row
  ("the row and the active broker change together or not at all") —
  keep that invariant when it becomes `active_broker` +
  `default_account`.
- `session/mod.rs` tests (`a_sign_in_racing_the_boundary_poll_is_kept`,
  `flow03_...`) encode the single-session assumptions as acceptance
  criteria — they are the tests to re-target per the plan, not delete.
- `events::OrderUpdate` already carries `broker`; adding `account` is
  additive — check every `json!` serializer of the event before renaming
  anything.
- The `permissions/autogenerated/*.toml` files in the working tree show
  LF→CRLF noise that predates this work — leave them unstaged.
- `rust-toolchain.toml` resolves `channel = "1.99.1"` to the gnu
  toolchain by default; the repo's CI uses MSVC. The directory override
  (see §1) is the local fix — do not commit an msvc triple into
  `rust-toolchain.toml` (it would break the Linux/macOS runners).
