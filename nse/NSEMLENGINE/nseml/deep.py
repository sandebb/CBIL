"""Phase 2: DeepLOB adapted to daily data (research design, "Model architecture").

One network, shared by every stock, reads a stock's last W days of the scale-free features_v2 inputs and
outputs P10 / P50 / P90 of the five next-day targets (15 numbers):

  x (B, W, F)
   -> 1. causal temporal convolution: depthwise 1D conv (kernels 3 and 5) on each feature alone,
         then a 1 x F pointwise mix across features (replaces DeepLOB's 1 x 2 order-book convolutions)
   -> 2. inception blocks, dilated + causal: 1x1 | 1x1->k3 | 1x1->k3 dilation 2 | causal maxpool->1x1
   -> 3. LSTM; its last hidden state
   -> 4. fusion: [LSTM state, today's inputs] -> 2-layer MLP
   -> 5. monotone quantile heads: P50, P10 = P50 - softplus(.), P90 = P50 + softplus(.)  (never cross)

Loss: pinball over all 15 outputs (+ a small Huber term on P50), targets in the same units as the
baselines (sigma units for gap / return / wicks, log relative volume). Missing targets are masked.

"Causal" everywhere: a window ending on day t contains days <= t only, and every convolution pads on the
left, so no output at step s sees step s+1 (tests/test_lookahead.py checks the windows).

Market inputs live inside the window (each row already carries that day's market features), so the
separate market encoder of the design is folded into the same network.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import math
import time

import numpy as np
import polars as pl

from .features import FEATURES
from .metrics import MODEL_TARGETS, qcol

KEYS = ["company_id", "datetime"]


def _torch():
    try:
        import torch
    except ImportError as e:
        raise SystemExit("phase 2 needs PyTorch: python -m pip install torch   (GPU: setup_env.ps1 -Gpu)") from e
    return torch


# =====================================================================================================
# PANEL: the whole dataset as arrays, windows cut on demand (nothing is materialised)
# =====================================================================================================
class Panel:
    """Rows sorted by (company, date). X = features (float32, raw, NaN kept), Y = model targets.
    start[i] = index of the first row of row i's company, so a window ending at i is rows
    max(start[i], i - W + 1) .. i, left-padded with zeros (mask = 0) when the stock has less history."""

    def __init__(self, df: pl.DataFrame):
        df = df.sort("company_id", "datetime")
        self.keys = df.select(KEYS)
        self.dates = df["datetime"].to_numpy()
        self.X = df.select(FEATURES).to_numpy().astype(np.float32)
        self.Y = df.select(MODEL_TARGETS).to_numpy().astype(np.float32)
        cid = df["company_id"].to_numpy()
        first = np.r_[True, cid[1:] != cid[:-1]]
        self.start = np.maximum.accumulate(np.where(first, np.arange(len(cid)), 0)).astype(np.int64)

    def rows_between(self, lo, hi, need_target=True) -> np.ndarray:
        m = (self.dates >= np.datetime64(lo)) & (self.dates <= np.datetime64(hi))
        if need_target:
            m &= np.isfinite(self.Y[:, 1])          # close return present = a valid next day
        return np.flatnonzero(m)


def window_index(start: np.ndarray, rows: np.ndarray, W: int):
    """(len(rows), W) row indices + validity mask for windows ending at `rows`."""
    idx = rows[:, None] - np.arange(W - 1, -1, -1)[None, :]
    mask = idx >= start[rows][:, None]
    return np.where(mask, idx, rows[:, None]), mask


# =====================================================================================================
# MODEL
# =====================================================================================================
def build_model(n_features: int, p: dict):
    torch = _torch()
    nn = torch.nn
    F_ = torch.nn.functional
    C, H = int(p["channels"]), int(p["lstm_hidden"])

    class UnrolledLSTM(nn.LSTM):
        """Single-layer nn.LSTM whose forward is plain linear/sigmoid/tanh ops: identical maths and
        parameter names (state_dict interchangeable with nn.LSTM), but no MIOpen RNN kernels.
        On the ROCm GPU the MIOpen JIT cannot build the RNN/reduction kernels for gfx1151
        (miopenStatusUnknownError); conv/linear/elementwise kernels work fine. Enabled per config
        with deeplob 'lstm: unrolled' (default 'native' = exactly today's nn.LSTM, CPU models
        untouched)."""
        def forward(self, input, hx=None):
            if self.num_layers != 1:
                raise NotImplementedError("UnrolledLSTM supports a single layer")
            if not self.batch_first:
                input = input.transpose(0, 1)
            B, T, _ = input.shape
            Hh = self.hidden_size
            if hx is None:
                h = input.new_zeros(B, Hh)
                c = input.new_zeros(B, Hh)
            else:
                h, c = hx
            xg = F_.linear(input, self.weight_ih_l0, self.bias_ih_l0)     # input side at every step (B, T, 4H)
            out = input.new_empty(B, T, Hh)
            for t in range(T):
                a = xg[:, t, :] + F_.linear(h, self.weight_hh_l0, self.bias_hh_l0)
                i_, f_, g_, o_ = a.chunk(4, dim=-1)                       # gates: input, forget, cell, output
                c = torch.sigmoid(f_) * c + torch.sigmoid(i_) * torch.tanh(g_)
                h = torch.sigmoid(o_) * torch.tanh(c)
                out[:, t, :] = h
            if not self.batch_first:
                out = out.transpose(0, 1).contiguous()
            return out, (h, c)

    class CausalConv(nn.Module):
        def __init__(self, cin, cout, k, dilation=1, groups=1):
            super().__init__()
            self.pad = (k - 1) * dilation
            self.conv = nn.Conv1d(cin, cout, k, dilation=dilation, groups=groups)

        def forward(self, x):                                  # x (B, C, T)
            return self.conv(F_.pad(x, (self.pad, 0)))

    class CausalDepthwise(nn.Module):
        """Per-feature causal conv written as k shifted multiply-adds: same maths as Conv1d(groups=F) but
        several times faster on a CPU (grouped convolutions are slow there)."""
        def __init__(self, c, k):
            super().__init__()
            self.k = k
            self.w = nn.Parameter(torch.randn(c, k) / math.sqrt(k))
            self.b = nn.Parameter(torch.zeros(c))

        def forward(self, x):                                  # x (B, C, T)
            T = x.shape[-1]
            xp = F_.pad(x, (self.k - 1, 0))
            y = self.b.view(1, -1, 1)
            for j in range(self.k):                            # tap j looks k-1-j steps back
                y = y + xp[..., j:j + T] * self.w[:, j].view(1, -1, 1)
            return y

    class ChannelNorm(nn.Module):                              # LayerNorm over channels at each step (causal)
        def __init__(self, c):
            super().__init__()
            self.ln = nn.LayerNorm(c)

        def forward(self, x):
            return self.ln(x.transpose(1, 2)).transpose(1, 2)

    class Inception(nn.Module):
        def __init__(self, c):
            super().__init__()
            b = c // 4
            self.b1 = nn.Conv1d(c, b, 1)
            self.b2 = nn.Sequential(nn.Conv1d(c, b, 1), nn.GELU(), CausalConv(b, b, 3))
            self.b3 = nn.Sequential(nn.Conv1d(c, b, 1), nn.GELU(), CausalConv(b, b, 3, dilation=2))
            self.b4 = nn.Conv1d(c, c - 3 * b, 1)
            self.norm = ChannelNorm(c)

        def forward(self, x):
            pooled = F_.max_pool1d(F_.pad(x, (2, 0), value=-1e4), 3, stride=1)   # causal max-pool
            y = torch.cat([self.b1(x), self.b2(x), self.b3(x), self.b4(pooled)], dim=1)
            return self.norm(x + F_.gelu(y))

    class DeepLOBDaily(nn.Module):
        def __init__(self):
            super().__init__()
            self.register_buffer("mu", torch.zeros(n_features))
            self.register_buffer("sd", torch.ones(n_features))
            self.dw3 = CausalDepthwise(n_features, 3)
            self.dw5 = CausalDepthwise(n_features, 5)
            self.mix = nn.Conv1d(3 * n_features + 1, C, 1)        # 1 x F mixing (+ the history mask)
            self.norm = ChannelNorm(C)
            self.inc = nn.Sequential(*[Inception(C) for _ in range(int(p["inception_blocks"]))])
            _lstm_cls = UnrolledLSTM if str(p.get("lstm", "native")).lower() == "unrolled" else nn.LSTM
            self.lstm = _lstm_cls(C, H, batch_first=True)
            self.drop = nn.Dropout(float(p["dropout"]))
            self.head = nn.Sequential(nn.Linear(H + n_features, int(p["mlp_hidden"])), nn.GELU(),
                                      nn.Dropout(float(p["dropout"])), nn.Linear(int(p["mlp_hidden"]), 15))

        def forward(self, x, m):                               # x (B, T, F) raw, m (B, T) 1 = real day
            x = torch.nan_to_num(((x - self.mu) / self.sd).clamp(-6, 6), nan=0.0) * m.unsqueeze(-1)
            xt = x.transpose(1, 2)                              # (B, F, T)
            z = torch.cat([xt, self.dw3(xt), self.dw5(xt), m.unsqueeze(1)], dim=1)
            z = self.inc(self.norm(F_.gelu(self.mix(z))))
            h, _ = self.lstm(z.transpose(1, 2))
            o = self.head(torch.cat([self.drop(h[:, -1]), x[:, -1]], dim=1)).view(-1, 5, 3)
            q50 = o[..., 0]
            return torch.stack([q50 - F_.softplus(o[..., 1]), q50, q50 + F_.softplus(o[..., 2])], dim=-1)

    return DeepLOBDaily()


def loss_fn(pred, y, quantiles, huber_weight):
    """pred (B, 5, 3), y (B, 5) with NaN for missing targets."""
    torch = _torch()
    mask = torch.isfinite(y)
    yy = torch.nan_to_num(y)
    q = torch.tensor(quantiles, device=pred.device).view(1, 1, -1)
    e = yy.unsqueeze(-1) - pred
    pin = torch.maximum(q * e, (q - 1) * e).mean(-1)
    hub = torch.nn.functional.huber_loss(pred[..., 1], yy, reduction="none", delta=1.0)
    per = (pin + huber_weight * hub) * mask
    return per.sum() / mask.sum().clamp(min=1)


# =====================================================================================================
# FOLD TRAINING / PREDICTION
# =====================================================================================================
class DeepLOB:
    name = "deeplob"

    def __init__(self, params: dict, panel: Panel, quantiles: list[float], log=print):
        self.p, self.panel, self.q, self.log = params, panel, quantiles, log
        torch = _torch()
        dev = str(params.get("device", "auto"))
        if dev == "auto":
            dev = "cuda" if torch.cuda.is_available() else "cpu"     # ROCm builds also report "cuda"
        self.device = torch.device(dev)
        if int(params.get("num_threads") or 0) > 0:
            torch.set_num_threads(int(params["num_threads"]))
        self.Xt = torch.from_numpy(panel.X)                            # stays on CPU; batches are moved
        self.Yt = torch.from_numpy(panel.Y)

    def _batch(self, rows):
        torch = _torch()
        W = int(self.p["window"])
        idx, mask = window_index(self.panel.start, rows, W)
        x = self.Xt[torch.from_numpy(idx.reshape(-1))].view(len(rows), W, -1)
        m = torch.from_numpy(mask.astype(np.float32))
        y = self.Yt[torch.from_numpy(rows)]
        return x.to(self.device, non_blocking=True), m.to(self.device), y.to(self.device)

    def _eval_loss(self, model, rows):
        torch = _torch()
        model.eval()
        tot, n = 0.0, 0
        bs = int(self.p["batch_size"]) * 4
        with torch.no_grad():
            for i in range(0, len(rows), bs):
                x, m, y = self._batch(rows[i:i + bs])
                tot += float(loss_fn(model(x, m), y, self.q, 0.0)) * len(x)
                n += len(x)
        return tot / max(n, 1)

    def fit(self, fold, seed: int = 0):
        torch = _torch()
        p, P = self.p, self.panel
        rng = np.random.default_rng(int(p["seed"]) + seed)
        torch.manual_seed(int(p["seed"]) + seed)
        tr = P.rows_between(*fold.train)
        va = P.rows_between(*fold.val)
        if len(va) > int(p["val_windows"]):
            va = np.sort(rng.choice(va, int(p["val_windows"]), replace=False))
        model = build_model(P.X.shape[1], p).to(self.device)
        # input scaling from the TRAINING window only (robust: median / IQR-based sd)
        sample = P.X[rng.choice(tr, min(len(tr), 300_000), replace=False)]
        mu = np.nanmedian(sample, axis=0)
        q75, q25 = np.nanpercentile(sample, [75, 25], axis=0)
        sd = (q75 - q25) / 1.349
        sd = np.where(np.isfinite(sd) & (sd > 1e-8), sd, np.nanstd(sample, axis=0))
        sd = np.where(np.isfinite(sd) & (sd > 1e-8), sd, 1.0)
        model.mu.copy_(torch.from_numpy(np.nan_to_num(mu).astype(np.float32)))
        model.sd.copy_(torch.from_numpy(sd.astype(np.float32)))
        opt = torch.optim.AdamW(model.parameters(), lr=float(p["lr"]), weight_decay=float(p["weight_decay"]))
        per_epoch = min(len(tr), int(p["train_windows_per_epoch"]))
        bs = int(p["batch_size"])
        steps = int(p["max_epochs"]) * math.ceil(per_epoch / bs)
        sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=float(p["lr"]), total_steps=steps, pct_start=0.15)
        best, best_state, bad = float("inf"), None, 0
        n_par = sum(t.numel() for t in model.parameters())
        self.log(f"      deeplob: {n_par:,} parameters, {len(tr):,} train / {len(va):,} val windows, "
                 f"W={p['window']}, device {self.device}")
        for ep in range(int(p["max_epochs"])):
            t0 = time.time()
            model.train()
            rows = rng.choice(tr, per_epoch, replace=False)
            run = 0.0
            for i in range(0, per_epoch, bs):
                x, m, y = self._batch(rows[i:i + bs])
                loss = loss_fn(model(x, m), y, self.q, float(p["huber_weight"]))
                opt.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                sched.step()
                run += loss.item() * len(x)
            vl = self._eval_loss(model, va)
            self.log(f"      deeplob epoch {ep + 1}: train {run / per_epoch:.4f}  val pinball {vl:.4f}  ({time.time() - t0:.0f}s)")
            if vl < best - 1e-5:
                best, bad = vl, 0
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            else:
                bad += 1
                if bad >= int(p["patience"]):
                    break
        model.load_state_dict(best_state)
        self.model = model
        self.best_val = best
        return self

    def predict_rows(self, rows) -> np.ndarray:
        torch = _torch()
        self.model.eval()
        out = []
        bs = int(self.p["batch_size"]) * 4
        with torch.no_grad():
            for i in range(0, len(rows), bs):
                x, m, _ = self._batch(rows[i:i + bs])
                out.append(self.model(x, m).cpu().numpy())
        return np.concatenate(out) if out else np.zeros((0, 5, 3), np.float32)

    def predict(self, fold, models: list | None = None) -> pl.DataFrame:
        """P10/P50/P90 for every test row (average over an ensemble of fitted copies if given)."""
        rows = self.panel.rows_between(*fold.test, need_target=False)
        preds = [mdl.predict_rows(rows) for mdl in (models or [self])]
        a = np.mean(preds, axis=0)                              # (N, 5, 3)
        a[:, 2:4, :] = np.maximum(a[:, 2:4, :], 0)              # wicks cannot be negative
        cols = {qcol(t, q): a[:, j, k] for j, t in enumerate(MODEL_TARGETS) for k, q in enumerate(self.q)}
        return self.panel.keys[rows].with_columns([pl.Series(k, v, dtype=pl.Float32) for k, v in cols.items()])

    def save(self, path):
        torch = _torch()
        torch.save({"state_dict": self.model.state_dict(), "params": self.p, "features": FEATURES,
                    "targets": MODEL_TARGETS, "quantiles": self.q, "best_val": self.best_val}, path)


def fit_predict(params, panel, fold, quantiles, out_dir, log=print) -> pl.DataFrame:
    """Train an ensemble of `ensemble` seeds for one fold, save the models, return test predictions."""
    members = []
    for s in range(int(params.get("ensemble", 1))):
        m = DeepLOB(params, panel, quantiles, log).fit(fold, seed=s)
        m.save(out_dir / f"model_deeplob_{fold.label()}_s{s}.pt")
        members.append(m)
    return members[0].predict(fold, members)
