> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Fyers API v3: the facts nsealgo relies on

Source: the Fyers API v3 documentation (myapi.fyers.in/docsv3), exported to PDF with Python samples on 1 Oct 2026
(187 pages, "API - FYERS V2"); page numbers refer to that export. Re-check after Fyers updates its docs. Limits are
settings in nsealgo, so a change is usually a config edit.

## Account-wide rate limits (p.4)

| Plan | Per second | Per minute | Per day |
|---|---|---|---|
| Standard | 10 | 200 | 1,00,000 |
| Prime | 10 | 600 | 2,00,000 |

**Exceeding the per-minute limit more than 3 times in a day blocks the user for the rest of the day.** nsealgo
counts every Fyers REST call, all endpoints together, and holds calls at 90%: 9/s and 180/min, or 540/min with
`brokers.fyers.rate_per_minute: 600` on Prime. Orders are also subject to the exchanges' 10 orders/s threshold (p.45).

## Orders

| Fact (from the docs) | Page | What nsealgo does |
|---|---|---|
| Order fields: symbol, qty, type (1 limit, 2 market, 3 SL-M, 4 SL-L), side (1 / -1), productType (CNC, INTRADAY, MARGIN, MTF), limitPrice, stopPrice, validity (DAY / IOC), disclosedQty, offlineOrder, orderTag, isSliceOrder | 47 | sends these; MARKET / SL-M go out as protected LIMIT / SL (OMS) |
| `orderTag`: alphanumeric only, 1–30 characters, not your client ID, not "Untagged" | 46 | OMS ids are uppercase letters and digits, at most 20 characters |
| Tags come back prefixed: `1:` before a tag you set, `2:` before one Fyers generated (e.g. `2:Untagged`) | 20 | prefix stripped, so reconciliation and duplicate checks can match their own tag |
| Response code **201**: the request was made but not acknowledged; check the order book before placing again | 48 | looks the order up by tag; if it isn't there yet, raises `OrderUncertain`. The OMS marks it UNKNOWN and confirms it from the book; it is never treated as rejected |
| Status: 1 cancelled, 2 filled, 3 unused, 4 transit, 5 rejected, 6 pending, 7 expired | 24 | mapped one to one (expired counts as cancelled) |
| Market protection (`mpp`) is a field of smart orders only | — | not needed: the OMS protects MARKET orders itself |
| The SDK (fyers-apiv3) catches every exception, timeouts included, and returns `{"s": "error", "code": -99}` | SDK source | -99 is treated like 201: confirm from the order book by tag, else uncertain, never rejected |
| The SDK's sync client uses its own `requests.Session`, with no timeouts | SDK source | replaced by nsealgo's transport: pooled connection, static-IP proxy, timeouts, and the outgoing-IP check |

## Positions and holdings

| Fact (from the docs) | Page | What nsealgo does |
|---|---|---|
| `netPositions`: signed `netQty`, `netAvg` (realized P&L folded into cost), `productType` | 27 | reconciles on quantity; INTRADAY is mapped to MIS |
| Holdings include T1 and demat shares; `quantity` = held at the start of the day, `remainingQuantity` = minus today's sales, `qty_t1` = T+1 quantity | 13 | start-of-day holdings plus today's positions |
| `holdingType`: T1 = bought, not yet delivered; HLD = in demat. No T1 example row is shown | 180 | T1 rows count `max(quantity, qty_t1)` and HLD rows `quantity + qty_t1`, so shares are never counted twice |

## History and symbols

| Fact (from the docs) | Page | What nsealgo does |
|---|---|---|
| Up to 100 days per request for minute resolutions, 366 for daily | 113 | 90- and 365-day chunks |
| The current, unfinished candle is included unless `range_to` is a full resolution back | 113 | drops a 1-minute candle under 60 s old, and today's daily candle before 15:30 |
| Candles are `[epoch, open, high, low, close, volume]`; `date_format: 1` takes yyyy-mm-dd | 113 | as documented |
| `NSE_CM.csv` columns: 0 Fytoken, 1 Symbol Details, 2 Exchange Instrument type, 3 Minimum lot size, 4 Tick size, 5 ISIN, …, 9 Symbol ticker (e.g. `NSE:SBIN-EQ`) | 107 | parses columns 0, 1, 3, 4 and 9 |

## Data websocket (quotes, 5-level depth)

| Fact (from the docs) | Page | What nsealgo does |
|---|---|---|
| Up to **5,000 data subscriptions**; only **one** websocket connection at a time | 139 | capacity 5,000, or 2,500 stocks when quotes and 5-level depth are both subscribed |
| To change the symbol list, unsubscribe and resubscribe (recommended) | 139 | subscribes incrementally; switch if Fyers objects |
| `FyersDataSocket(access_token="appid:accesstoken", log_path, litemode, write_to_file, reconnect, on_connect, on_close, on_error, on_message, reconnect_retry)`; `reconnect_retry` up to 50 | 140 | `reconnect_retry=50`, logs in the cache folder |
| SymbolUpdate fields `ltp`, `vol_traded_today`, `last_traded_time`, `exch_feed_time`, `bid_price`, `ask_price`, `bid_size`, `ask_size`, `upper_ckt`, `lower_ckt`, `open_price`, `high_price`, `low_price`, `prev_close_price` | 141 | parsed by these names |

## TBT (tick-by-tick) websocket: 50-level depth

| Fact (from the docs) | Page | What nsealgo does |
|---|---|---|
| NFO and NSE (equity) instruments; NSE equity added 23 Jun 2025 | 170, 182 | NSE stocks only |
| `wss://rtsocket-api.fyers.in/versova`, header `Authorization: appId:accessToken` | 170 | sent on every connection |
| Snapshot first, then only differences, which the client applies | 170 | a book is used only after its snapshot |
| Channels: subscribing alone doesn't start data, resume the channel; keep-alive is the string `"ping"` | 173 | channel 1, resumed; ping every 10 s |
| Protobuf schema `msg.proto` 1.0.0 (20 Feb 2025); `MarketLevel.num` = level, 0–49 | 174, 175 | decoded with the SDK's schema; `num` used as the index |
| **3 connections per app per user; 5 symbols per connection (market depth); 50 channels per connection** | 175 | up to 3 × 5 = 15 symbols |
| Price or entitlement for TBT | not stated | Fyers' error text is logged if it refuses |
