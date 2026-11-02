> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Setup & Run Instructions — Windows (Kaggle NSE Dataset)

Your parquet file is from the Kaggle dataset: [NSE Stock Data with sentiment and 80+ features](https://www.kaggle.com/datasets/charansv/nse-stock-data-with-sentiment-and-80-features). The column names and structure differ slightly from our pipeline, but I've created an adapter script to convert it automatically.

## Quick Setup (5 minutes)

### Step 1: Prepare Your Folder
```
%NSE_ROOT%\data\
├── final_cleaned.parquet          (your Kaggle data - already there)
├── prepare_kaggle_data.py          (adapter script - download)
├── run_pipeline_parquet.py         (main pipeline - download)
├── theory_validation.py            (theory tester - download)
├── features.py                     (feature engineer - download)
├── market.py                       (market context - download)
├── signals.py                      (signal generator - download)
├── validation.py                   (stat helpers - download)
└── run_validation.bat              (master script - download)
```

### Step 2: Download All Files
From this session's outputs folder, download these 9 files:
- `prepare_kaggle_data.py` ⭐ **NEW** (converts Kaggle format)
- `run_pipeline_parquet.py`
- `theory_validation.py`
- `features.py`
- `market.py`
- `signals.py`
- `validation.py`
- `run_validation.bat` (updated)
- Plus documentation files for reference

Place all in **%NSE_ROOT%** (alongside `final_cleaned.parquet`).

### Step 3: Run Everything

**Option A: Double-click the batch file (easiest)**
```
%NSE_ROOT%\data\run_validation.bat
```

**Option B: Run from PowerShell manually**
```powershell
cd %NSE_ROOT%

# Step 1: Install dependencies
python -m pip install -q pyarrow scikit-learn scipy numpy pandas

# Step 2: Convert Kaggle format to pipeline format
python prepare_kaggle_data.py final_cleaned.parquet kaggle_prepared.parquet

# Step 3: Generate signals (skip features since they're pre-computed)
python run_pipeline_parquet.py --input kaggle_prepared.parquet --skip-features --validate --fit --top 50

# Step 4: Detailed theory validation
python theory_validation.py data/signals/panel_features.parquet
```

## What Each Step Does

### Step 1: `prepare_kaggle_data.py` (Data Adapter)
Converts Kaggle column names to pipeline format:
- `Date` → `datetime`
- `Open_x`, `High_x`, `Low_x`, `Close_x`, `Volume_x` → standard OHLCV
- `Company_ID` or `COMPANY` → `symbol` (adds `NSE:` prefix)
- Renames 80+ technical/sentiment/macro features to simple names
- Removes rows with missing OHLCV
- Outputs: `kaggle_prepared.parquet`

**Runtime:** ~30 seconds

### Step 2: Pipeline with `--skip-features`
Since the Kaggle dataset already has 80+ pre-computed features:
- Skips re-computing features (`--skip-features` flag)
- Scores each stock using the pre-computed features
- V1: hand-weighted composite score
- V2: learns optimal weights (purged K-fold CV)
- Validates all 10 academic theories

**Runtime:** ~2-3 minutes

### Step 3: Theory Validation Report
Detailed test of 10 academic theories:
- Momentum (12-1, 6-1)
- Mean reversion (5-day)
- Trend following (golden cross, ADX)
- Delivery intensity
- Relative strength (vs market/sector)
- Volatility regime
- Lottery effect
- Market panic gate
- Beta & idiosyncratic vol
- Overnight/intraday decomposition

**Output:** IC (information coefficient), t-stats, p-values, backtest Sharpe ratio

**Total Runtime:** ~5-10 minutes

## Kaggle Dataset Details

Your data contains:

### Core OHLCV
- Open, High, Low, Close, Adjusted Close, Volume (daily bars)

### Technical Indicators (80+ features)
- Momentum: ROC, Momentum, ROC_5, ROC_10
- Trend: SMA, EMA, WMA, SAR, ADX (via ATR)
- Oscillators: RSI, MACD, Stochastic, CCI, OBV
- Volatility: ATR, Bollinger Bands, Rolling Std
- Volume: Volume-weighted metrics, On-Balance Volume
- Market structure: Pivot Points, Fibonacci levels
- Statistical: Z-Score, Kurtosis, Skewness, Sharpe Ratio

### Sentiment Features
- `sentiment_score`: Daily sentiment from news
- `Lagged_Sentiment_1/5/10`: Lagged sentiment
- `Weighted_Sentiment_Metric`: Composite sentiment

### Market Context (Benchmark & Correlation)
- Nifty50, SP500, FTSE100, DAX, Nikkei225
- Market Returns & Correlation
- Rolling Beta

### Macro Indicators
- INR/USD, Crude Oil, Gold prices
- VIX (volatility index)

## Expected Results

### Information Coefficient (IC)
Example output:
```
MOMENTUM (12-1)
  IC: +0.024 ± 0.018
  IR: +1.35
  t-stat: +2.12
  p-value: 0.0349  ✓ SIGNIFICANT
```

**What it means:**
- **IC > 0**: Past winners tend to outperform (momentum edge exists)
- **IC_IR > 1.0**: Consistent edge (not just noise)
- **p < 0.05**: Statistically significant (95% confidence)

### Signal Quality (Backtest)
```
Trades triggered:      142
Mean return (20d):     +2.34%
Sharpe ratio:          +1.85
Win rate:              52.1%
Best / Worst:          +14.56% / -8.23%
```

**What it means:**
- **Sharpe > 1.5**: Good signal
- **Sharpe > 2.0**: Strong signal (verify out-of-sample)
- **Win rate 50-60%**: Asymmetric payoff (small losses, bigger wins)

### V2 Model Performance
```
Purged-CV AUC: [0.560 0.548 0.552 0.555 0.551]
Mean AUC: 0.553

Top features by importance:
  mom_12_1:         +1.23
  rs_mkt_60:        +0.95
  sma50_gap:        +0.87
  atr_pct:          -0.72
```

**What it means:**
- **AUC = 0.5**: Random guessing
- **AUC = 0.55**: 5% edge (modest but tradeable)
- **AUC = 0.60+**: 10% edge (strong)

## Output Files

### 1. Signals CSV (Ready to Trade)
```
data/signals/long_term_latest.csv
```

Columns:
- `symbol`: NSE ticker
- `close`: Today's close
- `lt_score`: Composite score (-100..+100)
  - +40..+100: BUY
  - -25..+40: HOLD
  - < -25: SELL
- `lt_signal`: BUY/HOLD/SELL label
- `lt_momentum`: 12-month strength
- `lt_rel_strength`: vs market/sector
- `lt_trend`: Above 200-SMA?
- `rvol`: Relative volume (>1.5 = high participation)
- `market_score`: Overall market regime (-100..+100)
- `lt_stop`: Stop-loss level (close - 3×ATR)

### 2. Full Panel (For Research & Backtesting)
```
data/signals/panel_features.parquet
```

All computed features, signals, labels per (date, symbol).

### 3. Console Report
Validation results for all 10 theories with statistical significance.

## Troubleshooting

### "ModuleNotFoundError: No module named 'features'"
Make sure ALL .py files are in the same folder (%NSE_ROOT%).

### "KeyError: 'symbol' after conversion"
Check that Company_ID or COMPANY column exists in your parquet. Run:
```python
import pyarrow.parquet as pq
pf = pq.read_schema('final_cleaned.parquet')
print(pf.names)
```

### "Unable to find a usable engine; tried using 'pyarrow'"
Reinstall pyarrow:
```
pip install pyarrow --upgrade --force-reinstall
```

### Memory error / Process killed
Your file is large (2.6 GB). Try:
1. Close other applications
2. Use only the first year of data:
```python
import pandas as pd
df = pd.read_parquet('final_cleaned.parquet')
df = df[pd.to_datetime(df['Date']) >= '2024-01-01']
df.to_parquet('subset.parquet')
```
Then convert the subset.

### Signals look random / low Sharpe
This means the theories don't work strongly on your market. Check:
1. Theory validation results — do ANY theories have p < 0.05?
2. Regime breakdown — does IC vary by market condition?
3. Data quality — are all OHLCV columns populated?

## Next Steps After Validation

1. **Review theory validation results**
   - Which theories are significant (p < 0.05)?
   - Which work in bull vs bear markets?

2. **Examine the signals**
   - Sort `long_term_latest.csv` by `lt_score` (highest first)
   - Which stocks score highest?
   - Compare with your market view

3. **Paper-trade the signals**
   - Take top 20-30 BUY signals
   - Track them for 20 trading days
   - Measure real-time returns vs predictions

4. **Refit quarterly**
   - Feature weights change as markets evolve
   - Re-run `--fit` every 3 months
   - Monitor if theories still hold

## Questions?

See also:
- **QUICKSTART.md** — 3-step guide + FAQ
- **README_PARQUET_PIPELINE.md** — Full technical docs
- **PARQUET_PIPELINE_GUIDE.md** — Theory explanations

---

**Ready? Let's go!**

```powershell
cd %NSE_ROOT%
.\run_validation.bat
```
