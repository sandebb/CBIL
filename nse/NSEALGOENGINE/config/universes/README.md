# Universes

Put NSE index constituent CSVs here, named after the universe:

- `nifty500.csv`, `nifty100.csv`, `nifty50.csv` from niftyindices.com (Indices > constituent list, the
  `ind_nifty500list.csv` style files). Required column: `Symbol`. `Industry` enables the sector cap.
- `fno.csv` (column `Symbol`) is optional: the F&O stock list is derived automatically from your data
  broker's futures master when you log in.
- Any other `name.csv` with a `Symbol` column becomes a universe you can reference in config.

Refresh them after every semi-annual index reconstitution.
