"""Directional variant of ml_range_orders.py: only the side NSEMLENGINE ranks for. Long top-20% expected return:
buy at low_p50, sell at high_p50 or the close. Short bottom-20%: sell at high_p50, cover at low_p50 or the close.
Run from the NSEALGOENGINE folder:  python scripts\\research\\ml_range_directional.py
"""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import sys, importlib.util, numpy as np, pandas as pd, os
spec = importlib.util.spec_from_file_location("m", str(__import__("pathlib").Path(__file__).with_name("ml_range_orders.py"))); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
import glob, polars as pl
P = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(str(m.ML / "predictions_2*.csv")))]); P["date"]=pd.to_datetime(P["date"])
raw = pl.scan_parquet(m.RAW).filter(pl.col("datetime") >= pl.lit(P["date"].min())).select("company_id","datetime","open","high","low","close").collect().to_pandas()
raw["datetime"]=pd.to_datetime(raw["datetime"]).dt.normalize(); days=sorted(raw.datetime.unique()); nxt={d:days[i+1] for i,d in enumerate(days[:-1])}
P["session"]=P["date"].map(lambda d: nxt.get(pd.Timestamp(d)))
D=P.merge(raw.rename(columns={"datetime":"session"}),on=["company_id","session"]); D=D[D.close_today>20]
D=D[D.groupby("session")["median_turnover_60d"].rank(ascending=False)<=300]
D["q"]=D.groupby("session")["exp_return_pct"].rank(pct=True)
def one_side(U, long, entry, target):
    o,h,l,c=(U[k].to_numpy() for k in ("open","high","low","close")); tk=np.where(c<100,0.01,0.05)
    if long:
        f=np.where(o<=entry,o,np.where(l<=entry-tk,entry,np.nan)); hit=h>=target+tk
        ex=np.where(hit,target,c); pnl=ex-f
    else:
        f=np.where(o>=entry,o,np.where(h>=entry+tk,entry,np.nan)); hit=l<=target-tk
        ex=np.where(hit,target,c); pnl=f-ex
    t=np.isfinite(f); qty=np.where(t,np.floor(m.VALUE/np.where(t,f,1)),0)
    net=np.where(t,pnl*qty-m.charges(),0)
    r=pd.DataFrame({"session":U.session.to_numpy(),"t":t,"net":net}); return r
for name,U,long in (("LONG top 20% ML rank: buy low_p50, sell high_p50 / close",D[D.q>=0.8],True),
                    ("SHORT bottom 20%: sell high_p50, cover low_p50 / close",D[D.q<=0.2],False),
                    ("LONG all 300 (no rank filter)",D,True)):
    e,tg=(U.low_p50,U.high_p50) if long else (U.high_p50,U.low_p50)
    r=one_side(U,long,e.to_numpy(),tg.to_numpy()); t=r[r.t]; daily=r.groupby("session").net.sum()
    print(f"{name:58s} trades/day {len(t)/r.session.nunique():5.0f}  bps/trade {t.net.mean()/m.VALUE*1e4:+6.1f}  win {100*(t.net>0).mean():3.0f}%  t {daily.mean()/(daily.std()/np.sqrt(len(daily))):+.1f}  per day "+" ".join(f"{v:+.0f}" for v in daily))
