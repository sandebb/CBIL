//! Bringing back stored sessions after a restart or a password sign-in:
//! every session that may still be resumed (one from before the daily
//! boundary is revoked instead, SES-02; a crypto session has no boundary,
//! SES-01), each broker still accepting its own token — in parallel, at
//! most eight checks in flight, like the web's funds call — and each
//! accepted one installed under the session lock, beside any session
//! that is live meanwhile (D-S3). The newest session is the default
//! target the shared market feed and `/api/v1` serve (D-S4).

use super::BrokerAuthService;
use crate::brokers::{catalog, BrokerRegistry};
use crate::db::sqlite::{auth, credentials, SqliteDb};
use crate::error::{AppError, Result};
use crate::events::Event;
use crate::security::SecurityManager;
use crate::state::{AppState, BrokerSession};
use std::sync::Arc;
use std::time::Duration;

const RESUME_CHECK_TIMEOUT: Duration = Duration::from_secs(15);
/// The broker checks that run at once: a couple of brokers resume together,
/// but a fleet of them must not flood the machine (D7).
const MAX_CHECKS_IN_FLIGHT: usize = 8;

/// The in-memory session a stored row carries.
fn session_from(stored: &auth::StoredBrokerSession) -> BrokerSession {
    BrokerSession {
        broker_id: stored.broker_id.clone(),
        account_id: stored.account_id.clone(),
        auth_token: stored.auth_token.clone(),
        feed_token: stored.feed_token.clone(),
        user_id: stored.user_id.clone().unwrap_or_default(),
        user_name: stored.user_name.clone(),
        authenticated_at: stored.authenticated_at,
    }
}

/// The broker's answer for every candidate at once, at most
/// `MAX_CHECKS_IN_FLIGHT` in flight; the ones the broker accepts come back
/// in `list_active`'s order (newest first). A check that errors or is
/// cancelled skips only its own row. Each check runs on its own task with
/// the shared pieces only, so the handshakes run together, not behind the
/// session lock (D7).
async fn accept_candidates_in_parallel(
    state: &AppState,
    candidates: Vec<auth::StoredBrokerSession>,
) -> Vec<auth::StoredBrokerSession> {
    let brokers = state.brokers.clone();
    let sqlite = state.sqlite.clone();
    let security = state.security.clone();
    let mut set = tokio::task::JoinSet::new();
    let mut waiting = candidates.into_iter().enumerate();
    while set.len() < MAX_CHECKS_IN_FLIGHT {
        let Some((i, s)) = waiting.next() else { break };
        let (b, d, k) = (brokers.clone(), sqlite.clone(), security.clone());
        set.spawn(async move {
            let ok = accept_one(&b, &d, &k, &s).await;
            (i, s, ok)
        });
    }
    let mut accepted: Vec<(usize, auth::StoredBrokerSession)> = Vec::new();
    while let Some(r) = set.join_next().await {
        match r {
            Ok((i, s, Ok(true))) => accepted.push((i, s)),
            Ok((_, _, Ok(false))) => {
                // `accept_one` already says why.
            }
            Ok((_, s, Err(e))) => {
                tracing::warn!(
                    "Stored broker session for {} could not be checked: {}",
                    s.broker_id,
                    e
                );
            }
            Err(_) => tracing::warn!("A stored broker session check was cancelled"),
        }
        while set.len() < MAX_CHECKS_IN_FLIGHT {
            let Some((i, s)) = waiting.next() else { break };
            let (b, d, k) = (brokers.clone(), sqlite.clone(), security.clone());
            set.spawn(async move {
                let ok = accept_one(&b, &d, &k, &s).await;
                (i, s, ok)
            });
        }
    }
    accepted.sort_by_key(|(i, _)| *i);
    accepted.into_iter().map(|(_, s)| s).collect()
}

/// Whether the broker still accepts the stored token. Like the web, a
/// cheap funds call proves it; per-login state the token does not carry
/// (Kotak's UCC) comes back from the stored broker credentials first.
async fn accept_one(
    brokers: &Arc<BrokerRegistry>,
    sqlite: &Arc<SqliteDb>,
    security: &Arc<SecurityManager>,
    stored: &auth::StoredBrokerSession,
) -> Result<bool> {
    let Some(broker) = brokers.get(&stored.broker_id) else {
        return Ok(false);
    };
    let stored_creds = {
        let conn = sqlite.conn()?;
        credentials::load(&conn, security, &stored.broker_id, &stored.account_id)?
    };
    if let Some(c) = &stored_creds {
        broker.restore_session(&BrokerAuthService::stored_input(c));
    }
    match tokio::time::timeout(
        RESUME_CHECK_TIMEOUT,
        broker.get_funds(&crate::brokers::types::AuthToken::new(
            stored.auth_token.expose(),
        )),
    )
    .await
    {
        Ok(Ok(_)) => Ok(true),
        Ok(Err(e)) => {
            tracing::info!("Stored broker session not accepted by broker: {}", e.code());
            Ok(false)
        }
        Err(_) => {
            tracing::info!("Broker did not answer the session check in time");
            Ok(false)
        }
    }
}

impl BrokerAuthService {
    /// Resume every active stored session after a password sign-in or a
    /// restart: one from before the daily boundary is revoked instead, and
    /// every broker that still accepts its token gets its session back,
    /// beside any session that is live meanwhile. `Ok(None)` when there is
    /// nothing to resume (a live session, if there is one, is returned).
    pub async fn try_resume(state: &AppState) -> Result<Option<BrokerSession>> {
        // A session the trader ended whose row could not be revoked yet is
        // never brought back: the pending revoke is written first, and
        // while it cannot be, nothing is resumed. Once written, only what
        // it spared can come back (a crypto session the boundary kept).
        if Self::revoke_is_pending(state) {
            if let Err(e) = Self::retry_pending_revoke(state) {
                tracing::warn!("The ended broker session is still to be revoked: {}", e);
                return Ok(None);
            }
        }
        let stored = {
            let conn = state.sqlite.conn()?;
            match auth::list_active(&conn, &state.security) {
                Ok(v) => v,
                // Locked while it was read: nothing resumes now, and the
                // next poll or sign-in retries.
                Err(AppError::Locked) => Vec::new(),
                Err(e) => return Err(e),
            }
        };
        // Newest first (as `list_active` returns it). One from before the
        // daily boundary is revoked instead — per row, so the revoke never
        // reaches a newer session's row (SES-02); a crypto session has no
        // boundary (SES-01).
        let cfg = state.server_config();
        let now = state.now();
        let mut candidates: Vec<auth::StoredBrokerSession> = Vec::new();
        for s in stored {
            if catalog::has_daily_boundary(&s.broker_id)
                && !crate::session::boundary::is_fresh(
                    s.authenticated_at,
                    now,
                    cfg.session_expiry_hour,
                    cfg.session_expiry_minute,
                )
            {
                let conn = state.sqlite.conn()?;
                auth::revoke(&conn, &s.broker_id, &s.account_id)?;
                continue;
            }
            candidates.push(s);
        }
        if candidates.is_empty() {
            return Ok(state.get_broker_session());
        }
        // Each broker's answer, in parallel and bounded (D7): the checks
        // run together, not behind the session lock.
        let accepted = accept_candidates_in_parallel(state, candidates).await;
        // Newest first, so the first install is the default target (D-S4);
        // each one joins the store beside the live sessions (D-S3).
        let mut resumed: Vec<BrokerSession> = Vec::new();
        for stored in accepted {
            if Self::install_resumed(state, &stored).await? {
                resumed.push(session_from(&stored));
            }
        }
        if resumed.is_empty() {
            return Ok(state.get_broker_session());
        }
        Ok(resumed.into_iter().next())
    }

    /// Install one resumed session, under the session lock. A sign-in of
    /// the same pair that finished while the broker was answering wins; a
    /// pending revoke stops it; and the row must still be stored (a logout
    /// that wrote meanwhile ends it). Then it joins the store beside the
    /// live sessions (D-S3), its streaming started on its own runtime.
    async fn install_resumed(state: &AppState, stored: &auth::StoredBrokerSession) -> Result<bool> {
        let session = session_from(stored);
        let _held = Self::session_lock(state).await;
        if state
            .broker_sessions
            .get(&stored.broker_id, &stored.account_id)
            .is_some()
        {
            return Ok(false);
        }
        if Self::revoke_is_pending(state) {
            return Ok(false);
        }
        let still_stored = {
            let conn = state.sqlite.conn()?;
            auth::is_active(&conn, &stored.broker_id, &stored.account_id)?
        };
        if !still_stored {
            return Ok(false);
        }
        state.broker_sessions.upsert(session.clone());
        state.bus.publish(Event::BrokerConnected {
            broker: session.broker_id.clone(),
            user_id: Some(session.user_id.clone()),
        });
        tracing::info!(
            "Resumed broker session for {} ({})",
            session.broker_id,
            session.account_id
        );
        Self::activate(state, &session).await;
        Ok(true)
    }
}
