> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Moving the NSE data project to a new PC

## Moving everything to a new PC (all four projects) - start here

Two commands. Every project folder under `%NSE_ROOT%` with a `project_manifest.json` is included automatically
(NSEDATAENGINE, NSEALGOENGINE, NSEMLENGINE, NSEUIENGINE, and any project added later).

**Old PC** (any PowerShell window):

```
powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\prepare_move.ps1 -Destination E:\move
```

Registers every project, saves the settings variables (STATICIP_PROXY, STATIC_IP, FYERS_ID, FYERS_CLIENT_ID,
FYERS_REDIRECT_URI, PYTHONUTF8) and packs everything into `E:\move\migration_<date>\` with
`setup_new_pc.ps1` inside. Add `-IncludeRegenerable` to also carry the feature files and the NSEMLENGINE dataset.

**New PC** (normal PowerShell, not admin), inside the copied bundle folder:

```
powershell -ExecutionPolicy Bypass -File .\setup_new_pc.ps1            # options: -Root D:\algo  -Gpu  -Skip NSEUIENGINE
```

It installs Python 3.12 if needed (winget, per user), checks and unpacks every project to `%NSE_ROOT%\data\<folder>`,
restores the settings variables, then runs each project's own `setup` steps from its `project_manifest.json`:

| Order | Project | What its setup does |
|---|---|---|
| 1 | NSEDATAENGINE | packages into the `python` on PATH; folder variables; scheduled task "NSE data update"; contract + data check |
| 2 | NSEALGOENGINE | `.venv` + nsealgo + broker SDKs (`-AlgoExtras`, `-Gpu` for PyTorch ROCm); `NSEALGOENGINE_HOME`; `nsealgo doctor` |
| 3 | NSEMLENGINE | packages; `NSEMLENGINE_HOME` / `_WORK_DIR`; rebuilds `work\dataset_v2.parquet` if not moved; look-ahead test |
| 4 | NSEUIENGINE | `.venv`; **asks for the Fyers credentials again** (DPAPI secrets cannot move); token + trader tasks |

A summary table and a log (`%USERPROFILE%\AlgoSetup\setup_<date>.log`) show every step. Re-running is safe.
Without a bundle (folders copied some other way), the same script sets up the folders found under `-Root`.

To add a step for a project: add it to the `"setup"` block of that project's `project_manifest.json`
(`{python}`, `{root}`, `{datahome}` are filled in; `"when": "gpu" | "nogpu" | "forwardtest"` makes it optional).

The bundle can contain credentials (NSEALGOENGINE `.env` if present, STATICIP_PROXY): keep it private and
delete it after the move.


Everything in this project uses paths relative to one folder, the **project folder**. The scripts find that folder through the user environment variable **`NSEDATAENGINE_HOME`** (falling back to the folder the scripts are in). Moving the project therefore means: copy the folder's contents in the right order, point `NSEDATAENGINE_HOME` at the new location, and re-create the scheduled task. The scripts in `migrate\` do all of this.

## Project layout

| Path (inside the project folder) | What it is | Move it? |
|---|---|---|
| `nse_common.py` | Shared code: package bootstrap, project location, indicator formulas | **Yes** (part 1) |
| `contract\schema.yaml`, `nse_contract.py` | Column contract: the source of truth for every column in every parquet file, and the code that enforces it (`python nse_contract.py validate`) | **Yes** (part 1) |
| `nse_updater.py` | Daily/weekly updater | **Yes** (part 1) |
| `nse_fresh_build.py` | 5-year fresh build with audit trail | **Yes** (part 1) |
| `run_update.bat` | What the scheduled task runs | **Yes** (part 1) |
| `project_manifest.json` | The list below, in machine-readable form | **Yes** (part 1) |
| `requirements.txt`, `MIGRATION.md`, `migrate\*.ps1` | Package list, this file, migration tools | **Yes** (part 1) |
| `data\raw_ohlcv.parquet` | Master store: every adjusted price, 1991 to today | **Yes, essential** (part 2) |
| `data\market.parquet` | Nifty, VIX and global markets per day | **Yes, essential** (part 2) |
| `data\logs\` | Update log, corporate-action ledger, renames, review logs | **Yes, essential** (part 2) |
| `data_fresh\raw_ohlcv.parquet`, `market.parquet`, `audit\`, `logs\` | 5-year build and its audit trail | Recommended (part 3) |
| `data\cache\`, `data_fresh\cache\` | Every NSE file downloaded so far | Recommended (part 4) |
| `data\features_output_clean.parquet`, `data_fresh\features_5y.parquet` | Feature files | No, they're rebuilt in minutes (part 5, only with `-IncludeRegenerable`) |
| `%NSE_ROOT%\data\features_output*.parquet`, `data_quality_report.csv`, `build_features_clean.py` | The original files the project started from | Optional (part 6, restored under `archive\`) |

Why this order: the code comes first so that a partly copied bundle is still runnable. The irreplaceable state comes next, then what is merely expensive to recreate (the cache), and finally what can be rebuilt.

## Environment variables (folders)

No script contains a folder or file name. Folders are defined once, in `contract\schema.yaml` (`locations:`), and
each one can be moved with its user environment variable. `migrate\register_project.ps1` (and
`unpack_project.ps1`) set all of them through `migrate\set_env_vars.ps1`; `python nse_contract.py paths` shows
where everything resolves.

| Variable | Default | What |
|---|---|---|
| `NSEDATAENGINE_HOME` | the project folder | project root |
| `NSEDATAENGINE_DATA_DIR` | `<root>\data` | updater store (1991 -> today), cache, logs |
| `NSEDATAENGINE_FRESH_DIR` | `<root>\data_fresh` | 5-year rebuild, cache, audit |
| `NSEDATAENGINE_REF_DIR` | `<root>\data_ref` | F&O universe + cache |
| `NSEDATAENGINE_EVENTS_DIR` | `<root>\data\events` | results calendar cache |
| `NSEDATAENGINE_BACKTEST_DIR` | `<root>\backtest_results` | nse_backtest.py runs |
| `NSEDATAENGINE_PORTFOLIO_DIR` | `<root>\portfolio_results` | nse_portfolio.py runs |
| `NSEALGO_SRC` | `<root>\..\NSEALGOENGINE` | NSEALGOENGINE v3.11 source used by the backtest |

`NSE_ALGO_HOME` is the old name of `NSEDATAENGINE_HOME` (folder `%NSE_ROOT%\data\nse_updater` until 3 Oct 2026); the
scripts still honour it and register_project.ps1 removes it.

## One-time setup on the current PC

Run this once. It sets `NSEDATAENGINE_HOME`, records the project in `%LOCALAPPDATA%\AlgoProjects\projects.json` and re-creates the scheduled task at the current path:

```
powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\register_project.ps1 -ScheduledTask
```

Other projects (for example your trading-system code) can join the same system. Give each one a `project_manifest.json` with its own `env_var` and parts, copy the `migrate\` folder into it and run its `register_project.ps1`. `pack_project.ps1` packs every registered project together.

## On the old PC: pack

```
powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\pack_project.ps1 -Destination E:\move
```

This creates `E:\move\migration_<date>\` containing:

* numbered zips per project;
* `bundle_manifest.json`, with a SHA-256 checksum for every zip and the original paths;
* `unpack_project.ps1`;
* a short `README.txt`.

Options:

* `-IncludeRegenerable` also packs the feature files.
* `-RequiredOnly` packs only the essential parts.
* `-Project nse_data` packs one project.

## On the new PC: unpack

1. Install Python 3.11 or later from python.org and tick **Add python.exe to PATH**.
2. Copy the `migration_<date>` folder to the new PC.
3. In that folder, run:

   ```
   powershell -ExecutionPolicy Bypass -File .\unpack_project.ps1
   ```

   For each project you are asked for a folder; press Enter to keep the old path. The script then:

   * checks every zip's checksum, and refuses a damaged copy rather than unpacking it;
   * unpacks the zips in order, keeping all relative paths;
   * sets the environment variable and registers the project;
   * installs the Python packages;
   * runs `python nse_updater.py check` to show the data it found;
   * offers to re-create the scheduled task.

   A log is written to `<project>\migrate\unpack_log.txt`.

4. Open a new terminal and run `python nse_updater.py features` to rebuild the feature file, unless you packed it. After that, `python nse_updater.py update` continues exactly where the old PC stopped.
