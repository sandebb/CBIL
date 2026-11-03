//! Whether a broker's live session may end (roadmap "Broker switching:
//! enforcement, not only confirmation"; LOG-07).
//!
//! | On the broker A whose session would end | Ending it |
//! | --- | --- |
//! | Running Live strategies, OpenScript runs or scalping protection | refused until they are stopped; the message names them |
//! | Placements whose outcome is not yet known (LOG-08) | refused until reconciled |
//! | Exits partly filled | refused until they reach a final state |
//! | Working orders and positions placed by hand | allowed after the confirm; they stay at A |
//! | Sandbox runs | allowed (never counted) |
//!
//! The counts come from the order-outcomes service through [`SwitchGuard`],
//! so this module never reads another module's tables. Every way a broker
//! session is ENDED asks it first: the web-shaped settings save that makes
//! another broker the active one, new keys for the logged-in broker and
//! forgetting it (all of them end that broker's session, and only it —
//! D-S3). A completed sign-in to another broker is not an end: several
//! brokers are live at once and the new one joins the store beside the
//! others, so it never asks this guard. Signing out of OpenAlgo and the
//! daily boundary end sessions without a switch, and neither asks it.

use crate::error::Result;
use crate::state::AppState;

/// What still depends on a broker's Live session.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, serde::Serialize)]
pub struct SwitchBlockers {
    /// Running Live strategy runs.
    pub strategy_runs: u32,
    /// Running Live OpenScript runs.
    pub openscript_runs: u32,
    /// Active Live scalping protection (stops and targets).
    pub scalping_stops: u32,
    /// Placements whose outcome is not yet known.
    pub uncertain_placements: u32,
    /// Exits partly filled and not yet final.
    pub partial_exits: u32,
}

impl SwitchBlockers {
    pub fn is_empty(&self) -> bool {
        *self == Self::default()
    }

    /// Why the session of `broker` (its display name) cannot end yet, in a
    /// trader's words, or `None` when nothing blocks it.
    pub fn refusal(&self, broker: &str) -> Option<String> {
        if self.is_empty() {
            return None;
        }
        let count = |n: u32, one: &str, many: &str| match n {
            0 => None,
            1 => Some(format!("1 {}", one)),
            n => Some(format!("{} {}", n, many)),
        };
        let items: Vec<String> = [
            count(
                self.strategy_runs,
                "running Live strategy",
                "running Live strategies",
            ),
            count(
                self.openscript_runs,
                "running Live OpenScript run",
                "running Live OpenScript runs",
            ),
            count(
                self.scalping_stops,
                "position under scalping protection",
                "positions under scalping protection",
            ),
            count(
                self.uncertain_placements,
                "order whose outcome is not known yet",
                "orders whose outcome is not known yet",
            ),
            count(
                self.partial_exits,
                "exit still filling",
                "exits still filling",
            ),
        ]
        .into_iter()
        .flatten()
        .collect();
        let list = match items.len() {
            1 => items[0].clone(),
            n => format!("{} and {}", items[..n - 1].join(", "), items[n - 1]),
        };
        let mut next = Vec::new();
        if self.strategy_runs + self.openscript_runs + self.scalping_stops > 0 {
            next.push("stop the strategies and protection");
        }
        if self.uncertain_placements > 0 {
            next.push("check the order book until every order is settled");
        }
        if self.partial_exits > 0 {
            next.push("wait for the exits to finish");
        }
        Some(format!(
            "{} still has {}. They are not moved to another broker. First {}, then switch. Nothing was changed.",
            broker,
            list,
            next.join(", then ")
        ))
    }
}

/// Where the counts come from: the order-outcomes service
/// (`services::obligations::OutcomesGuard`, installed by `AppState::new`).
/// Tests replace it to drive each row.
pub trait SwitchGuard: Send + Sync {
    /// What still depends on `broker`'s Live session. Sandbox items are
    /// never counted.
    fn blockers(&self, state: &AppState, broker: &str) -> Result<SwitchBlockers>;
}

/// The trader-facing refusal when `live_broker`'s session must not end yet.
/// A guard that cannot answer refuses too: a switch is never allowed on an
/// unknown.
pub fn refusal(state: &AppState, live_broker: &str) -> Option<String> {
    let name = crate::brokers::setup_guide::display_name(live_broker);
    let guard = state.switch_guard.read().clone();
    match guard.blockers(state, live_broker) {
        Ok(b) => b.refusal(&name),
        Err(e) => {
            tracing::warn!("Could not check what depends on the broker session: {}", e);
            Some(format!(
                "OpenAlgo could not check whether anything still runs on {}. Nothing was changed. Try again.",
                name
            ))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_what_blocks_the_switch_in_a_traders_words() {
        assert_eq!(SwitchBlockers::default().refusal("Zerodha"), None);
        let b = SwitchBlockers {
            strategy_runs: 2,
            scalping_stops: 1,
            ..Default::default()
        };
        assert_eq!(
            b.refusal("Zerodha").unwrap(),
            "Zerodha still has 2 running Live strategies and 1 position under scalping protection. They are not moved to another broker. First stop the strategies and protection, then switch. Nothing was changed."
        );
        let b = SwitchBlockers {
            openscript_runs: 1,
            uncertain_placements: 3,
            partial_exits: 1,
            ..Default::default()
        };
        assert_eq!(
            b.refusal("Fyers").unwrap(),
            "Fyers still has 1 running Live OpenScript run, 3 orders whose outcome is not known yet and 1 exit still filling. They are not moved to another broker. First stop the strategies and protection, then check the order book until every order is settled, then wait for the exits to finish, then switch. Nothing was changed."
        );
    }
}
