//! What runs while a broker session is live, started on login (or resume)
//! and torn down on logout, at the daily boundary and on app shutdown.
//!
//! One `BrokerRuntime` per live session entry (`SessionEntry.runtime`,
//! D-S2): its own order-update socket, its own owned tasks and its order
//! relay, so several brokers stream at once and ending one never touches
//! the other's. The shared pieces follow the default entry (the newest
//! session, D-S4): the market feed lives on `ctx.websocket`, the depth
//! feed on `ctx.depth_ws`, and the bridge state (`ctx.bridge`) is theirs —
//! a non-default session gets no market feed and no depth feed in Phase 1
//! (its order feed and relay still run, and its master loads through the
//! shared object while it is the default). The 8765 union of several
//! feeds is Phase 2 (D2 grid).
//!
//! `activate` (web `handle_auth_success` plus `order_update_service`):
//! 1. tear down what the same pair's earlier activation left running;
//! 2. for the default entry: the master contract, download when the smart
//!    rule says so, else load the stored one (`master_contract_service::ensure`);
//! 3. for the default entry: the feed bridge learns the broker's depth
//!    levels, the market feed onto the shared manager, a depth socket on
//!    the shared depth manager when the broker streams deeper books;
//! 4. the order feed (`create_order_feed`, a socket or the adapter's
//!    poller) on this entry's own manager — for every entry;
//! 5. order updates from any of them are published as `order.update` with
//!    the web payload, generation-stamped (EV-02).
//!
//! `teardown`: this entry's tasks are aborted, its order socket
//! disconnected, and — only for the entry that held the shared sockets —
//! the shared market/depth sockets are closed and the bridge reset. The
//! broker's `on_logout` stops what the adapter runs itself (Groww's
//! poller).
//!
//! `promote_default`: when the default entry ends while others stay live,
//! the newest survivor takes the shared sockets: its market feed
//! reconnects, the bridge re-applies what its clients still want.

use crate::brokers::common::streaming::{FeedEvent, MarketEvent, OrderFeed, OrderUpdate};
use crate::brokers::types::AuthToken;
use crate::brokers::Broker;
use crate::error::AppError;
use crate::events::{self, Event, EventBus};
use crate::feed::bridge;
use crate::services::master_contract_service;
use crate::state::{AppState, BrokerSession};
use crate::websocket::WebSocketManager;
use parking_lot::Mutex;
use std::sync::Arc;
use tokio::sync::broadcast::error::RecvError;
use tokio::sync::{broadcast, mpsc};
use tokio::task::JoinSet;

pub struct BrokerRuntime {
    /// The broker's order-update socket (this entry's own).
    pub order_ws: Arc<WebSocketManager>,
    tasks: Arc<Mutex<JoinSet<()>>>,
    active: Mutex<Option<Arc<dyn Broker>>>,
    /// The bus generation stamped on this entry's order updates (EV-02);
    /// read again when the entry is promoted to the default.
    generation: Mutex<Option<u64>>,
    /// The order relay on the shared market socket (the default entry's);
    /// aborted when this entry stops holding the shared socket.
    shared_relay: Mutex<Option<tokio::task::AbortHandle>>,
    /// Serialises activate / teardown of this entry.
    lifecycle: tokio::sync::Mutex<()>,
}

impl Default for BrokerRuntime {
    fn default() -> Self {
        Self::new()
    }
}

impl Drop for BrokerRuntime {
    fn drop(&mut self) {
        // The entry is gone (a stale session the boundary check already
        // dropped from memory, or a store clear): nothing may outlive it.
        if let Some(h) = self.shared_relay.lock().take() {
            h.abort();
        }
        let mut tasks = std::mem::take(&mut *self.tasks.lock());
        tasks.abort_all();
    }
}

impl BrokerRuntime {
    pub fn new() -> Self {
        Self {
            order_ws: Arc::new(WebSocketManager::new()),
            tasks: Arc::new(Mutex::new(JoinSet::new())),
            active: Mutex::new(None),
            generation: Mutex::new(None),
            shared_relay: Mutex::new(None),
            lifecycle: tokio::sync::Mutex::new(()),
        }
    }

    /// Owned tasks still alive (finished ones are reaped first).
    pub fn task_count(&self) -> usize {
        let mut t = self.tasks.lock();
        while t.try_join_next().is_some() {}
        t.len()
    }

    /// The broker whose session is running, if any.
    pub fn active_broker(&self) -> Option<String> {
        self.active.lock().as_ref().map(|b| b.id().to_string())
    }

    /// Abort the relay on the shared market socket (this entry stops
    /// holding it: a newer default took over, and its broker's id must not
    /// tag the new broker's order updates).
    pub fn abort_shared_relay(&self) {
        if let Some(h) = self.shared_relay.lock().take() {
            h.abort();
        }
    }

    /// Run `fut` as a task of this entry (aborted on its teardown).
    pub fn spawn_task<F>(&self, fut: F)
    where
        F: std::future::Future<Output = ()> + Send + 'static,
    {
        Self::spawn(&self.tasks, fut);
    }

    /// One task of this entry; the handle aborts it on the entry's teardown.
    fn spawn<F>(tasks: &Arc<Mutex<JoinSet<()>>>, fut: F) -> tokio::task::AbortHandle
    where
        F: std::future::Future<Output = ()> + Send + 'static,
    {
        let mut t = tasks.lock();
        while t.try_join_next().is_some() {}
        t.spawn(fut)
    }

    /// Start everything for a new broker session (returns once it is
    /// started; the master contract and the sockets come up in a task).
    /// `is_default` marks the entry that holds the shared market feed and
    /// the bridge state (the newest session, D-S2/D-S4).
    pub async fn activate(&self, ctx: &Arc<AppState>, session: &BrokerSession, is_default: bool) {
        let _guard = self.lifecycle.lock().await;
        // A re-sign-in of the same pair: its earlier streaming ends first
        // (only this entry's — another entry's keeps running, D-S2).
        self.teardown_locked(ctx, false).await;
        if is_default {
            // A different broker's master in memory must not serve this
            // session's symbols: its tokens would resolve to the wrong
            // instruments (MC-01). Cleared before the load, as the
            // single-session switch did.
            let owner = ctx
                .sqlite
                .conn()
                .ok()
                .and_then(|c| crate::db::sqlite::symbol::owner(&c).ok())
                .flatten();
            if !ctx.symbols.is_empty()
                && owner
                    .as_ref()
                    .is_some_and(|o| o.broker != session.broker_id)
            {
                ctx.clear_symbol_cache();
            }
            // The shared socket was the previous default's: close it and
            // its bookkeeping before this entry's feed takes over (the
            // bridge keeps what its clients still want, for `resync`).
            for e in ctx.broker_sessions.entries() {
                if !std::ptr::eq(Arc::as_ptr(&e.runtime), self) {
                    e.runtime.abort_shared_relay();
                }
            }
            let _ = ctx.websocket.disconnect().await;
            let _ = ctx.depth_ws.disconnect().await;
            ctx.bridge.reset();
        }
        let Some(broker) = ctx.brokers.get(&session.broker_id) else {
            tracing::warn!("No adapter for {}; nothing to start", session.broker_id);
            return;
        };
        let auth = AuthToken::new(session.auth_token.expose())
            .with_feed(session.feed_token.as_ref().map(|s| s.expose().to_string()))
            .with_user_id(session.user_id.clone());
        *self.active.lock() = Some(broker.clone());
        // Every order update this session relays carries its generation; one
        // from an earlier session is never delivered (EV-02).
        let generation = ctx.bus.begin_session();
        *self.generation.lock() = Some(generation);

        // Order updates: subscribe before any socket connects so none is
        // missed (Kite carries them on the market feed; the default
        // entry's relay on the shared socket covers that case).
        let bus = ctx.bus.clone();
        let id = broker.id();
        {
            let rx = self.order_ws.subscribe_ticks();
            Self::spawn(
                &self.tasks,
                relay_socket_orders(rx, self.order_ws.clone(), bus.clone(), (id, generation)),
            );
        }
        if is_default {
            let rx = ctx.websocket.subscribe_ticks();
            let h = Self::spawn(
                &self.tasks,
                relay_socket_orders(rx, ctx.websocket.clone(), bus, (id, generation)),
            );
            *self.shared_relay.lock() = Some(h);
        }

        // A gap in this entry's order updates must pause the reconciler's
        // new entries (LOG-08); feeds are watched additively.
        ctx.reconciler.watch_feed(self.order_ws.watch_status());

        let weak = Arc::downgrade(ctx);
        let order_ws = self.order_ws.clone();
        let tasks = self.tasks.clone();
        Self::spawn(&self.tasks, async move {
            let Some(ctx) = weak.upgrade() else { return };
            start_session(ctx, broker, auth, order_ws, tasks, generation, is_default).await;
        });
    }

    /// Stop everything this entry runs. Idempotent. `is_default` marks the
    /// entry that held the shared market and depth sockets: only its
    /// teardown closes them and resets the bridge (another entry's may
    /// still be live beside it, D-S2).
    pub async fn teardown(&self, ctx: &AppState, is_default: bool) {
        let _guard = self.lifecycle.lock().await;
        self.teardown_locked(ctx, is_default).await;
    }

    /// Returns the broker whose session was running, if any.
    async fn teardown_locked(&self, ctx: &AppState, is_default: bool) -> Option<&'static str> {
        // Updates still queued from the ending session are stale from here.
        ctx.bus.begin_session();
        if let Some(h) = self.shared_relay.lock().take() {
            h.abort();
        }
        let mut tasks = std::mem::take(&mut *self.tasks.lock());
        tasks.abort_all();
        while tasks.join_next().await.is_some() {}
        let _ = self.order_ws.disconnect().await;
        if is_default {
            let _ = ctx.websocket.disconnect().await;
            let _ = ctx.depth_ws.disconnect().await;
            ctx.bridge.reset();
        }
        let broker = self.active.lock().take();
        let b = broker?;
        b.on_logout().await;
        tracing::info!("Broker streaming for {} stopped", b.id());
        Some(b.id())
    }
}

/// The default entry ended while others stay live (D-S4): the newest
/// survivor takes the shared market feed, the depth socket and the bridge.
/// Its order feed and relay already run (per entry, D-S2); this connects
/// what the shared socket carries.
pub async fn promote_default(ctx: &Arc<AppState>) {
    let Some(entry) = ctx.broker_sessions.newest_entry() else {
        return;
    };
    let rt = entry.runtime.clone();
    if rt.active_broker().is_none() || rt.generation.lock().is_none() {
        return; // never activated (or already torn down)
    }
    for e in ctx.broker_sessions.entries() {
        if Arc::as_ptr(&e.runtime) != Arc::as_ptr(&rt) {
            e.runtime.abort_shared_relay();
        }
    }
    let Some(broker) = ctx.brokers.get(&entry.session.broker_id) else {
        tracing::warn!(
            "No adapter for {}; the promotion does nothing",
            entry.session.broker_id
        );
        return;
    };
    let auth = AuthToken::new(entry.session.auth_token.expose())
        .with_feed(
            entry
                .session
                .feed_token
                .as_ref()
                .map(|s| s.expose().to_string()),
        )
        .with_user_id(entry.session.user_id.clone());
    // A different broker's master in memory must not serve the promoted
    // session's symbols (MC-01); the download below loads or replaces it.
    let owner = ctx
        .sqlite
        .conn()
        .ok()
        .and_then(|c| crate::db::sqlite::symbol::owner(&c).ok())
        .flatten();
    if !ctx.symbols.is_empty() && owner.as_ref().is_some_and(|o| o.broker != broker.id()) {
        ctx.clear_symbol_cache();
    }
    let generation = rt.generation.lock().unwrap();
    let bus = ctx.bus.clone();
    let id = broker.id();
    {
        let rx = ctx.websocket.subscribe_ticks();
        let h = BrokerRuntime::spawn(
            &rt.tasks,
            relay_socket_orders(rx, ctx.websocket.clone(), bus, (id, generation)),
        );
        *rt.shared_relay.lock() = Some(h);
    }
    let master_ready = ensure_master_for_promotion(ctx, &broker, &auth).await;
    connect_shared_feeds_on_promotion(ctx, &broker, &auth, master_ready).await;
    tracing::info!("{} takes the default broker session", id);
}

/// The promoted session's master contract (MC-01): load or replace it in
/// the shared master, or report it unavailable.
async fn ensure_master_for_promotion(
    ctx: &Arc<AppState>,
    broker: &Arc<dyn Broker>,
    auth: &AuthToken,
) -> bool {
    match master_contract_service::ensure(ctx, broker, auth).await {
        Ok(_) => true,
        Err(e) => {
            tracing::error!(
                "Master contract for {} is not available on promotion: {}",
                broker.id(),
                e
            );
            master_contract_service::report_unavailable(ctx, broker.id());
            false
        }
    }
}

/// The shared sockets the promoted session takes: the market feed, the
/// depth feed and the bridge's depth capability; then the feed clients'
/// desired sets are applied to what is now in place (they survived the
/// ended default's `bridge.reset`).
async fn connect_shared_feeds_on_promotion(
    ctx: &Arc<AppState>,
    broker: &Arc<dyn Broker>,
    auth: &AuthToken,
    master_ready: bool,
) {
    let id = broker.id();
    let caps = {
        let b = broker.clone();
        Arc::new(move |exchange: &str| b.feed_depth_levels(exchange))
    };
    ctx.bridge.set_depth_capability(caps);
    // A feed that cannot stream makes feed subscribes say why (WS-01).
    match broker.create_feed(auth) {
        Ok(feed) => {
            if let Err(e) = ctx.websocket.connect(feed).await {
                tracing::error!(
                    "Market data feed for {} did not start on promotion: {}",
                    id,
                    e
                );
                ctx.bridge.set_unavailable(bridge::FEED_NOT_STARTED);
            }
        }
        Err(AppError::Unsupported(_)) => {
            tracing::info!("{} has no live market data feed in OpenAlgo Desktop", id);
            ctx.bridge.set_unavailable(bridge::NO_LIVE_FEED);
        }
        Err(e) => {
            tracing::warn!(
                "Market data feed for {} not started on promotion: {}",
                id,
                e
            );
            ctx.bridge.set_unavailable(bridge::FEED_NOT_STARTED);
        }
    }
    let deep = broker
        .capabilities()
        .depth_levels
        .iter()
        .copied()
        .filter(|l| *l > 5)
        .find_map(|l| broker.create_depth_feed(auth, l).ok().map(|f| (l, f)));
    if let Some((levels, feed)) = deep {
        match ctx.depth_ws.connect(feed).await {
            Ok(()) => ctx.bridge.set_deep_levels(Some(levels)),
            Err(e) => tracing::warn!(
                "Market depth feed for {} not started on promotion: {}",
                id,
                e
            ),
        }
    }
    if master_ready {
        ctx.bridge.resync();
    }
}

/// The session's start-up sequence (runs as an owned task).
async fn start_session(
    ctx: Arc<AppState>,
    broker: Arc<dyn Broker>,
    auth: AuthToken,
    order_ws: Arc<WebSocketManager>,
    tasks: Arc<Mutex<JoinSet<()>>>,
    generation: u64,
    is_default: bool,
) {
    let id = broker.id();
    // The master contract and the bridge state serve the default entry
    // (D-S2): a non-default session loads nothing into the shared master
    // and touches the shared sockets not at all.
    if is_default {
        let master_ready = match master_contract_service::ensure(&ctx, &broker, &auth).await {
            Ok(_) => true,
            Err(e) => {
                tracing::error!("Master contract for {} is not available: {}", id, e);
                master_contract_service::report_unavailable(&ctx, id);
                false
            }
        };
        let caps = {
            let b = broker.clone();
            Arc::new(move |exchange: &str| b.feed_depth_levels(exchange))
        };
        ctx.bridge.set_depth_capability(caps);
        // A feed that cannot stream makes feed subscribes say why (WS-01).
        match broker.create_feed(&auth) {
            Ok(feed) => {
                if let Err(e) = ctx.websocket.connect(feed).await {
                    tracing::error!("Market data feed for {} did not start: {}", id, e);
                    ctx.bridge.set_unavailable(bridge::FEED_NOT_STARTED);
                }
            }
            Err(AppError::Unsupported(_)) => {
                tracing::info!("{} has no live market data feed in OpenAlgo Desktop", id);
                ctx.bridge.set_unavailable(bridge::NO_LIVE_FEED);
            }
            Err(e) => {
                tracing::warn!("Market data feed for {} not started: {}", id, e);
                ctx.bridge.set_unavailable(bridge::FEED_NOT_STARTED);
            }
        }
        let deep = broker
            .capabilities()
            .depth_levels
            .iter()
            .copied()
            .filter(|l| *l > 5)
            .find_map(|l| broker.create_depth_feed(&auth, l).ok().map(|f| (l, f)));
        if let Some((levels, feed)) = deep {
            match ctx.depth_ws.connect(feed).await {
                Ok(()) => ctx.bridge.set_deep_levels(Some(levels)),
                Err(e) => tracing::warn!("Market depth feed for {} not started: {}", id, e),
            }
        }
        // Feed clients that were already subscribed are applied now that
        // the master and the sockets are in place. Without a master they
        // wait: a later download or cache reload resyncs them.
        if master_ready {
            ctx.bridge.resync();
        }
    }
    // The order feed runs for every entry, on its own socket (D-S2).
    match broker.create_order_feed(&auth) {
        Ok(OrderFeed::Socket(feed)) => {
            if let Err(e) = order_ws.connect(feed).await {
                tracing::warn!("Order updates for {} not started: {}", id, e);
            }
        }
        Ok(OrderFeed::Stream(rx)) => {
            let bus = ctx.bus.clone();
            BrokerRuntime::spawn(&tasks, relay_stream_orders(rx, bus, id, generation));
        }
        Err(AppError::Unsupported(_)) => {}
        Err(e) => tracing::warn!("Order updates for {} not started: {}", id, e),
    }
    tracing::info!("Broker session for {} is streaming", id);
}

/// The web's `OrderUpdateEvent` payload, stamped with its session.
pub fn order_event(u: &OrderUpdate, broker: &str, generation: u64) -> Event {
    Event::OrderUpdate(events::OrderUpdate {
        session_generation: generation,
        mode: "live".into(),
        broker: broker.to_string(),
        orderid: u.orderid.clone(),
        symbol: u.symbol.clone(),
        exchange: u.exchange.clone(),
        action: u.action.clone(),
        quantity: u.quantity,
        price: u.price,
        trigger_price: u.trigger_price,
        pricetype: u.pricetype.clone(),
        product: u.product.clone(),
        order_status: u.order_status.clone(),
        filled_quantity: u.filled_quantity,
        pending_quantity: u.pending_quantity,
        average_price: u.average_price,
        rejection_reason: u.rejection_reason.clone(),
    })
}

async fn relay_socket_orders(
    mut rx: broadcast::Receiver<MarketEvent>,
    feed: Arc<WebSocketManager>,
    bus: Arc<EventBus>,
    (broker, generation): (&'static str, u64),
) {
    loop {
        match rx.recv().await {
            Ok(ev) => {
                if let FeedEvent::OrderUpdate(u) = &*ev {
                    bus.publish(order_event(u, broker, generation));
                }
            }
            Err(RecvError::Lagged(n)) => {
                // Skipped events may include order updates: the order
                // reconciler repairs from the broker's book (ARCH-01).
                tracing::warn!("Order update relay fell behind; skipped {} events", n);
                bus.report_relay_lag(n);
                feed.stats()
                    .note_skipped(crate::websocket::Reader::OrderRelay, n);
            }
            Err(RecvError::Closed) => return,
        }
    }
}

async fn relay_stream_orders(
    mut rx: mpsc::Receiver<OrderUpdate>,
    bus: Arc<EventBus>,
    broker: &'static str,
    generation: u64,
) {
    while let Some(u) = rx.recv().await {
        bus.publish(order_event(&u, broker, generation));
    }
}
