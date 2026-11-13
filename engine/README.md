# ENGINE — how to re-run the backtest

**Lane 1 (panel-loom) of the strategy-lab early-entry ensemble.**

## Files

- `params.json` — the scored early-entry signal stack (5 signals: id, weight,
  direction, thresholds, min_confidence, data_source, half_life). Aligned to
  the hub contract (`hub/score_schema.md` §2.1). This is what the hub reads
  for the `engine` component of the EARLY-ENTRY SCORE.
- `backtest.py` — data collector + lead-time/precision computation. Sources:
  DexScreener token-profiles (universe), DexScreener pairs (stats),
  GeckoTerminal pools + 1m OHLCV (launch time + volume/price path),
  gmgn-cli token info (bundler/holder/dev fields, probe).
- `backtest.md` — the results: measured lead times per signal, precision
  status at 3/6/12h, and the honest field-availability gaps.
- `leadtime_results.json` — raw per-token output of one run (20 launches).

## Run it

```bash
cd /opt/ares/strategy-lab
python3 engine/backtest.py            # uses ~20 most recent Solana profiles
python3 engine/backtest.py --limit 30 # bigger sample
```

Pacing is built in (GeckoTerminal ≈ 2.2s/req ≈ 30 req/min ceiling).
No API keys needed for DexScreener/GeckoTerminal. gmgn-cli needs its key
(`gmgn-cli config --check`); if the VPS IP is banned, use the proxy runner in
`/opt/ares/wallet_intel/gmgn_cli_proxy.py` per STRATEGY.md.

## Known limits (v1)

- Sample is fresh-launch heavy (~0h old): 3h/6h/12h outcomes are mostly
  censored → precision numbers are anecdotal until the hub verdicts ledger
  accumulates ≥ 20 resolved verdicts.
- gmgn fields (bundler/dev/holder) returned no data at the 2026-08-19
  collection window (GMGN rate limit) — their thresholds in params.json are
  reference-based and unverified; re-run with gmgn-cli healthy to calibrate.
