//! What a broker revoke whose database write failed still has to write
//! (SEC-05b), and how it is kept in the marker file
//! (`BrokerAuthService`'s `REVOKE_MARKER`) across a restart.

use crate::brokers::catalog;
use crate::db::sqlite::auth;
use crate::error::Result;

/// What a broker revoke whose database write failed still has to write
/// (SEC-05b), kept in memory and in the marker file (`REVOKE_MARKER`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PendingRevoke {
    /// The app sign-out: every stored session except the ones live in
    /// memory meanwhile (a retry must never revoke a session that signed in
    /// beside the one that logged out). An empty or unreadable marker means
    /// this (fail closed; it is also what a marker written before scopes
    /// existed holds).
    All,
    /// The daily boundary (SES-02): the stored sessions authenticated
    /// before it, except continuous (crypto) ones (SES-01). A session
    /// signed in after the boundary is never touched by its retry.
    Before(chrono::DateTime<chrono::Utc>),
    /// One broker's sign-out (D-S3): that (broker, account) row and only
    /// that — a failed write must never grow into a sibling's stored
    /// session, even across a restart. A row whose session is live
    /// meanwhile (a re-sign-in) is never touched by its retry.
    Pair(String, String),
}

impl PendingRevoke {
    /// Two revokes both still to be written: the wider one. Scopes that do
    /// not contain each other (a pair and a boundary, two different pairs)
    /// merge to `All`: a retry never revokes less than was asked for, and
    /// while the app runs the live set still spares every live session.
    pub(super) fn merge(self, other: Self) -> Self {
        match (self, other) {
            (Self::All, _) | (_, Self::All) => Self::All,
            (Self::Before(a), Self::Before(b)) => Self::Before(a.max(b)),
            (Self::Pair(b1, a1), Self::Pair(b2, a2)) if b1 == b2 && a1 == a2 => Self::Pair(b1, a1),
            _ => Self::All,
        }
    }

    /// `live` are the (broker, account) sessions running in memory now; no
    /// scope ever reaches their rows (a sign-in that finished after the
    /// revoke outlives it).
    pub(super) fn write(
        self,
        conn: &rusqlite::Connection,
        live: &[(String, String)],
    ) -> Result<()> {
        match self {
            Self::All => auth::revoke_all_except(conn, live).map(|_| ()),
            Self::Before(boundary) => auth::revoke_before(conn, boundary, |broker, _account| {
                !catalog::has_daily_boundary(broker)
            })
            .map(|_| ()),
            Self::Pair(broker, account) => {
                if live.iter().any(|(b, a)| b == &broker && a == &account) {
                    return Ok(());
                }
                auth::revoke(conn, &broker, &account).map(|_| ())
            }
        }
    }

    /// The marker's contents: empty for `All`, the boundary (RFC 3339) for
    /// `Before`, `pair:<broker>|<account>` for one broker's sign-out.
    pub(super) fn encode(&self) -> String {
        match self {
            Self::All => String::new(),
            Self::Before(boundary) => boundary.to_rfc3339(),
            Self::Pair(broker, account) => format!("pair:{}|{}", broker, account),
        }
    }

    /// Read a marker; anything but a boundary or a well-formed pair means
    /// every session (fail closed).
    pub(super) fn decode(text: &str) -> Self {
        let text = text.trim();
        if let Some(rest) = text.strip_prefix("pair:") {
            let Some((broker, account)) = rest.split_once('|') else {
                return Self::All;
            };
            if !broker.is_empty() && !account.is_empty() {
                return Self::Pair(broker.to_string(), account.to_string());
            }
            return Self::All;
        }
        chrono::DateTime::parse_from_rfc3339(text)
            .map(|b| Self::Before(b.with_timezone(&chrono::Utc)))
            .unwrap_or(Self::All)
    }
}

#[cfg(test)]
mod tests {
    use super::PendingRevoke;
    use chrono::TimeZone;

    /// The marker's scope: empty (as written before scopes existed) or
    /// unreadable means every session; a boundary and one broker's pair
    /// read back as written; two pending revokes merge to the wider.
    #[test]
    fn a_marker_reads_back_its_scope_and_merges_to_the_wider() {
        let b1 = chrono::Utc
            .with_ymd_and_hms(2026, 10, 4, 21, 30, 0)
            .unwrap();
        let b2 = chrono::Utc
            .with_ymd_and_hms(2026, 10, 5, 21, 30, 0)
            .unwrap();
        let (all, before1, before2) = (
            PendingRevoke::All,
            PendingRevoke::Before(b1),
            PendingRevoke::Before(b2),
        );
        for unreadable in ["", "\n", "2026-10-0", "garbage"] {
            assert_eq!(PendingRevoke::decode(unreadable), all, "{:?}", unreadable);
        }
        let pair = PendingRevoke::Pair("zerodha".into(), "default".into());
        assert_eq!(all.encode(), "");
        assert_eq!(PendingRevoke::decode(&before1.encode()), before1);
        assert_eq!(PendingRevoke::decode(&pair.encode()), pair);
        // A pair marker never parses as a boundary or a wider scope.
        assert_eq!(PendingRevoke::decode("pair:zerodha|"), PendingRevoke::All);
        assert_eq!(PendingRevoke::decode("pair:zerodha"), PendingRevoke::All);
        assert_eq!(before1.clone().merge(before2.clone()), before2.clone());
        assert_eq!(before2.clone().merge(before1.clone()), before2.clone());
        assert_eq!(before1.clone().merge(all.clone()), all.clone());
        assert_eq!(all.clone().merge(before1.clone()), all.clone());
        // The same pair twice stays scoped to it; anything else that does
        // not contain it widens to `All` (a retry never revokes less).
        assert_eq!(pair.clone().merge(pair.clone()), pair);
        assert_eq!(pair.clone().merge(before1.clone()), PendingRevoke::All);
        assert_eq!(before1.merge(pair.clone()), PendingRevoke::All);
        assert_eq!(pair.merge(all), PendingRevoke::All);
    }
}
