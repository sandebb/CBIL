"""Stream parsers for each free feed, fed with messages shaped like the SDKs deliver them."""
from nsealgo.brokers.angelone import AngelOneAdapter
from nsealgo.brokers.base import noren_quote
from nsealgo.brokers.fyers import FyersAdapter
from nsealgo.brokers.icici_breeze import ICICIBreezeAdapter
from nsealgo.brokers.kotak_neo import KotakNeoAdapter
from nsealgo.brokers.upstox import UpstoxAdapter


def test_angel_paise_and_book_orientation():
    msg = {"token": "3045", "exchange_type": 1, "exchange_timestamp": 1790648400000, "last_traded_price": 80125,
           "volume_trade_for_the_day": 1234567, "open_price_of_the_day": 79900, "closed_price": 79500,
           "best_5_buy_data": [{"flag": 1, "quantity": 300, "price": 80130}],      # labels swapped by some SDKs
           "best_5_sell_data": [{"flag": 0, "quantity": 500, "price": 80120}]}
    q = AngelOneAdapter.parse_tick(msg, "NSE:SBIN")
    assert (q.ltp, q.bid, q.ask, q.bid_qty, q.ask_qty) == (801.25, 801.2, 801.3, 500.0, 300.0)
    assert q.volume == 1234567 and q.prev_close == 795.0 and q.exch_ts is not None and q.source == "angelone"


def test_upstox_full_index_and_ltpc_feeds():
    full = {"fullFeed": {"marketFF": {"ltpc": {"ltp": 801.25, "ltt": "1790648400000", "cp": 795.0}, "vtt": "1234567",
                                      "marketLevel": {"bidAskQuote": [{"bidQ": "500", "bidP": 801.2, "askQ": "300",
                                                                       "askP": 801.3}]},
                                      "marketOHLC": {"ohlc": [{"interval": "1d", "open": 799.0, "high": 805.0,
                                                               "low": 798.0, "close": 801.25, "vol": "1234567"}]}}}}
    q = UpstoxAdapter.parse_feed(full, "NSE:SBIN")
    assert (q.ltp, q.volume, q.bid, q.ask, q.open, q.prev_close) == (801.25, 1234567.0, 801.2, 801.3, 799.0, 795.0)
    idx = {"fullFeed": {"indexFF": {"ltpc": {"ltp": 25101.5, "cp": 25000.0}}}}
    assert UpstoxAdapter.parse_feed(idx, "IDX:NIFTY50").ltp == 25101.5
    assert UpstoxAdapter.parse_feed({"ltpc": {"ltp": 12.5}}, "NSE:X").volume == 0.0


def test_breeze_kotak_noren_fyers():
    b = ICICIBreezeAdapter.parse_tick({"symbol": "4.1!3045", "last": 801.25, "ttq": 1234567, "bPrice": 801.2,
                                       "bQty": 500, "sPrice": 801.3, "sQty": 300, "open": 799, "close": 795}, "NSE:SBIN")
    assert (b.ltp, b.bid, b.ask, b.volume) == (801.25, 801.2, 801.3, 1234567.0)
    k = KotakNeoAdapter.parse_tick({"tk": "3045", "e": "nse_cm", "ltp": "801.25", "v": "1234567", "bp": "801.2",
                                    "sp": "801.3", "bq": "500", "bs": "300", "c": "795"}, "NSE:SBIN")
    assert (k.ltp, k.bid_qty, k.ask_qty, k.prev_close) == (801.25, 500.0, 300.0, 795.0)
    assert KotakNeoAdapter.parse_tick({"tk": "Nifty 50", "e": "nse_cm", "iv": "25101.5", "ic": "25000"},
                                      "IDX:NIFTY50").ltp == 25101.5
    n = noren_quote("NSE:SBIN", {"lp": "801.25", "v": "1234567", "bp1": "801.2", "sp1": "801.3", "bq1": "500",
                                 "sq1": "300", "ft": "1790648400"}, "shoonya")
    assert n.bid == 801.2 and n.exch_ts is not None and n.source == "shoonya"
    f = FyersAdapter.parse_tick({"symbol": "NSE:SBIN-EQ", "ltp": 801.25, "vol_traded_today": 1234567,
                                 "bid_price": 801.2, "ask_price": 801.3, "bid_size": 500, "ask_size": 300,
                                 "exch_feed_time": 1790648400}, "NSE:SBIN")
    assert (f.ask, f.ask_qty, f.source) == (801.3, 300.0, "fyers") and f.exch_ts is not None
