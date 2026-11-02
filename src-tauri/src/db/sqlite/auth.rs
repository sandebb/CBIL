//! Stored broker session (web `auth` table equivalent).
//!
//! One row per broker account (MULTI_BROKER_PLAN D3). Tokens are
//! AES-256-GCM encrypted with AAD bound to the broker id and the account
//! row (the legacy account keeps the v1 binding, so pre-multi-account
//! ciphertexts still decrypt). A logout or the 03:00 IST boundary revokes
//! the row (`is_revoked = 1`) and blanks the ciphertexts, so "expired" can
//! be told apart from "never connected".

use crate::error::{AppError, Result};
use crate::security::crypto::Aad;
use crate::security::{Secret, SecurityManager};
use chrono::{DateTime, Utc};
use rusqlite::{params, Connection, OptionalExtension};

#[derive(Debug, Clone)]
pub struct StoredBrokerSession {
    pub broker_id: String,
    /// The broker account the session is signed in with. Pre-multi-account
    /// rows are the legacy single account (`DEFAULT_ACCOUNT`).
    pub account_id: String,
    pub auth_token: Secret,
    pub feed_token: Option<Secret>,
    pub user_id: Option<String>,
    pub user_name: Option<String>,
    pub authenticated_at: DateTime<Utc>,
}

fn aad(column: &str, broker: &str, account: &str) -> Aad {
    Aad::for_account("auth", column, broker, account)
}

pub fn upsert(
    conn: &Connection,
    security: &SecurityManager,
    s: &StoredBrokerSession,
) -> Result<()> {
    let (auth_ct, auth_nonce) = security.encrypt(
        s.auth_token.expose(),
        &aad("auth_token", &s.broker_id, &s.account_id),
    )?;
    let (feed_ct, feed_nonce) = match &s.feed_token {
        Some(f) => {
            let (c, n) =
                security.encrypt(f.expose(), &aad("feed_token", &s.broker_id, &s.account_id))?;
            (Some(c), Some(n))
        }
        None => (None, None),
    };
    conn.execute(
        "INSERT INTO auth (broker_id, account_id, auth_token_encrypted, auth_token_nonce,
             feed_token_encrypted, feed_token_nonce, user_id, user_name, authenticated_at,
             is_revoked)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, 0)
         ON CONFLICT(broker_id, account_id) DO UPDATE SET
             auth_token_encrypted = excluded.auth_token_encrypted,
             auth_token_nonce = excluded.auth_token_nonce,
             feed_token_encrypted = excluded.feed_token_encrypted,
             feed_token_nonce = excluded.feed_token_nonce,
             user_id = excluded.user_id,
             user_name = excluded.user_name,
             authenticated_at = excluded.authenticated_at,
             is_revoked = 0,
             updated_at = datetime('now')",
        params![
            s.broker_id,
            s.account_id,
            auth_ct,
            auth_nonce,
            feed_ct,
            feed_nonce,
            s.user_id,
            s.user_name,
            s.authenticated_at.to_rfc3339(),
        ],
    )?;
    Ok(())
}

/// (broker, account, auth ciphertext + nonce, feed ciphertext + nonce,
/// user id, user name, authenticated at)
type RawRow = (
    String,
    String,
    String,
    String,
    Option<String>,
    Option<String>,
    Option<String>,
    Option<String>,
    Option<String>,
);

/// All stored sessions that may still be resumed, newest first. Rows that
/// no longer decrypt (key rotated, tampered) or whose time cannot be read
/// are revoked and skipped.
pub fn list_active(
    conn: &Connection,
    security: &SecurityManager,
) -> Result<Vec<StoredBrokerSession>> {
    let rows: Vec<RawRow> = {
        let mut stmt = conn.prepare(
            "SELECT broker_id, account_id, auth_token_encrypted, auth_token_nonce,
                    feed_token_encrypted, feed_token_nonce, user_id, user_name, authenticated_at
             FROM auth WHERE is_revoked = 0 AND auth_token_encrypted != ''
             ORDER BY authenticated_at DESC, id DESC",
        )?;
        let r = stmt.query_map([], |r| {
            Ok((
                r.get(0)?,
                r.get(1)?,
                r.get(2)?,
                r.get(3)?,
                r.get(4)?,
                r.get(5)?,
                r.get(6)?,
                r.get(7)?,
                r.get(8)?,
            ))
        })?;
        r.collect::<std::result::Result<Vec<_>, _>>()?
    };
    let mut sessions = Vec::with_capacity(rows.len());
    for (broker, account, ct, nonce, fct, fnonce, user_id, user_name, at) in rows {
        let Some(authenticated_at) = at
            .as_deref()
            .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
            .map(|d| d.with_timezone(&Utc))
        else {
            revoke(conn, &broker, &account)?;
            continue;
        };
        let auth_token = match security.decrypt(&ct, &nonce, &aad("auth_token", &broker, &account))
        {
            Ok(t) => t,
            Err(AppError::Locked) => return Err(AppError::Locked),
            Err(_) => {
                tracing::warn!(
                    "Stored broker session for {} ({}) could not be read; revoking",
                    broker,
                    account
                );
                revoke(conn, &broker, &account)?;
                continue;
            }
        };
        let feed_token = match (fct, fnonce) {
            (Some(c), Some(n)) if !c.is_empty() => security
                .decrypt(&c, &n, &aad("feed_token", &broker, &account))
                .ok(),
            _ => None,
        };
        sessions.push(StoredBrokerSession {
            broker_id: broker,
            account_id: account,
            auth_token,
            feed_token,
            user_id,
            user_name,
            authenticated_at,
        });
    }
    Ok(sessions)
}

/// The most recent non-revoked session, decrypted.
pub fn latest_active(
    conn: &Connection,
    security: &SecurityManager,
) -> Result<Option<StoredBrokerSession>> {
    Ok(list_active(conn, security)?.into_iter().next())
}

/// Revoke the stored session of one broker account.
pub fn revoke(conn: &Connection, broker_id: &str, account_id: &str) -> Result<()> {
    conn.execute(
        "UPDATE auth SET is_revoked = 1, auth_token_encrypted = '', auth_token_nonce = '',
            feed_token_encrypted = NULL, feed_token_nonce = NULL, updated_at = datetime('now')
         WHERE broker_id = ?1 AND account_id = ?2",
        params![broker_id, account_id],
    )?;
    Ok(())
}

pub fn revoke_all(conn: &Connection) -> Result<usize> {
    Ok(conn.execute(
        "UPDATE auth SET is_revoked = 1, auth_token_encrypted = '', auth_token_nonce = '',
            feed_token_encrypted = NULL, feed_token_nonce = NULL, updated_at = datetime('now')
         WHERE is_revoked = 0",
        [],
    )?)
}

/// Revoke the stored sessions authenticated before `boundary` (the daily
/// boundary, SES-02), except those `keep` names (continuous crypto
/// sessions, SES-01). `keep` sees (broker, account). A row whose time
/// cannot be read is revoked. Returns how many were revoked.
pub fn revoke_before(
    conn: &Connection,
    boundary: DateTime<Utc>,
    keep: impl Fn(&str, &str) -> bool,
) -> Result<usize> {
    let rows: Vec<(String, String, Option<String>)> = conn
        .prepare("SELECT broker_id, account_id, authenticated_at FROM auth WHERE is_revoked = 0")?
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
        .collect::<std::result::Result<_, _>>()?;
    let mut n = 0;
    for (broker, account, at) in rows {
        let before = at
            .and_then(|a| DateTime::parse_from_rfc3339(&a).ok())
            .is_none_or(|a| a.with_timezone(&Utc) < boundary);
        if before && !keep(&broker, &account) {
            revoke(conn, &broker, &account)?;
            n += 1;
        }
    }
    Ok(n)
}

/// The broker account id of the last session with `(broker_id, account_id)`,
/// revoked or not (the account a new sign-in is expected to belong to).
pub fn last_user_id(
    conn: &Connection,
    broker_id: &str,
    account_id: &str,
) -> Result<Option<String>> {
    Ok(conn
        .query_row(
            "SELECT user_id FROM auth WHERE broker_id = ?1 AND account_id = ?2",
            params![broker_id, account_id],
            |r| r.get::<_, Option<String>>(0),
        )
        .optional()?
        .flatten()
        .filter(|u| !u.trim().is_empty()))
}

/// Forget the account an ended session with `(broker_id, account_id)` was
/// for, so the next sign-in may be for another account (the trader saved
/// that key set again). A live session keeps its account.
pub fn forget_account(conn: &Connection, broker_id: &str, account_id: &str) -> Result<()> {
    conn.execute(
        "UPDATE auth SET user_id = NULL WHERE broker_id = ?1 AND account_id = ?2 AND is_revoked = 1",
        params![broker_id, account_id],
    )?;
    Ok(())
}

/// Whether a row exists but is revoked (for "expired" vs "never connected").
pub fn has_revoked(conn: &Connection) -> Result<bool> {
    let n: i64 = conn.query_row("SELECT COUNT(*) FROM auth WHERE is_revoked = 1", [], |r| {
        r.get(0)
    })?;
    Ok(n > 0)
}

/// Remove the stored session rows of one broker (all its accounts), as when
/// the broker is forgotten.
pub fn delete(conn: &Connection, broker_id: &str) -> Result<()> {
    conn.execute("DELETE FROM auth WHERE broker_id = ?1", [broker_id])?;
    Ok(())
}

/// Remove the stored session of one broker account.
pub fn delete_account(conn: &Connection, broker_id: &str, account_id: &str) -> Result<()> {
    conn.execute(
        "DELETE FROM auth WHERE broker_id = ?1 AND account_id = ?2",
        params![broker_id, account_id],
    )?;
    Ok(())
}

pub fn delete_all(conn: &Connection) -> Result<()> {
    conn.execute("DELETE FROM auth", params![])?;
    Ok(())
}
