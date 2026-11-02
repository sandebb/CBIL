"""Fyers TBT recorder (scalp plan step 1): raw depth messages to parquet, replayable, with ltq / vtt_diff and the
feed / send / receive times; `check` summarises a day."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import time

import pytest

pb = pytest.importorskip("fyers_apiv3.FyersWebsocket.msg_pb2")
pytest.importorskip("pyarrow")

from nsealgo.data.fyers_tbt import FyersTbtFeed  # noqa: E402
from nsealgo.data.tbt_recorder import TbtRecorder, check  # noqa: E402


def _msg(snap, levels, seq, ltq=None, t=1_760_000_000_000):
    m = pb.SocketMessage()
    m.snapshot = snap
    fd = m.feeds["NSE:SBIN-EQ"]
    fd.ticker, fd.sequence_no, fd.snapshot = "NSE:SBIN-EQ", seq, snap
    fd.feed_time.value, fd.send_time.value = t, t + 3
    if ltq:
        fd.quote.ltq.value, fd.quote.vtt_diff.value, fd.quote.ltp.value = ltq, ltq, 80010
    for i, (p, q) in enumerate(levels):
        lv = fd.depth.bids.add()
        lv.num.value = i
        if p:
            lv.price.value = p
        lv.qty.value = q
    return m.SerializeToString()


def test_messages_are_recorded_as_received_and_the_book_still_updates(tmp_path):
    rec = TbtRecorder(tmp_path, flush_seconds=999)
    f = FyersTbtFeed("APP", "TOK", lambda k: k, ws_factory=lambda *a: None, threaded=False, lookup=False, schema=pb,
                     recorder=rec)
    f._on_message(_msg(True, [(80000, 100), (79995, 50)], 1))
    f._on_message(_msg(False, [(None, 60)], 2, ltq=40))
    assert f.books["NSE:SBIN-EQ"].bq[:2] == [60.0, 50.0]
    f.close()
    import pyarrow.parquet as pq
    files = list(tmp_path.glob("*/part-*.parquet"))
    assert len(files) == 1
    t = pq.read_table(files[0]).to_pandas()
    assert list(t["seq"]) == [1, 2] and list(t["snapshot"]) == [True, False]
    assert list(t["bid_qty"][1]) == [60] and str(t["bid_px"][1][0]) == "nan"     # unchanged price = NaN
    assert t["ltq"].isna()[0] and t["ltq"][1] == 40 and t["vtt_diff"][1] == 40
    assert t["recv_ns"].min() > time.time_ns() - 60e9
    s = check(files[0].parent)
    assert "2 messages" in s and "vtt_diff present: 50.0%" in s
