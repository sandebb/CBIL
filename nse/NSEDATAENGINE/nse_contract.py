r"""
nse_contract.py - reads contract\\schema.yaml, the single source of truth for every column in the parquet
files NSEDATAENGINE publishes, and gives every script (here, in NSEALGOENGINE and in NSEMLENGINE) the same
three operations:

  columns(dataset, variant)   the contract's column list (writers use it instead of hard-coded lists)
  conform(df, dataset, ...)   make a frame match the contract before it is written:
                                - a contract column the code does not produce yet -> added as null + warning
                                - types cast to the contract types
                                - contract columns first, in contract order
                                - columns the contract does not know are KEPT at the end (+ warning),
                                  so a writer never silently destroys data it does not understand
  read(dataset, columns=...)  read only the named columns, with a clear error naming the contract when a
                              column is missing. Readers that name their columns are not affected when
                              columns are added to a file.

Rules for changing columns are at the top of contract\\schema.yaml.

Command line:
  python nse_contract.py validate            check every published file against the contract
  python nse_contract.py show [dataset]      print the contract
  python nse_contract.py paths               where every folder / file resolves on this PC
  python nse_contract.py env [--home DIR]    the environment variables register_project.ps1 sets
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import os
import sys
from pathlib import Path

runtime_deps.ensure_packages("yaml", "polars")
import yaml
import polars as pl

CONTRACT_PATH = Path(__file__).resolve().parent / "contract" / "schema.yaml"
HOME_ENV_VAR = "NSE_ROOT"
HOME_ENV_VARS = ("NSE_ROOT", "NSEDATAENGINE_HOME", "NSE_ALGO_HOME")
_TYPES = {"string": pl.String, "float": pl.Float64, "int": pl.Int64, "int32": pl.Int32, "datetime": pl.Datetime("ms"),
          "date": pl.Date, "bool": pl.Boolean}
_cache: dict = {}
_warned: set = set()


def manifest(path: str | Path | None = None) -> dict:
    """The parsed contract (re-read automatically when the file changes)."""
    p = Path(path) if path else CONTRACT_PATH
    key = (str(p), p.stat().st_mtime)
    if key not in _cache:
        with open(p, encoding="utf-8") as f:
            _cache[key] = yaml.safe_load(f)
    return _cache[key]


def _schema(lf):
    """Schema of a LazyFrame on old and new polars versions."""
    return dict(lf.collect_schema() if hasattr(lf, "collect_schema") else lf.schema)


def version() -> str:
    return str(manifest()["contract_version"])


def _ds(dataset: str) -> dict:
    ds = manifest()["datasets"]
    if dataset not in ds:
        raise KeyError(f"dataset '{dataset}' is not in {CONTRACT_PATH} (known: {', '.join(ds)})")
    return ds[dataset]


def _in_variant(spec: dict, variant: str | None) -> bool:
    return variant is None or "in" not in spec or variant in spec["in"]


def columns(dataset: str, variant: str | None = None, adjust: str | None = None) -> list[str]:
    """Contract columns of a dataset, in contract order. variant = master / fresh (columns marked `in:`
    for other variants are left out); adjust = price / volume / none filters on the corporate-action rule."""
    out = []
    for name, spec in _ds(dataset)["columns"].items():
        if _in_variant(spec, variant) and (adjust is None or spec.get("adjust", "none") == adjust):
            out.append(name)
    return out


def dtypes(dataset: str, variant: str | None = None) -> dict:
    cols = _ds(dataset)["columns"]
    return {c: _TYPES[cols[c]["type"]] for c in columns(dataset, variant)}

def nse_root() -> Path:
    env = os.environ.get("NSE_ROOT", "").strip().strip('"')
    if env and Path(env).is_dir():
        return Path(env).resolve()
    for parent in Path(__file__).resolve().parents:
        if (parent / "code").is_dir() and (parent / "data").is_dir():
            return parent
    return Path(__file__).resolve().parents[2]

def data_root() -> Path:
    env_data = os.environ.get("NSE_DATA_ROOT", "").strip().strip('"')
    if env_data:
        return Path(env_data).resolve()
    return (nse_root() / "data").resolve()

def project_home() -> Path:
    env_home = os.environ.get("NSEDATAENGINE_HOME", "").strip().strip('"')
    if env_home and Path(env_home).is_dir():
        return Path(env_home).resolve()
    return (nse_root() / "code" / "NSEDATAENGINE").resolve()


def _env(name: str | None) -> str:
    return os.environ.get(name, "").strip().strip('"') if name else ""


def location(name: str, home: str | Path | None = None, use_env: bool = True) -> Path:
    locs = manifest().get("locations", {})
    if name not in locs:
        raise KeyError(f"location '{name}' is not in {CONTRACT_PATH} (known: {', '.join(locs)})")
    spec = locs[name]
    if use_env and home is None and _env(spec.get("env")):
        return Path(_env(spec.get("env")))
    if home is not None:
        return (Path(home) / spec["dir"]).resolve()
    if name == "algo_engine":
        return (nse_root() / "code" / "NSEALGOENGINE").resolve()
    shared_rel = spec.get("shared") or f"NSEDATAENGINE/{spec['dir']}"
    return (data_root() / shared_rel).resolve()

def file_path(dataset: str, variant: str | None = None, home: str | Path | None = None,
              folder: str | Path | None = None) -> Path:
    """Full path of a dataset's file. variant = master / fresh / ref (default: the first in `files:`).
    folder = an explicit folder (e.g. a --data-dir option) instead of the contract location."""
    files = _ds(dataset)["files"]
    variant = variant or next(iter(files))
    if variant not in files:
        raise KeyError(f"{dataset} has no '{variant}' file in the contract (has: {', '.join(files)})")
    return Path(folder or location(variant, home)) / files[variant]


def env_assignments(home: str | Path | None = None) -> list[tuple[str, str]]:
    """(variable, value) pairs register_project.ps1 sets for the Windows user: the root + every location."""
    root = Path(home or project_home()).resolve()
    out = [(HOME_ENV_VAR, str(root))]
    for name, spec in manifest().get("locations", {}).items():
        if spec.get("env"):
            out.append((spec["env"], str(location(name, root))))
    return out


def _warn(log, msg):
    if msg not in _warned:
        _warned.add(msg)
        (log or (lambda m: print(m, file=sys.stderr)))(f"contract: {msg}")


def conform(df: pl.DataFrame, dataset: str, variant: str | None = None, log=None,
            keep_unknown: bool = True) -> pl.DataFrame:
    """Return df shaped as the contract says (see module docstring). Use right before every write."""
    want = dtypes(dataset, variant)
    missing = [c for c in want if c not in df.columns]
    if missing:
        _warn(log, f"{dataset}: not produced by the code yet, written as null: {missing}")
        df = df.with_columns([pl.lit(None, dtype=want[c]).alias(c) for c in missing])
    df = df.with_columns([pl.col(c).cast(t, strict=False) for c, t in want.items() if df.schema[c] != t])
    extra = [c for c in df.columns if c not in want]
    if extra and keep_unknown:
        _warn(log, f"{dataset}: columns not in the contract kept as they are (add them to "
                   f"{CONTRACT_PATH.name} or drop them): {extra}")
    return df.select(list(want) + (extra if keep_unknown else []))


def read(dataset: str, columns: list[str] | None = None, variant: str = "master",
         home: str | Path | None = None, path: str | Path | None = None, lazy: bool = False):
    """Read the named columns of a published file (all contract columns present if columns=None).
    Raises a clear error if a named column is not in the file."""
    p = Path(path) if path else file_path(dataset, variant, home)
    lf = pl.scan_parquet(p)
    have = list(_schema(lf))
    if columns is None:
        columns = [c for c in globals()["columns"](dataset) if c in have]
    known = set(_ds(dataset)["columns"])
    unknown = [c for c in columns if c not in known]
    if unknown:
        _warn(None, f"{dataset}: reading columns that are not in the contract: {unknown}")
    absent = [c for c in columns if c not in have]
    if absent:
        raise KeyError(f"{p} has no column(s) {absent}. The contract ({CONTRACT_PATH}) lists "
                       f"{dataset} columns: {list(known)}")
    lf = lf.select(columns)
    return lf if lazy else lf.collect()


def require(have_columns, dataset: str, needed: list[str], who: str) -> None:
    """Fail early, with a useful message, when a consumer's columns are not present."""
    absent = [c for c in needed if c not in set(have_columns)]
    if absent:
        raise KeyError(f"{who} needs {dataset} column(s) {absent}, which are not in the file. "
                       f"See {CONTRACT_PATH} (contract {version()}).")


def validate(home: str | Path | None = None) -> list[str]:
    """Check every published file: required columns present with the contract type, no unknown columns."""
    problems = []
    for ds, spec in manifest()["datasets"].items():
        for variant in spec["files"]:
            p = file_path(ds, variant, home)
            if "YYYY-MM-DD" in p.name:                   # one file per day (e.g. preopen): check the newest one
                days = sorted(p.parent.glob(p.name.replace("YYYY-MM-DD", "????-??-??")))
                if not days:
                    continue                             # nothing recorded yet - not an error
                p = days[-1]
            rel = str(p)
            if not p.exists():
                problems.append(f"{rel}: missing")
                continue
            sch = _schema(pl.scan_parquet(p))
            want = dtypes(ds, variant)
            for c, t in want.items():
                if c not in sch:
                    problems.append(f"{rel}: contract column '{c}' missing")
                elif sch[c] != t:
                    problems.append(f"{rel}: '{c}' is {sch[c]}, contract says {t}")
            for c in sch:
                if c not in want:
                    problems.append(f"{rel}: column '{c}' is not in the contract")
    return problems


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "validate"
    if cmd == "validate":
        print(f"contract {version()}  ({CONTRACT_PATH})")
        probs = validate(sys.argv[2] if len(sys.argv) > 2 else None)
        print("\n".join(probs) if probs else "all files match the contract")
        sys.exit(1 if probs else 0)
    elif cmd == "paths":
        print(f"root  {project_home()}   ({HOME_ENV_VAR}={_env(HOME_ENV_VAR) or '<not set>'})")
        for name, spec in manifest().get("locations", {}).items():
            src = "env" if _env(spec.get("env")) else ("shared" if _env("NSE_DATA_ROOT") and spec.get("shared")
                                                           else "default")
            print(f"  {name:18s} {str(location(name)):60s} [{src}: {spec.get('env')}]")
        for ds, spec in manifest()["datasets"].items():
            for v in spec["files"]:
                p = file_path(ds, v)
                print(f"  {ds + ':' + v:18s} {str(p):60s} {'ok' if p.exists() else 'MISSING'}")
    elif cmd == "env":   # used by migrate\register_project.ps1: one NAME=value line per variable
        home = sys.argv[sys.argv.index("--home") + 1] if "--home" in sys.argv else None
        for k, v in env_assignments(home):
            print(f"{k}={v}")
    elif cmd == "show":
        for ds in ([sys.argv[2]] if len(sys.argv) > 2 else manifest()["datasets"]):
            print(f"{ds}: {', '.join(columns(ds))}")
    else:
        sys.exit(__doc__)
