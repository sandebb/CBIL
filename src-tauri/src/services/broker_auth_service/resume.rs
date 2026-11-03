//! Bringing back a stored session after a restart or a password sign-in:
//! what may still be resumed (a session from before the daily boundary
//! is revoked instead, SES-02; a crypto session has no boundary, SES-01),
//! the broker still accepting the token (a funds call, like the web), and
//! the install under the session lock, beside any session that is live
//! meanwhile (D-S3).

use super::BrokerAuthService;
use crate::brokers::catalog;
use crate::db::sqlite::{auth, credentials};
use crate::error::{AppError, Result};
use crate::events::Event;
use crate::state::{AppState, BrokerSession};
use std::time::Duration;

const RESUME_CHECK_TIMEOUT: Duration = Duration::from_secs(15);

impl BrokerAuthService {
    /// Resume the stored session after a password sign-in. `Ok(None)` when
    /// there is nothing to resume or the broker no longer accepts it.
    pub async fn try_resume(state: &AppState) -> Result<Option<BrokerSession>> {
        if let Some(s) = state.get_broker_session() {
            return Ok(Some(s));
        }
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
        let Some(stored) = Self::resumable_stored_session(state)? else {
            return Ok(None);
        };
        if !Self::broker_accepts(state, &stored).await? {
            return Ok(None);
        }
        Self::install_resumed(state, stored).await
    }

    /// The latest stored session, if it may still be resumed. One from
    /// before the daily boundary is revoked instead; a continuous (crypto)
    /// session has no daily boundary (SES-01), and the broker checks its
    /// token either way.
    fn resumable_stored_session(state: &AppState) -> Result<Option<auth::StoredBrokerSession>> {
        let stored = {
            let conn = state.sqlite.conn()?;
            match auth::latest_active(&conn, &state.security) {
                Ok(s) => s,
                Err(AppError::Locked) => None,
                Err(e) => return Err(e),
            }
        };
        let Some(stored) = stored else {
            return Ok(None);
        };
        let cfg = state.server_config();
        if catalog::has_daily_boundary(&stored.broker_id)
            && !crate::session::boundary::is_fresh(
                stored.authenticated_at,
                state.now(),
                cfg.session_expiry_hour,
                cfg.session_expiry_minute,
            )
        {
            let conn = state.sqlite.conn()?;
            auth::revoke(&conn, &stored.broker_id, &stored.account_id)?;
            return Ok(None);
        }
        Ok(Some(stored))
    }

    /// Whether the broker still accepts the stored token. Like the web, a
    /// cheap funds call proves it; per-login state the token does not carry
    /// (Kotak's UCC) comes back from the stored broker credentials first.
    async fn broker_accepts(state: &AppState, stored: &auth::StoredBrokerSession) -> Result<bool> {
        let Some(broker) = state.brokers.get(&stored.broker_id) else {
            return Ok(false);
        };
        let stored_creds = {
            let conn = state.sqlite.conn()?;
            credentials::load(
                &conn,
                &state.security,
                &stored.broker_id,
                &stored.account_id,
            )?
        };
        if let Some(c) = &stored_creds {
            broker.restore_session(&Self::stored_input(c));
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

    /// Install the resumed session. A sign-in or a sign-out may have
    /// finished during the broker's check: under the session lock, the
    /// stored session comes back only if nothing changed meanwhile.
    async fn install_resumed(
        state: &AppState,
        stored: auth::StoredBrokerSession,
    ) -> Result<Option<BrokerSession>> {
        let session = BrokerSession {
            broker_id: stored.broker_id.clone(),
            account_id: stored.account_id.clone(),
            auth_token: stored.auth_token,
            feed_token: stored.feed_token,
            user_id: stored.user_id.unwrap_or_default(),
            user_name: stored.user_name,
            authenticated_at: stored.authenticated_at,
        };
        let _held = Self::session_lock(state).await;
        if let Some(s) = state.get_broker_session() {
            return Ok(Some(s));
        }
        if Self::revoke_is_pending(state) {
            return Ok(None);
        }
        let still_stored = {
            let conn = state.sqlite.conn()?;
            auth::latest_active(&conn, &state.security)?.is_some_and(|s| {
                s.broker_id == session.broker_id && s.authenticated_at == session.authenticated_at
            })
        };
        if !still_stored {
            return Ok(None);
        }
        // A session live in memory meanwhile is untouched (D-S3); the
        // resumed one joins the store beside it.
        state.broker_sessions.upsert(session.clone());
        state.bus.publish(Event::BrokerConnected {
            broker: session.broker_id.clone(),
            user_id: Some(session.user_id.clone()),
        });
        tracing::info!("Resumed broker session for {}", session.broker_id);
        Self::activate(state, &session).await;
        Ok(Some(session))
    }
}
