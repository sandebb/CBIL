//! Migration 102: the multi-account dimension (MULTI_BROKER_PLAN, D3).
//!
//! Adds `account_id` (defaulting to `'default'`) to `auth` and
//! `broker_credentials`, so several credential sets and several live
//! sessions can coexist per broker. Both tables are rebuilt to replace the
//! single-broker uniqueness with `UNIQUE(broker_id, account_id)`, and
//! `settings` gains `active_account` (the default target beside the
//! existing `active_broker`).
//!
//! A rebuild rather than in-place `ALTER`, because SQLite cannot drop the
//! unique constraint on `broker_id`. Every pre-102 row belongs to the one
//! legacy account, carried over as `'default'` — the account that keeps
//! the v1 AAD binding (`Aad::for_account`), so every existing ciphertext
//! still decrypts and nothing is re-encrypted here.

use super::migrations::{add_column, column_exists};
use crate::error::Result;
use rusqlite::Connection;

pub fn migrate(conn: &Connection) -> Result<()> {
    rebuild_auth(conn)?;
    rebuild_broker_credentials(conn)?;
    add_column(conn, "settings", "active_account", "TEXT")?;
    Ok(())
}

fn rebuild_auth(conn: &Connection) -> Result<()> {
    if column_exists(conn, "auth", "account_id")? {
        return Ok(());
    }
    conn.execute_batch(
        "DROP TABLE IF EXISTS auth_next;
         CREATE TABLE auth_next (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             broker_id TEXT NOT NULL,
             account_id TEXT NOT NULL DEFAULT 'default',
             auth_token_encrypted TEXT NOT NULL,
             feed_token_encrypted TEXT,
             auth_token_nonce TEXT NOT NULL,
             created_at TEXT NOT NULL DEFAULT (datetime('now')),
             updated_at TEXT NOT NULL DEFAULT (datetime('now')),
             feed_token_nonce TEXT,
             user_id TEXT,
             user_name TEXT,
             authenticated_at TEXT,
             is_revoked INTEGER NOT NULL DEFAULT 0,
             UNIQUE(broker_id, account_id)
         );
         INSERT INTO auth_next (
             id, broker_id, account_id, auth_token_encrypted, feed_token_encrypted,
             auth_token_nonce, created_at, updated_at, feed_token_nonce,
             user_id, user_name, authenticated_at, is_revoked
         )
         SELECT
             id, broker_id, 'default', auth_token_encrypted, feed_token_encrypted,
             auth_token_nonce, created_at, updated_at, feed_token_nonce,
             user_id, user_name, authenticated_at, COALESCE(is_revoked, 0)
         FROM auth;
         DROP TABLE auth;
         ALTER TABLE auth_next RENAME TO auth;",
    )?;
    Ok(())
}

fn rebuild_broker_credentials(conn: &Connection) -> Result<()> {
    if column_exists(conn, "broker_credentials", "account_id")? {
        return Ok(());
    }
    conn.execute_batch(
        "DROP TABLE IF EXISTS broker_credentials_next;
         CREATE TABLE broker_credentials_next (
             broker_id TEXT NOT NULL,
             account_id TEXT NOT NULL DEFAULT 'default',
             api_key_encrypted TEXT NOT NULL,
             api_key_nonce TEXT NOT NULL,
             api_secret_encrypted TEXT,
             api_secret_nonce TEXT,
             client_id TEXT,
             created_at TEXT NOT NULL DEFAULT (datetime('now')),
             updated_at TEXT NOT NULL DEFAULT (datetime('now')),
             api_key_market_encrypted TEXT,
             api_key_market_nonce TEXT,
             api_secret_market_encrypted TEXT,
             api_secret_market_nonce TEXT,
             redirect_url TEXT,
             PRIMARY KEY (broker_id, account_id)
         );
         INSERT INTO broker_credentials_next (
             broker_id, account_id, api_key_encrypted, api_key_nonce,
             api_secret_encrypted, api_secret_nonce, client_id,
             created_at, updated_at, api_key_market_encrypted, api_key_market_nonce,
             api_secret_market_encrypted, api_secret_market_nonce, redirect_url
         )
         SELECT
             broker_id, 'default', api_key_encrypted, api_key_nonce,
             api_secret_encrypted, api_secret_nonce, client_id,
             created_at, updated_at, api_key_market_encrypted, api_key_market_nonce,
             api_secret_market_encrypted, api_secret_market_nonce, redirect_url
         FROM broker_credentials;
         DROP TABLE broker_credentials;
         ALTER TABLE broker_credentials_next RENAME TO broker_credentials;",
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use crate::db::sqlite::auth::{self, StoredBrokerSession};
    use crate::db::sqlite::credentials::{self, CredentialUpdate};
    use crate::db::sqlite::migrations;
    use crate::security::crypto::{Aad, DEFAULT_ACCOUNT};
    use crate::security::SecurityManager;
    use chrono::{DateTime, Utc};
    use rusqlite::{params, Connection};

    /// Build a connection at the pre-102 schema: legacy tables (001-036)
    /// plus the columns added by migrations 038, 041, 042, and 080 on
    /// `auth`, `broker_credentials`, and `settings`.
    fn legacy_pre_102_conn(dir: &tempfile::TempDir) -> Connection {
        let conn = Connection::open(dir.path().join("openalgo.db")).unwrap();
        migrations::run_legacy_schema(&conn).unwrap();
        for (table, col, decl) in [
            ("auth", "user_id", "TEXT"),
            ("auth", "user_name", "TEXT"),
            ("auth", "authenticated_at", "TEXT"),
            ("auth", "is_revoked", "INTEGER NOT NULL DEFAULT 0"),
            ("broker_credentials", "api_key_market_encrypted", "TEXT"),
            ("broker_credentials", "api_key_market_nonce", "TEXT"),
            ("broker_credentials", "api_secret_market_encrypted", "TEXT"),
            ("broker_credentials", "api_secret_market_nonce", "TEXT"),
            ("broker_credentials", "redirect_url", "TEXT"),
            ("settings", "http_port", "INTEGER"),
            ("settings", "ws_port", "INTEGER"),
            ("settings", "bind_host", "TEXT"),
            ("settings", "active_broker", "TEXT"),
            ("settings", "redirect_url", "TEXT"),
            ("settings", "host_server", "TEXT"),
            ("settings", "websocket_url", "TEXT"),
            ("settings", "ngrok_allow", "INTEGER NOT NULL DEFAULT 0"),
        ] {
            migrations::add_column(&conn, table, col, decl).unwrap();
        }
        conn
    }

    fn migrated_setup() -> (tempfile::TempDir, Connection, SecurityManager) {
        let dir = tempfile::tempdir().unwrap();
        let conn = legacy_pre_102_conn(&dir);
        super::migrate(&conn).unwrap();
        let sec = SecurityManager::for_tests();
        (dir, conn, sec)
    }

    fn ts(rfc3339: &str) -> DateTime<Utc> {
        DateTime::parse_from_rfc3339(rfc3339)
            .unwrap()
            .with_timezone(&Utc)
    }

    fn session(broker: &str, account: &str, token: &str, at: &str) -> StoredBrokerSession {
        StoredBrokerSession {
            broker_id: broker.to_string(),
            account_id: account.to_string(),
            auth_token: token.into(),
            feed_token: Some(format!("feed-{}", token).into()),
            user_id: Some(format!("uid-{}-{}", broker, account)),
            user_name: Some(format!("User {} {}", broker, account)),
            authenticated_at: ts(at),
        }
    }

    #[test]
    fn a_legacy_database_gains_a_default_account_on_both_tables() {
        let dir = tempfile::tempdir().unwrap();
        let conn = legacy_pre_102_conn(&dir);

        conn.execute(
            "INSERT INTO broker_credentials (
                broker_id, api_key_encrypted, api_key_nonce,
                api_secret_encrypted, api_secret_nonce, client_id
            ) VALUES ('zerodha', 'x', 'y', NULL, NULL, 'cid')",
            [],
        )
        .unwrap();

        conn.execute(
            "INSERT INTO auth (
                broker_id, auth_token_encrypted, auth_token_nonce,
                feed_token_encrypted, feed_token_nonce,
                user_id, user_name, authenticated_at, is_revoked
            ) VALUES (
                'zerodha', 'tok_ct', 'tok_n',
                'feed_ct', 'feed_n',
                'ZA1234', 'Trader One', '2026-11-02T04:00:00Z', 0
            )",
            [],
        )
        .unwrap();

        super::migrate(&conn).unwrap();

        type CredRow = (
            String,
            String,
            String,
            String,
            Option<String>,
            Option<String>,
            Option<String>,
        );
        let cred: CredRow = conn
            .query_row(
                "SELECT broker_id, account_id, api_key_encrypted, api_key_nonce,
                        api_secret_encrypted, api_secret_nonce, client_id
                 FROM broker_credentials WHERE broker_id = 'zerodha'",
                [],
                |r| {
                    Ok((
                        r.get(0)?,
                        r.get(1)?,
                        r.get(2)?,
                        r.get(3)?,
                        r.get(4)?,
                        r.get(5)?,
                        r.get(6)?,
                    ))
                },
            )
            .unwrap();
        assert_eq!(
            cred,
            (
                "zerodha".to_string(),
                "default".to_string(),
                "x".to_string(),
                "y".to_string(),
                None,
                None,
                Some("cid".to_string()),
            )
        );

        type AuthRow = (
            String,
            String,
            String,
            String,
            Option<String>,
            Option<String>,
            Option<String>,
            Option<String>,
            Option<String>,
            i64,
        );
        let auth_row: AuthRow = conn
            .query_row(
                "SELECT broker_id, account_id, auth_token_encrypted, auth_token_nonce,
                        feed_token_encrypted, feed_token_nonce,
                        user_id, user_name, authenticated_at, is_revoked
                 FROM auth WHERE broker_id = 'zerodha'",
                [],
                |r| {
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
                        r.get(9)?,
                    ))
                },
            )
            .unwrap();
        assert_eq!(
            auth_row,
            (
                "zerodha".to_string(),
                "default".to_string(),
                "tok_ct".to_string(),
                "tok_n".to_string(),
                Some("feed_ct".to_string()),
                Some("feed_n".to_string()),
                Some("ZA1234".to_string()),
                Some("Trader One".to_string()),
                Some("2026-11-02T04:00:00Z".to_string()),
                0,
            )
        );

        assert!(migrations::column_exists(&conn, "settings", "active_account").unwrap());
        let active_account: Option<String> = conn
            .query_row(
                "SELECT active_account FROM settings WHERE id = 1",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(active_account, None);
    }

    #[test]
    fn two_accounts_of_one_broker_coexist_in_credentials() {
        let (_dir, conn, sec) = migrated_setup();

        credentials::save(
            &conn,
            &sec,
            "zerodha",
            "A",
            CredentialUpdate {
                api_key: Some("key-alpha".into()),
                api_secret: Some("secret-alpha".into()),
                client_id: Some("ZA0001".to_string()),
                ..Default::default()
            },
        )
        .unwrap();

        credentials::save(
            &conn,
            &sec,
            "zerodha",
            "B",
            CredentialUpdate {
                api_key: Some("key-bravo".into()),
                api_secret: Some("secret-bravo".into()),
                client_id: Some("ZA0002".to_string()),
                ..Default::default()
            },
        )
        .unwrap();

        let a = credentials::load(&conn, &sec, "zerodha", "A")
            .unwrap()
            .unwrap();
        let b = credentials::load(&conn, &sec, "zerodha", "B")
            .unwrap()
            .unwrap();

        assert_eq!(a.api_key.expose(), "key-alpha");
        assert_eq!(a.api_secret.unwrap().expose(), "secret-alpha");
        assert_eq!(a.client_id.as_deref(), Some("ZA0001"));

        assert_eq!(b.api_key.expose(), "key-bravo");
        assert_eq!(b.api_secret.unwrap().expose(), "secret-bravo");
        assert_eq!(b.client_id.as_deref(), Some("ZA0002"));

        let configured = credentials::list_configured(&conn).unwrap();
        assert!(configured.contains(&("zerodha".to_string(), "A".to_string())));
        assert!(configured.contains(&("zerodha".to_string(), "B".to_string())));
        assert_eq!(
            configured,
            vec![
                ("zerodha".to_string(), "A".to_string()),
                ("zerodha".to_string(), "B".to_string()),
            ]
        );

        let accounts = credentials::accounts_for_broker(&conn, "zerodha").unwrap();
        assert_eq!(accounts, vec!["A".to_string(), "B".to_string()]);
    }

    #[test]
    fn a_ciphertext_moved_to_another_account_will_not_decrypt() {
        let (_dir, conn, sec) = migrated_setup();

        credentials::save(
            &conn,
            &sec,
            "fyers",
            "A",
            CredentialUpdate {
                api_key: Some("fyers-key-account-a".into()),
                api_secret: Some("fyers-secret-account-a".into()),
                ..Default::default()
            },
        )
        .unwrap();

        let (ct, nonce): (String, String) = conn
            .query_row(
                "SELECT api_key_encrypted, api_key_nonce
                 FROM broker_credentials
                 WHERE broker_id = 'fyers' AND account_id = 'A'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();

        conn.execute(
            "INSERT INTO broker_credentials (
                broker_id, account_id, api_key_encrypted, api_key_nonce
            ) VALUES ('fyers', 'B', ?1, ?2)",
            params![ct, nonce],
        )
        .unwrap();

        assert!(credentials::load(&conn, &sec, "fyers", "B").is_err());

        let loaded_a = credentials::load(&conn, &sec, "fyers", "A")
            .unwrap()
            .unwrap();
        assert_eq!(loaded_a.api_key.expose(), "fyers-key-account-a");
    }

    #[test]
    fn the_default_account_keeps_the_legacy_aad() {
        let (_dir, conn, sec) = migrated_setup();

        credentials::save(
            &conn,
            &sec,
            "zerodha",
            DEFAULT_ACCOUNT,
            CredentialUpdate {
                api_key: Some("legacy-compatible-key".into()),
                ..Default::default()
            },
        )
        .unwrap();

        let (ct, nonce): (String, String) = conn
            .query_row(
                "SELECT api_key_encrypted, api_key_nonce
                 FROM broker_credentials
                 WHERE broker_id = 'zerodha' AND account_id = ?1",
                [DEFAULT_ACCOUNT],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();

        let decrypted = sec
            .decrypt(
                &ct,
                &nonce,
                &Aad::new("broker_credentials", "api_key", "zerodha"),
            )
            .unwrap();
        assert_eq!(decrypted.expose(), "legacy-compatible-key");
        assert_eq!(
            Aad::for_account("broker_credentials", "api_key", "zerodha", DEFAULT_ACCOUNT),
            Aad::new("broker_credentials", "api_key", "zerodha")
        );
    }

    #[test]
    fn sessions_revoke_per_account_only() {
        let (_dir, conn, sec) = migrated_setup();

        let s_a = session("dhan", "A", "dhan-token-a", "2026-11-02T05:00:00Z");
        let s_b = session("dhan", "B", "dhan-token-b", "2026-11-02T05:00:00Z");
        auth::upsert(&conn, &sec, &s_a).unwrap();
        auth::upsert(&conn, &sec, &s_b).unwrap();

        auth::revoke(&conn, "dhan", "A").unwrap();

        let active = auth::list_active(&conn, &sec).unwrap();
        assert_eq!(active.len(), 1);
        assert_eq!(active[0].broker_id, "dhan");
        assert_eq!(active[0].account_id, "B");
        assert_eq!(active[0].auth_token.expose(), "dhan-token-b");

        let latest = auth::latest_active(&conn, &sec).unwrap().unwrap();
        assert_eq!(latest.broker_id, "dhan");
        assert_eq!(latest.account_id, "B");
        assert_eq!(latest.auth_token.expose(), "dhan-token-b");

        assert!(auth::has_revoked(&conn).unwrap());
    }

    #[test]
    fn revoke_before_keeps_the_named_brokers_per_account() {
        let (_dir, conn, sec) = migrated_setup();

        auth::upsert(
            &conn,
            &sec,
            &session("angel", "A", "tok-angel-a", "2026-11-02T02:00:00Z"),
        )
        .unwrap();
        auth::upsert(
            &conn,
            &sec,
            &session("angel", "B", "tok-angel-b", "2026-11-02T04:00:00Z"),
        )
        .unwrap();
        auth::upsert(
            &conn,
            &sec,
            &session("deltaexchange", "A", "tok-delta-a", "2026-11-02T02:00:00Z"),
        )
        .unwrap();

        let boundary = ts("2026-11-02T03:00:00Z");
        let revoked = auth::revoke_before(&conn, boundary, |broker, _account| {
            broker == "deltaexchange"
        })
        .unwrap();
        assert_eq!(revoked, 1);

        let active = auth::list_active(&conn, &sec).unwrap();
        let surviving: Vec<(&str, &str)> = active
            .iter()
            .map(|s| (s.broker_id.as_str(), s.account_id.as_str()))
            .collect();
        assert_eq!(surviving.len(), 2);
        assert!(surviving.contains(&("angel", "B")));
        assert!(surviving.contains(&("deltaexchange", "A")));
        assert!(!surviving.contains(&("angel", "A")));
    }

    #[test]
    fn latest_active_still_returns_the_newest_across_accounts() {
        let (_dir, conn, sec) = migrated_setup();

        auth::upsert(
            &conn,
            &sec,
            &session("shoonya", "A", "tok-shoonya-a", "2026-11-02T01:00:00Z"),
        )
        .unwrap();
        auth::upsert(
            &conn,
            &sec,
            &session("upstox", "B", "tok-upstox-b", "2026-11-02T02:00:00Z"),
        )
        .unwrap();

        let latest = auth::latest_active(&conn, &sec).unwrap().unwrap();
        assert_eq!(latest.broker_id, "upstox");
        assert_eq!(latest.account_id, "B");
        assert_eq!(latest.auth_token.expose(), "tok-upstox-b");

        let list = auth::list_active(&conn, &sec).unwrap();
        let order: Vec<(&str, &str)> = list
            .iter()
            .map(|s| (s.broker_id.as_str(), s.account_id.as_str()))
            .collect();
        assert_eq!(order, vec![("upstox", "B"), ("shoonya", "A")]);
    }

    #[test]
    fn the_migration_is_idempotent() {
        let dir = tempfile::tempdir().unwrap();
        let conn = legacy_pre_102_conn(&dir);
        let sec = SecurityManager::for_tests();

        conn.execute(
            "INSERT INTO broker_credentials (
                broker_id, api_key_encrypted, api_key_nonce,
                api_secret_encrypted, api_secret_nonce, client_id
            ) VALUES ('zerodha', 'x', 'y', NULL, NULL, 'cid')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO auth (
                broker_id, auth_token_encrypted, auth_token_nonce,
                feed_token_encrypted, feed_token_nonce,
                user_id, user_name, authenticated_at, is_revoked
            ) VALUES (
                'zerodha', 'tok_ct', 'tok_n',
                NULL, NULL,
                'ZA1234', 'Trader One', '2026-11-02T04:00:00Z', 0
            )",
            [],
        )
        .unwrap();

        super::migrate(&conn).unwrap();

        credentials::save(
            &conn,
            &sec,
            "fyers",
            "B",
            CredentialUpdate {
                api_key: Some("fyers-b-key".into()),
                ..Default::default()
            },
        )
        .unwrap();

        // Running the migration a second time on an already-migrated
        // connection is a no-op.
        super::migrate(&conn).unwrap();

        let cred_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM broker_credentials", [], |r| r.get(0))
            .unwrap();
        let auth_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM auth", [], |r| r.get(0))
            .unwrap();
        assert_eq!(cred_count, 2);
        assert_eq!(auth_count, 1);

        let z_row: (String, String, String, Option<String>) = conn
            .query_row(
                "SELECT account_id, api_key_encrypted, api_key_nonce, client_id
                 FROM broker_credentials WHERE broker_id = 'zerodha'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
            )
            .unwrap();
        assert_eq!(
            z_row,
            (
                "default".to_string(),
                "x".to_string(),
                "y".to_string(),
                Some("cid".to_string()),
            )
        );

        let fyers_b = credentials::load(&conn, &sec, "fyers", "B")
            .unwrap()
            .unwrap();
        assert_eq!(fyers_b.api_key.expose(), "fyers-b-key");
    }
}
