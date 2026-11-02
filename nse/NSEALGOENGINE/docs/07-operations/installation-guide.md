> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Installation guide (Windows 11, ASUS ProArt PX13)

## Prerequisites

| Item | Notes |
|---|---|
| Windows 11, admin rights for setup | A standard user afterwards |
| **Python 3.12** | Required for AMD's ROCm PyTorch wheels (`-Gpu`); 3.10+ otherwise |
| AMD graphics driver "AMD Software: PyTorch on Windows Edition 7.2.1" | Only for the GPU ([PX13 plan](../03-architecture/PX13.md)) |
| Fyers API app (client ID, secret, redirect `http://127.0.0.1:5000/`) | Orders and data |
| Upstox API app (and Upstox Plus for 30-level depth) | Optional depth tier |
| Static IP from StaticIP.in, **registered with Fyers** (and with any other order broker) | Required for live API orders |

## Steps

1. **Get the code** into a folder on a Dev Drive if you have one ([Windows 11 setup](WINDOWS.md)).
2. **Install:**
   ```
   powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1 -Gpu -PowerPlan
   ```
   - `-Gpu` installs ROCm PyTorch, prints the detected GPU and runs `nsealgo gpu --fresh`.
   - `-PowerPlan` switches to the Ultimate Performance plan.
   - `-DefenderDataExclusions` is optional; a trusted Dev Drive doesn't need exclusions.
3. **Secrets:** create `.env` next to `config/` and restrict it to your account. At least:
   ```
   FYERS_CLIENT_ID=XXXX-100
   FYERS_SECRET_KEY=...
   STATICIP_PROXY=http://user:password@host:port
   STATIC_IP=203.0.113.7
   UPSTOX_API_KEY=...      UPSTOX_API_SECRET=...      (if used)
   TELEGRAM_TOKEN=...      TELEGRAM_CHAT_ID=...       (optional alerts)
   ```
   Other brokers' variables are named in `config/system.yaml` (e.g. `SHOONYA_*`, `KITE_*`, `DHAN_*`).
4. **Review `config/system.yaml`:**
   - `broker.orders`: Fyers by default;
   - `broker.data`;
   - `feeds.depth_overlay`, `feeds.watch`;
   - `strategies`, `capital`, `risk`.
   
   Every key is in the [Configuration reference](../05-reference/configuration-reference.md).
5. **Verify:**
   ```
   nsealgo doctor        # Python, SDKs, config, data
   nsealgo hardware      # cores, workers, BLAS
   nsealgo gpu           # GPU choices per kernel
   nsealgo netcheck      # egress IP = static IP; latency
   ```
6. **First data:** run `nsealgo login fyers`, then `nsealgo fetch-history`.
7. **Try it:** run `nsealgo demo` (offline) and `nsealgo desk --demo`.
8. **Schedule:** `deploy\windows\register-tasks.ps1` creates the pre-market start; see [Runbook](runbook.md).

## Upgrading

Stop the session, replace the code, run `pip install -e .[dev,desk,fyers,upstox]` in the venv, then `pytest -q` and `nsealgo doctor`. Read the CHANGELOG for config changes.
