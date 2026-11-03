//! Broker sign-in, session persistence, resume and revocation.
//!
//! * OAuth brokers: `start_oauth` stores a server-generated `state` (with
//!   the redirect address the authorize URL was built with) and returns the
//!   broker's authorize URL; a broker whose URL needs a call first (Dhan's
//!   consent) builds it in `Broker::begin_login`. The callback
//!   (`complete_oauth`) consumes the state (Dhan, whose redirect carries no
//!   state: the newest pending Dhan sign-in, single use, 10 minutes),
//!   exchanges the code for a token in Rust, persists it encrypted and
//!   publishes `broker.connected`. The code never reaches the frontend.
//! * Form logins: `login_with_form`, with the fields
//!   `catalog::login_fields` declares for the broker (Groww's pasted token,
//!   Kotak's mobile, TOTP and MPIN, Dhan's pasted access token), else the
//!   web's client id / PIN / TOTP names.
//! * Every successful sign-in or resume starts the session's streaming
//!   (`BrokerRuntime::activate`: master contract, feeds, order updates);
//!   `revoke` tears it down.
//! * After a password sign-in or a restart, `try_resume` brings back
//!   every stored session that may still be resumed: one issued before
//!   the last 03:00 IST boundary is revoked instead (any time for a
//!   crypto session, `catalog::has_daily_boundary`); each broker still
//!   accepts its token (a funds call, like the web), checked in parallel
//!   with at most eight in flight; each accepted one joins the store
//!   beside the live sessions, newest first (D-S3/D-S4).
//! * Logout, a broker switch and account reset call `revoke` (every
//!   session) or `revoke_broker` (one broker's), which end the session in
//!   memory before it writes the stored row, and retry that write while it
//!   fails — never over a session that is live meanwhile. The daily
//!   boundary calls `expire_before`, which ends only the sessions
//!   authenticated before it, and never a crypto session.
//! * Several brokers are logged in at once (MULTI_BROKER_PLAN Phase 1):
//!   keys are saved per broker, each signed-in (broker, account) keeps its
//!   own streaming, and the newest session is the default target the
//!   shared market feed and `/api/v1` serve (D-S2/D-S4). Signing in to one
//!   broker never ends another's session; an explicit logout ends only the
//!   named broker's (the app sign-out ends all). Every hand-over runs
//!   under one lock (`session_lock`), so two sign-ins finishing together
//!   never interleave.

use crate::brokers::{catalog, BrokerCredentials};
use crate::db::sqlite::{auth, credentials, oauth_state};
use crate::error::{AppError, Result};
use crate::events::{Event, SessionEndReason};
use crate::security::crypto::DEFAULT_ACCOUNT;
use crate::security::Secret;
use crate::session::web::random_token;
use crate::state::{AppState, BrokerSession};
use std::collections::HashMap;

mod daily_boundary;
mod pending_revoke;
mod resume;

pub use pending_revoke::PendingRevoke;

pub const OAUTH_STATE_TTL_MINUTES: i64 = 10;
/// In the data folder while an ended broker session's stored row is still
/// to be revoked, so a restart before the retry does not resume it
/// (SEC-05b). It holds what is still to be revoked (`PendingRevoke`).
pub const REVOKE_MARKER: &str = "broker-revoke.pending";

pub struct BrokerAuthService;

/// Where a broker callback came from.
#[derive(Debug, Clone, Copy)]
pub enum CallbackOrigin<'a> {
    /// The broker redirected the browser here; `session_id` is the browser
    /// session's cookie, when it was sent.
    Redirect { session_id: Option<&'a str> },
    /// The signed-in trader pasted the address into OpenAlgo.
    Manual { session_id: &'a str },
}

/// A field label inside a sentence: the first letter lowered unless the
/// first word is an abbreviation ("OTP sent to ..." stays as it is).
fn sentence_case(label: &str) -> String {
    let first = label.split_whitespace().next().unwrap_or("");
    if first.len() > 1 && first.chars().all(|c| !c.is_lowercase()) {
        return label.to_string();
    }
    let mut c = label.chars();
    match c.next() {
        Some(f) => f.to_lowercase().chain(c).collect(),
        None => String::new(),
    }
}

/// Inputs from a broker login form (web field names).
#[derive(Debug, Default, Clone)]
pub struct FormLogin {
    pub client_id: Option<String>,
    pub password: Option<Secret>,
    pub totp: Option<Secret>,
    /// Date of birth (Motilal's second factor, `DD/MM/YYYY`).
    pub dob: Option<Secret>,
}

impl FormLogin {
    /// The broker's own fields (`catalog::login_fields`) when it declares
    /// them, with required ones checked; otherwise the web's common names.
    pub fn for_broker(broker: &str, f: &HashMap<String, String>) -> Result<Self> {
        let fields = catalog::login_fields(broker);
        if fields.is_empty() {
            return Ok(Self::from_fields(f));
        }
        let mut out = FormLogin::default();
        for lf in fields {
            let pick = |name: &str| {
                f.get(name)
                    .map(|v| v.trim().to_string())
                    .filter(|v| !v.is_empty())
            };
            // Nubra's form field is `otp`, with `totp` accepted too (web
            // `request.form.get("otp") or request.form.get("totp")`).
            let v = pick(lf.name).or_else(|| {
                (broker == "nubra" && lf.name == "otp")
                    .then(|| pick("totp"))
                    .flatten()
            });
            let Some(v) = v else {
                if lf.required {
                    return Err(AppError::Validation(format!(
                        "Enter the {} to sign in.",
                        sentence_case(lf.label)
                    )));
                }
                continue;
            };
            match catalog::credential_slot(lf.name) {
                Some(catalog::CredentialSlot::ClientId) => out.client_id = Some(v),
                Some(catalog::CredentialSlot::Password) => out.password = Some(Secret::new(v)),
                Some(catalog::CredentialSlot::Totp) => out.totp = Some(Secret::new(v)),
                Some(catalog::CredentialSlot::Dob) => out.dob = Some(Secret::new(v)),
                None => tracing::warn!("Login field {} has no credential slot", lf.name),
            }
        }
        Ok(out)
    }

    pub fn from_fields(f: &HashMap<String, String>) -> Self {
        let pick = |keys: &[&str]| {
            keys.iter().find_map(|k| {
                f.get(*k)
                    .filter(|v| !v.trim().is_empty())
                    .map(|v| v.trim().to_string())
            })
        };
        FormLogin {
            client_id: pick(&["userid", "clientid", "client_id", "mobile"]),
            password: pick(&["pin", "password", "mpin"]).map(Secret::new),
            totp: pick(&["totp", "twofa", "otp"]).map(Secret::new),
            dob: None,
        }
    }
}

impl BrokerAuthService {
    /// Broker chosen in settings (web: from REDIRECT_URL).
    pub fn active_broker(state: &AppState) -> Option<String> {
        state.server_config().active_broker
    }

    fn load_credentials(
        state: &AppState,
        broker: &str,
    ) -> Result<credentials::BrokerCredentialSet> {
        let conn = state.sqlite.conn()?;
        // Multi-session: the session signs in with the default account's
        // keys until the accounts UI names one (MULTI_BROKER_PLAN D3).
        credentials::load(&conn, &state.security, broker, DEFAULT_ACCOUNT)?
            .filter(|c| !c.api_key.is_empty())
            .ok_or_else(|| {
                AppError::Validation(
                    "Add your broker API key and secret on the Broker Configuration page, then try again."
                        .into(),
                )
            })
    }

    /// The stored broker credentials as adapter input (no login secrets).
    fn stored_input(creds: &credentials::BrokerCredentialSet) -> BrokerCredentials {
        BrokerCredentials {
            api_key: creds.api_key.expose().to_string(),
            api_secret: creds.api_secret.as_ref().map(|s| s.expose().to_string()),
            client_id: creds.client_id.clone(),
            api_key_market: creds
                .api_key_market
                .as_ref()
                .map(|s| s.expose().to_string()),
            api_secret_market: creds
                .api_secret_market
                .as_ref()
                .map(|s| s.expose().to_string()),
            ..Default::default()
        }
    }

    /// Start an OAuth login: store a fresh `state` with the redirect
    /// address, return the authorize URL.
    pub async fn start_oauth(
        state: &AppState,
        broker: &str,
        session_id: Option<&str>,
    ) -> Result<String> {
        let adapter = state.brokers.adapter(broker)?;
        let creds = Self::load_credentials(state, broker)?;
        // The redirect URL saved with this broker's keys, else the automatic
        // one (`BrokerConfigService::redirect_url`).
        let redirect = creds
            .redirect_url
            .clone()
            .unwrap_or_else(|| state.server_config().redirect_url_for(broker));
        let st = random_token();
        let url = match catalog::authorize_url(broker, creds.api_key.expose(), &redirect, &st) {
            Some(a) => a.url,
            None => {
                let input = BrokerCredentials {
                    redirect_uri: Some(redirect.clone()),
                    ..Self::stored_input(&creds)
                };
                // No database connection is held across this await.
                adapter.begin_login(&input).await?.ok_or_else(|| {
                    AppError::Validation(format!(
                        "{} signs in with a form, not a browser redirect.",
                        broker
                    ))
                })?
            }
        };
        let binding = catalog::login_binding(broker, &url);
        {
            let conn = state.sqlite.conn()?;
            oauth_state::insert(
                &conn,
                oauth_state::PendingSignIn {
                    state: &st,
                    broker,
                    redirect_uri: Some(&redirect),
                    session_id,
                    binding: binding.as_deref(),
                },
                state.now(),
                chrono::Duration::minutes(OAUTH_STATE_TTL_MINUTES),
            )?;
        }
        Ok(url)
    }

    /// The pending sign-in this OpenAlgo started for `broker`, used once
    /// (consumed here), and whether it was found without a `state` (a
    /// broker that drops it, from the same browser session, within the
    /// short window).
    fn take_pending(
        state: &AppState,
        broker: &str,
        st: &str,
        origin: CallbackOrigin<'_>,
    ) -> Result<(Option<oauth_state::Pending>, bool)> {
        let now = state.now();
        let mut state_less = false;
        let pending = {
            let conn = state.sqlite.conn()?;
            match origin {
                // The broker's redirect (GET, or the XTS form POST): by
                // `state`; without it only for brokers that drop it, from
                // the same browser session, within three minutes.
                CallbackOrigin::Redirect { session_id } => {
                    if !st.is_empty() {
                        oauth_state::consume(&conn, st, broker, now)?
                    } else if let (false, Some(sid)) =
                        (catalog::callback_carries_state(broker), session_id)
                    {
                        state_less = true;
                        oauth_state::consume_latest(
                            &conn,
                            broker,
                            sid,
                            now,
                            chrono::Duration::seconds(oauth_state::STATELESS_WINDOW_SECONDS),
                        )?
                    } else {
                        None
                    }
                }
                // Pasted by the signed-in trader (CSRF-checked route): a
                // sign-in this same session started, by `state` when the
                // address has one, else its newest one for this broker.
                CallbackOrigin::Manual { session_id } => {
                    if !st.is_empty() {
                        oauth_state::consume_for_session(&conn, st, broker, session_id, now)?
                    } else {
                        oauth_state::consume_latest(
                            &conn,
                            broker,
                            session_id,
                            now,
                            chrono::Duration::minutes(OAUTH_STATE_TTL_MINUTES),
                        )?
                    }
                }
            }
        };
        Ok((pending, state_less))
    }

    /// Finish an OAuth login from the callback query parameters.
    pub async fn complete_oauth(
        state: &AppState,
        broker: &str,
        params: &HashMap<String, String>,
        origin: CallbackOrigin<'_>,
    ) -> Result<BrokerSession> {
        let st = params.get("state").cloned().unwrap_or_default();
        let (pending, state_less) = Self::take_pending(state, broker, &st, origin)?;
        // A ready token is accepted only when the signed-in trader pasted
        // the address into OpenAlgo; one arriving by redirect is ignored,
        // since nothing ties it to this trader.
        let pasted = match origin {
            CallbackOrigin::Manual { .. } => catalog::pasted_token(broker, params),
            CallbackOrigin::Redirect { .. } => None,
        };
        let Some(pending) = pending else {
            return Err(AppError::Auth(
                "This broker sign-in was not started from OpenAlgo or has expired. Start the broker login again from OpenAlgo."
                    .into(),
            ));
        };
        // A login id the callback repeats must be the one this sign-in
        // started with (Dhan's consent).
        if let Some(id) = catalog::callback_binding(broker, params) {
            if !pending.binding_matches(&id) {
                tracing::warn!(broker, "Broker sign-in refused: the consent does not match");
                return Err(AppError::Auth(
                    "This broker sign-in does not match the one started from OpenAlgo. Start the broker login again from OpenAlgo."
                        .into(),
                ));
            }
        }
        let creds = Self::load_credentials(state, broker)?;
        let expected = Self::expected_account(state, broker, &creds, state_less)?;
        if let Some((token, uid)) = pasted {
            let stored = Self::stored_input(&creds);
            let input = BrokerCredentials {
                password: Some(token),
                client_id: uid.or(stored.client_id.clone()),
                ..stored
            };
            return Self::authenticate_as(
                state,
                broker,
                input,
                expected.as_deref(),
                Self::keys_digest(&creds),
            )
            .await;
        }
        let code = catalog::extract_code(broker, params).ok_or_else(|| {
            AppError::Auth("The broker did not complete the sign-in. Try again.".into())
        })?;
        let input = BrokerCredentials {
            request_token: Some(code.clone()),
            auth_code: Some(code),
            redirect_uri: pending.redirect_uri,
            ..Self::stored_input(&creds)
        };
        Self::authenticate_as(
            state,
            broker,
            input,
            expected.as_deref(),
            Self::keys_digest(&creds),
        )
        .await
    }

    /// The broker account a sign-in must belong to, on every path: the
    /// account the stored credentials name (Dhan, the Noren family), else
    /// the account of the last session with this broker (forgotten when the
    /// trader saves that broker's settings again, to switch accounts).
    /// `None` on a first sign-in, except for a callback without `state`,
    /// which is refused then: the browser session alone does not prove the
    /// trader started it.
    fn expected_account(
        state: &AppState,
        broker: &str,
        creds: &credentials::BrokerCredentialSet,
        state_less: bool,
    ) -> Result<Option<String>> {
        if let Some(a) =
            catalog::configured_account(broker, creds.api_key.expose(), creds.client_id.as_deref())
        {
            return Ok(Some(a));
        }
        let last = {
            let conn = state.sqlite.conn()?;
            auth::last_user_id(&conn, broker, DEFAULT_ACCOUNT)?
        };
        match last {
            Some(u) => Ok(Some(u)),
            None if !state_less => Ok(None),
            // Named the way the Broker Configuration page labels the field.
            None => {
                let guide = crate::brokers::setup_guide::guide(broker);
                let wanted = if catalog::CLIENT_ID_BROKERS.contains(&broker) {
                    crate::brokers::setup_guide::KeyName::ClientId
                } else {
                    crate::brokers::setup_guide::KeyName::ApiKey
                };
                let label = guide
                    .fields
                    .iter()
                    .find(|f| f.key == wanted)
                    .map(|f| f.label)
                    .unwrap_or("client ID");
                Err(AppError::Validation(format!(
                    "Add your {} {} on the Broker Configuration page, then start the broker login again.",
                    crate::brokers::setup_guide::display_name(broker),
                    label
                )))
            }
        }
    }

    /// Form login (Angel and the other client-id/PIN/TOTP brokers).
    pub async fn login_with_form(
        state: &AppState,
        broker: &str,
        form: FormLogin,
    ) -> Result<BrokerSession> {
        if catalog::auth_type(broker) != catalog::AuthType::Form
            && catalog::login_fields(broker).is_empty()
        {
            return Err(AppError::Validation(
                "This broker signs in through the broker's own page.".into(),
            ));
        }
        let creds = Self::load_credentials(state, broker)?;
        let expected = Self::expected_account(state, broker, &creds, false)?;
        let stored = Self::stored_input(&creds);
        let input = BrokerCredentials {
            client_id: form.client_id.or(stored.client_id.clone()),
            password: form.password.map(|s| s.expose().to_string()),
            totp: form.totp.map(|s| s.expose().to_string()),
            // A form login has no OAuth code; the one extra factor (Motilal's
            // date of birth, `CredentialSlot::Dob`) travels in its place.
            auth_code: form.dob.map(|s| s.expose().to_string()),
            ..stored
        };
        Self::authenticate_as(
            state,
            broker,
            input,
            expected.as_deref(),
            Self::keys_digest(&creds),
        )
        .await
    }

    /// Prepare a form login when its page opens or the trader asks again:
    /// Definedge and Nubra send the login OTP (web GET `/<broker>/callback`,
    /// and Definedge's `resend` action). `Ok(None)` for brokers with nothing
    /// to prepare, otherwise the broker's message for the trader.
    pub async fn prepare_form_login(state: &AppState, broker: &str) -> Result<Option<String>> {
        let Some(adapter) = state.brokers.get(broker) else {
            return Ok(None);
        };
        let Some(any) = adapter.as_any() else {
            return Ok(None);
        };
        if let Some(definedge) = any.downcast_ref::<crate::brokers::definedge::DefinedgeBroker>() {
            let input = Self::stored_input(&Self::load_credentials(state, broker)?);
            // No database connection is held across this await.
            return definedge.send_otp(&input).await.map(Some);
        }
        if let Some(nubra) = any.downcast_ref::<crate::brokers::nubra::NubraBroker>() {
            let input = Self::stored_input(&Self::load_credentials(state, broker)?);
            return nubra.send_login_otp(&input).await.map(Some);
        }
        Ok(None)
    }

    /// Samco static IP check (web GET `/samco/ip-status`):
    /// `(http status, body)` with the web's success and error shapes.
    pub async fn samco_ip_status(state: &AppState) -> (u16, serde_json::Value) {
        use crate::brokers::samco::{self, SamcoBroker};
        let session = state
            .get_broker_session()
            .filter(|s| s.broker_id == "samco");
        let adapter = state.brokers.get("samco");
        let samco = adapter
            .as_ref()
            .and_then(|a| a.as_any())
            .and_then(|a| a.downcast_ref::<SamcoBroker>());
        let (Some(session), Some(samco)) = (session, samco) else {
            return samco::ip_status_error(samco::IP_STATUS_NOT_CONNECTED);
        };
        let auth = crate::brokers::types::AuthToken::new(session.auth_token.expose());
        match samco.ip_status(&auth).await {
            Ok(s) => (200, s.to_json()),
            Err(e) => {
                tracing::warn!("Samco IP status check failed: {}", e.code());
                samco::ip_status_error(&e.client_message())
            }
        }
    }

    /// Sign in; when `expected` names an account, a session for any other
    /// account is refused before anything is stored or replaced.
    async fn authenticate_as(
        state: &AppState,
        broker_id: &str,
        input: BrokerCredentials,
        expected: Option<&str>,
        keys: [u8; 32],
    ) -> Result<BrokerSession> {
        let name = crate::brokers::setup_guide::display_name(broker_id);
        let broker = state.brokers.adapter(broker_id)?;
        // No database connection is held across this await.
        let resp = broker.authenticate(input).await?;
        if let Some(want) = expected {
            if !resp.user_id.trim().eq_ignore_ascii_case(want.trim()) {
                tracing::warn!(
                    broker = broker_id,
                    "Broker sign-in refused: it belongs to a different account than the one configured"
                );
                return Err(AppError::Auth(format!(
                    "This sign-in is for a different {} account than the one set up in OpenAlgo. Nothing was changed. Log in with your own account; to switch accounts, log out and save the broker settings again first.",
                    name
                )));
            }
        }
        let session = BrokerSession {
            broker_id: broker_id.to_string(),
            // The sign-in used the default account's keys (D3); a named
            // account arrives with the accounts UI.
            account_id: DEFAULT_ACCOUNT.to_string(),
            auth_token: Secret::new(resp.auth_token),
            feed_token: resp.feed_token.map(Secret::new),
            user_id: resp.user_id,
            user_name: resp.user_name,
            authenticated_at: state.now(),
        };
        // One broker at a time: another broker's live session ends first
        // (memory, streaming, caches, then its stored row), and the
        // hand-over runs under the session lock, so a second sign-in that
        // completes at the same moment waits and then ends this one.
        let held = Self::session_lock(state).await;
        // The keys this sign-in used must still be the saved ones: a save
        // or a forget that finished while the broker was answering wins,
        // and nothing made with the old keys is stored.
        let unchanged = Self::load_credentials(state, broker_id)
            .ok()
            .map(|c| Self::keys_digest(&c))
            == Some(keys);
        if !unchanged {
            tracing::info!(
                "Sign-in to {} not stored: its keys changed while it ran",
                broker_id
            );
            return Err(AppError::Validation(format!(
                "Your {} keys changed while this login was in progress. Nothing was stored. Log in again.",
                name
            )));
        }
        // A live session of another broker is not ended: several brokers
        // are logged in at once (MULTI_BROKER_PLAN), and this sign-in is
        // the newest — the default target they trade through.
        Self::persist(state, &session)?;
        Self::activate(state, &session).await;
        drop(held);
        Ok(session)
    }

    /// A digest of the saved keys a sign-in starts with, kept in memory for
    /// that sign-in only (never stored or logged), so one that completes
    /// after the trader changed or forgot those keys is refused.
    fn keys_digest(creds: &credentials::BrokerCredentialSet) -> [u8; 32] {
        use sha2::{Digest, Sha256};
        let mut h = Sha256::new();
        for part in [
            Some(creds.api_key.expose()),
            creds.api_secret.as_ref().map(|s| s.expose()),
            creds.api_key_market.as_ref().map(|s| s.expose()),
            creds.api_secret_market.as_ref().map(|s| s.expose()),
            creds.client_id.as_deref(),
        ] {
            match part {
                Some(p) => {
                    h.update([1u8]);
                    h.update((p.len() as u64).to_le_bytes());
                    h.update(p.as_bytes());
                }
                None => h.update([0u8]),
            }
        }
        h.finalize().into()
    }

    /// Hold the one-session lock (`AppState::session_switch`) while a
    /// session is ended, started or replaced.
    pub async fn session_lock(state: &AppState) -> tokio::sync::MutexGuard<'_, ()> {
        state.session_switch.lock().await
    }

    /// Start the session's streaming (master contract, feeds, order
    /// updates) on the entry's own runtime (D-S2); the newest session is
    /// the default target the shared market feed and the bridge serve.
    async fn activate(state: &AppState, session: &BrokerSession) {
        match state.arc() {
            Some(ctx) => {
                let is_default = state.is_default_session(&session.broker_id, &session.account_id);
                match state.runtime_for(&session.broker_id, &session.account_id) {
                    Some(runtime) => runtime.activate(&ctx, session, is_default).await,
                    None => tracing::error!(
                        "Broker streaming could not start: no session entry for {}",
                        session.broker_id
                    ),
                }
            }
            None => tracing::error!("Broker streaming could not start: the app is shutting down"),
        }
    }

    /// Store and activate a session, then announce it.
    pub fn persist(state: &AppState, s: &BrokerSession) -> Result<()> {
        {
            // A revoke still pending is written first, in the same
            // transaction and under the same hold, so a later retry never
            // revokes this new session's row. The row and the active broker
            // change together or not at all.
            let mut pending = state.revoke_pending.lock();
            let was_pending = Self::pending_locked(state, &pending);
            // The new session is live from this commit on: the pending
            // revoke (and any later retry) must not reach its row.
            let mut live: Vec<(String, String)> = state
                .broker_sessions
                .list()
                .into_iter()
                .map(|s| (s.broker_id, s.account_id))
                .collect();
            live.push((s.broker_id.clone(), s.account_id.clone()));
            let mut conn = state.sqlite.conn()?;
            let tx = conn.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
            if let Some(what) = was_pending {
                what.write(&tx, &live)?;
            }
            auth::upsert(
                &tx,
                &state.security,
                &auth::StoredBrokerSession {
                    broker_id: s.broker_id.clone(),
                    // Single-session core: every session is the default
                    // account until the runtime carries one (D3).
                    account_id: DEFAULT_ACCOUNT.to_string(),
                    auth_token: s.auth_token.clone(),
                    feed_token: s.feed_token.clone(),
                    user_id: Some(s.user_id.clone()),
                    user_name: s.user_name.clone(),
                    authenticated_at: s.authenticated_at,
                },
            )?;
            crate::config::save(
                &tx,
                &crate::config::ServerConfigUpdate {
                    active_broker: Some(s.broker_id.clone()),
                    ..Default::default()
                },
            )?;
            tx.commit()?;
            Self::clear_pending_locked(state, &mut pending);
        }
        let _ = state.reload_config();
        // A second broker's session stays live beside it (D-S3); this one
        // is the newest, so it is the default target (D-S4).
        state.broker_sessions.upsert(s.clone());
        state.api_keys.clear();
        state.bus.publish(Event::BrokerConnected {
            broker: s.broker_id.clone(),
            user_id: Some(s.user_id.clone()),
        });
        tracing::info!("Broker session started for {}", s.broker_id);
        Ok(())
    }

    /// End every broker session (the app sign-out, a fresh start, an
    /// account reset). Memory goes first and the stored rows last, as on
    /// the web (which drops its cache before the database write): each
    /// entry's streaming (feeds closed, subscriptions cleared, owned tasks
    /// aborted, adapter pollers stopped), the API-key cache and the symbol
    /// cache, then subscribers are told, then the rows are revoked.
    ///
    /// The sessions are over in this process whatever the database does. If
    /// the rows cannot be written, the revoke stays pending, in memory and
    /// in a marker file in the data folder that the next start reads
    /// (`REVOKE_MARKER`, SEC-05b): no stored session is resumed until the
    /// session poll, a resume or the next sign-in writes it
    /// (`retry_pending_revoke`), in this run or the next — and a write
    /// never reaches a row whose session is live meanwhile. An `Err` means
    /// only that a stored row is still to be revoked.
    pub async fn revoke(state: &AppState, reason: SessionEndReason) -> Result<()> {
        let held = Self::session_lock(state).await;
        Self::revoke_all_held(state, reason, &held).await
    }

    /// End one broker's session (its default account, Phase 1) while the
    /// others stay live (D-S3). The newest entry's teardown owns the shared
    /// sockets; when that is the one ending, the newest survivor is
    /// promoted to the default (D-S4).
    pub async fn revoke_broker(
        state: &AppState,
        broker: &str,
        reason: SessionEndReason,
    ) -> Result<()> {
        let held = Self::session_lock(state).await;
        Self::revoke_broker_held(state, broker, reason, &held).await
    }

    /// `revoke` for a caller that already holds the session lock
    /// (`session_lock`); the guard is the proof.
    pub async fn revoke_all_held(
        state: &AppState,
        reason: SessionEndReason,
        _held: &tokio::sync::MutexGuard<'_, ()>,
    ) -> Result<()> {
        // Marked before memory is cleared, so a sign-in racing this call
        // cannot resume a row being revoked, and recorded on disk, so a
        // stop or a restart before the rows are written cannot bring them
        // back.
        {
            let mut pending = state.revoke_pending.lock();
            Self::mark_pending_locked(state, &mut pending, PendingRevoke::All);
        }
        // Every live entry ends; the newest's teardown also owns the shared
        // market and depth sockets (D-S2).
        let entries = state.broker_sessions.entries();
        let newest = entries
            .iter()
            .max_by(|a, b| a.session.authenticated_at.cmp(&b.session.authenticated_at));
        for e in &entries {
            let is_default = newest.is_some_and(|n| {
                n.session.broker_id == e.session.broker_id
                    && n.session.account_id == e.session.account_id
            });
            if state
                .broker_sessions
                .take_entry(&e.session.broker_id, &e.session.account_id)
                .is_some()
            {
                e.runtime.teardown(state, is_default).await;
            }
        }
        state.api_keys.clear();
        state.clear_symbol_cache();
        state.bus.publish(Event::BrokerSessionEnded { reason });
        let written = Self::retry_pending_revoke(state);
        // No copy of the ended sessions may outlive them in the data
        // folder's update backups (REL-02 security review).
        crate::db::upgrade::purge_backups(&state.data_dir, "Broker session ended");
        written
    }

    /// `revoke_broker` for a caller that already holds the session lock.
    pub async fn revoke_broker_held(
        state: &AppState,
        broker: &str,
        reason: SessionEndReason,
        _held: &tokio::sync::MutexGuard<'_, ()>,
    ) -> Result<()> {
        // Marked before memory is cleared, so a sign-in racing this call
        // cannot resume the row being revoked, and recorded on disk, scoped
        // to this broker: a failed write must never grow into a sibling's
        // stored session (SEC-05b, D-S3).
        {
            let mut pending = state.revoke_pending.lock();
            Self::mark_pending_locked(
                state,
                &mut pending,
                PendingRevoke::Pair(
                    broker.to_string(),
                    crate::security::crypto::DEFAULT_ACCOUNT.to_string(),
                ),
            );
        }
        // Phase 1: a session signs in with the default account's keys (D3);
        // the accounts UI names others.
        let account = crate::security::crypto::DEFAULT_ACCOUNT;
        let was_default = state.broker_sessions.is_newest(broker, account);
        if let Some(entry) = state.broker_sessions.take_entry(broker, account) {
            entry.runtime.teardown(state, was_default).await;
        }
        // The default socket had this session's feed: the newest survivor
        // takes it (D-S4), or nothing does.
        if was_default && !state.broker_sessions.is_empty() {
            if let Some(ctx) = state.arc() {
                crate::services::broker_runtime::promote_default(&ctx).await;
            }
        }
        state.api_keys.clear();
        if state.broker_sessions.is_empty() {
            state.clear_symbol_cache();
        }
        state.bus.publish(Event::BrokerSessionEnded { reason });
        let written = Self::retry_pending_revoke(state);
        // No copy of the ended session may outlive it in the data folder's
        // update backups (REL-02 security review).
        crate::db::upgrade::purge_backups(&state.data_dir, "Broker session ended");
        written
    }

    /// Whether an ended session's stored row is still to be revoked, in
    /// this run or one before it (the marker file).
    pub fn revoke_is_pending(state: &AppState) -> bool {
        let pending = state.revoke_pending.lock();
        Self::pending_locked(state, &pending).is_some()
    }

    /// Write a pending revoke; a no-op when none is pending. Run by
    /// `revoke` itself, by the session poll every 30 seconds (which also
    /// finishes a revoke a previous run left pending) and before a resume.
    /// A session that is live meanwhile is never revoked by it.
    pub fn retry_pending_revoke(state: &AppState) -> Result<()> {
        let mut pending = state.revoke_pending.lock();
        let Some(what) = Self::pending_locked(state, &pending) else {
            return Ok(());
        };
        let live: Vec<(String, String)> = state
            .broker_sessions
            .list()
            .into_iter()
            .map(|s| (s.broker_id, s.account_id))
            .collect();
        let conn = state.sqlite.conn()?;
        what.write(&conn, &live)?;
        Self::clear_pending_locked(state, &mut pending);
        Ok(())
    }

    /// Forget a pending revoke because the stored sessions themselves are
    /// gone (account reset deletes them).
    pub fn clear_pending_revoke(state: &AppState) {
        let mut pending = state.revoke_pending.lock();
        Self::clear_pending_locked(state, &mut pending);
    }

    fn revoke_marker(state: &AppState) -> std::path::PathBuf {
        state.data_dir.join(REVOKE_MARKER)
    }

    /// What is still to be revoked: this run's and the marker's (a
    /// previous run's), the wider of the two.
    fn pending_locked(state: &AppState, pending: &Option<PendingRevoke>) -> Option<PendingRevoke> {
        let on_disk = match std::fs::read_to_string(Self::revoke_marker(state)) {
            Ok(text) => Some(PendingRevoke::decode(&text)),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
            // Present but unreadable: fail closed.
            Err(_) => Some(PendingRevoke::All),
        };
        match (pending.clone(), on_disk) {
            (Some(a), Some(b)) => Some(a.merge(b)),
            (a, b) => a.or(b),
        }
    }

    fn mark_pending_locked(
        state: &AppState,
        pending: &mut Option<PendingRevoke>,
        what: PendingRevoke,
    ) {
        let what = match Self::pending_locked(state, pending) {
            Some(p) => p.merge(what),
            None => what,
        };
        *pending = Some(what.clone());
        let marker = Self::revoke_marker(state);
        if let Err(e) =
            crate::security::fsperm::write_private_file(&marker, what.encode().as_bytes())
        {
            // A wider revoke never leaves a narrower one on disk: a
            // boundary's marker is emptied in place (empty means every
            // session), which needs no free space on a full disk.
            if what == PendingRevoke::All {
                if let Ok(f) = std::fs::OpenOptions::new().write(true).open(&marker) {
                    let _ = f.set_len(0).and_then(|()| f.sync_all());
                }
            }
            // Memory still refuses a resume in this run; only a restart
            // before the row is written would lose the intent.
            tracing::warn!("Could not record the broker sign-out on disk: {}", e);
        }
    }

    fn clear_pending_locked(state: &AppState, pending: &mut Option<PendingRevoke>) {
        *pending = None;
        let marker = Self::revoke_marker(state);
        if marker.exists() {
            if let Err(e) = std::fs::remove_file(&marker) {
                // Fail closed: the next start revokes the stored rows again.
                tracing::warn!("Could not remove the broker sign-out record: {}", e);
            }
        }
    }

    /// Whether the stored broker session ended (revoked/expired) rather than
    /// never having existed.
    pub fn had_revoked_session(state: &AppState) -> bool {
        state
            .sqlite
            .conn()
            .ok()
            .and_then(|c| auth::has_revoked(&c).ok())
            .unwrap_or(false)
    }
}

#[cfg(test)]
mod form_tests {
    use super::*;

    #[test]
    fn form_carries_the_date_of_birth() {
        let f: HashMap<String, String> = [
            ("userid", "AB1"),
            ("password", "pw"),
            ("dob", " 18/10/1988 "),
            ("totp", "123456"),
        ]
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
        // Through Motilal's declared fields (`catalog::login_fields`).
        let form = FormLogin::for_broker("motilal", &f).unwrap();
        assert_eq!(form.client_id.as_deref(), Some("AB1"));
        assert_eq!(form.password.as_ref().map(|s| s.expose()), Some("pw"));
        assert_eq!(form.dob.as_ref().map(|s| s.expose()), Some("18/10/1988"));
        assert_eq!(form.totp.as_ref().map(|s| s.expose()), Some("123456"));
        // The date of birth is required; the TOTP is not.
        let mut no_dob = f.clone();
        no_dob.remove("dob");
        assert!(FormLogin::for_broker("motilal", &no_dob).is_err());
        let mut no_totp = f.clone();
        no_totp.remove("totp");
        assert!(FormLogin::for_broker("motilal", &no_totp)
            .unwrap()
            .totp
            .is_none());
        // Brokers without the field never pick it up.
        assert!(FormLogin::from_fields(&f).dob.is_none());
    }
}
