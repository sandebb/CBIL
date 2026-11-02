> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Release process

## Versioning

Semantic versioning in `pyproject.toml`:
- **MAJOR** changes behaviour users rely on (e.g. 3.0: stocks only).
- **MINOR** adds features (e.g. 3.8: the desk).
- **PATCH** fixes bugs (e.g. 3.6.1: the Fyers audit).

## Steps

1. **Scope:** the CHANGELOG entry lists every change, with any config changes called out.
2. **Test:** run the [test plan](../06-testing/test-plan.md), activities 1–4 (sandbox).
3. **Docs:**
   - update the documents in the [maintenance matrix](documentation-maintenance.md);
   - run `python scripts/docs/build_all.py`, which must leave no diff and no broken links;
   - write ADRs for any decisions.
4. **Version:** bump `pyproject.toml`; update the README's "New in" line and test count.
5. **Package:** a release zip (or a Git tag) of the repository without caches.
6. **Accept:** on the PX13, run test-plan activities 5–9 and the relevant [acceptance tests](../06-testing/acceptance-tests.md).
7. **Deploy:** stop the session; install over the venv (`pip install -e .[...]`); run `nsealgo doctor`; restart the scheduled task.
8. **Watch:** the first session after a release, in forward mode if the release touched trading paths.

## Rollback

Reinstall the previous release zip or tag, and restore `config/` if the release changed config. Session data in `runs/` is compatible across minor versions; check the CHANGELOG for exceptions.
