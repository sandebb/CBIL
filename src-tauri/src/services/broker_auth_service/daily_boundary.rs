//! The daily boundary (SES-01, SES-02): what ends when the 03:00 IST
//! boundary passes, and what does not.

use super::{BrokerAuthService, PendingRevoke};
use crate::brokers::catalog;
use crate::db::sqlite::auth;
use crate::error::Result;
use crate::events::{Event, SessionEndReason};
use crate::state::AppState;

impl BrokerAuthService {
    /// The daily boundary (SES-02): end only the sessions authenticated
    /// before `boundary`, so a sign-in made after it (before the next
    /// 30-second poll, or right after a wake from sleep) keeps its session,
    /// its streaming and its stored row. Continuous (crypto) sessions are
    /// not ended (SES-01). Each ended entry is torn down on its own
    /// runtime (D-S3); when the default entry ends and others stay live,
    /// the newest survivor takes the shared sockets (D-S4). Runs under the
    /// session lock, so no sign-in or resume completes meanwhile.
    ///
    /// The stored rows from before the boundary are revoked by time. When
    /// that write fails, the revoke is left pending like a failed sign-out
    /// (SEC-05b: in memory and on disk across a restart; no resume until it
    /// is written), recorded as the boundary's (`PendingRevoke::Before`):
    /// its retry revokes only what this write would have, never a session
    /// signed in after the boundary or a crypto one. Returns whether a live
    /// session ended.
    pub async fn expire_before(
        state: &AppState,
        boundary: chrono::DateTime<chrono::Utc>,
    ) -> Result<bool> {
        let daily = catalog::has_daily_boundary;
        let _held = Self::session_lock(state).await;
        // Every live session that predates the boundary (D-S3); a sign-in
        // that replaced one meanwhile wins (take_if keeps the newer one).
        let stale: Vec<crate::state::BrokerSession> = state
            .broker_sessions
            .list()
            .into_iter()
            .filter(|s| s.authenticated_at < boundary && daily(&s.broker_id))
            .collect();
        let (mut ended, mut default_ended) = (false, false);
        for s in stale {
            let was_default = state.broker_sessions.is_newest(&s.broker_id, &s.account_id);
            let runtime = state
                .broker_sessions
                .runtime_for(&s.broker_id, &s.account_id);
            let at = s.authenticated_at;
            if state
                .broker_sessions
                .take_if(&s.broker_id, &s.account_id, |l| l.authenticated_at == at)
                .is_some()
            {
                if let Some(runtime) = runtime {
                    runtime.teardown(state, was_default).await;
                }
                ended = true;
                default_ended = default_ended || was_default;
            }
        }
        // The default socket had the ended entry's feed: the newest
        // survivor takes it (D-S4), or nothing does.
        if default_ended && !state.broker_sessions.is_empty() {
            if let Some(ctx) = state.arc() {
                crate::services::broker_runtime::promote_default(&ctx).await;
            }
        }
        if ended {
            state.api_keys.clear();
        }
        if state.broker_sessions.is_empty() {
            state.clear_symbol_cache();
        }
        let written = state
            .sqlite
            .conn()
            .and_then(|conn| auth::revoke_before(&conn, boundary, |b, _a| !daily(b)));
        // No copy of a session the boundary ended may outlive it in the data
        // folder's update backups (REL-02), as after any other revoke.
        crate::db::upgrade::purge_backups(&state.data_dir, "Daily session boundary");
        if ended {
            state.bus.publish(Event::BrokerSessionEnded {
                reason: SessionEndReason::DailyExpiry,
            });
        }
        match written {
            Ok(_) => Ok(ended),
            Err(e) => {
                let mut pending = state.revoke_pending.lock();
                Self::mark_pending_locked(state, &mut pending, PendingRevoke::Before(boundary));
                Err(e)
            }
        }
    }
}
