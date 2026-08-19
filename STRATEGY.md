# STRATEGY-LAB — Collaborative Early-Entry Doctrine

## THE ONE MISSION
**Know what to buy BEFORE it hits the crowds.** Every artifact in this repo
serves one question: *can we detect a token that is about to pump, early
enough to enter before retail volume arrives — with a measurable edge?*

This is a THREE-PANEL ENSEMBLE. Each panel owns a lane, but the strategy is
ONE document that converges. Panels read each other's outputs and iterate.

## The three lanes (who owns what)

### LANE 1 — SIGNAL ENGINE (panel-loom, branch: engine)
Owns: the early-entry signal math and parameters.
Question: *what measurable signals fire BEFORE the crowd buys?*
Candidate signals to evaluate (pick, parameterize, backtest):
- New pool creation / pump.fun graduation within last N minutes
- Bundler behavior: high bundler_rat_share at launch (snipers loaded)
- Dev wallet: token transfer patterns, liquidity seeding, dev sell risk
- Holder concentration curve: top-10 share over time — early pumps have
  distinctive signatures
- Volume-to-holders ratio: rising volume BEFORE price, not after
- GeckoTerminal/DexScreener OHLCV micro-structure: candle shape at launch
- GMGN signal stream (smart money buys, price spikes)
Deliverable: `engine/params.json` — a scored early-entry signal stack with
thresholds, plus `engine/backtest.md` showing each signal's lead time
(how many minutes before crowd volume it fires) on real historical data.

### LANE 2 — WHALE RADAR (panel-axiom, branch: radar)
Owns: first-mover wallet intelligence.
Question: *who buys first, and can we see them in real time?*
- Build the first-mover watchlist: wallets that repeatedly appear in the
  first 1-10 minutes of winning pumps (derive from wallet_intel.db roles,
  GMGN smart money/KOL track, Vantage alpha_wallets)
- Whale-cluster detection: >= N watchlist wallets buying the same token
  within a window = the "crowd hasn't arrived yet" proof
- KOL/ influencer mapping: wallet -> handle where derivable
Deliverable: `radar/watchlist.json` (ranked first-movers with conviction),
`radar/cluster_schema.md` (event schema for a buy-cluster alert), and a
live probe script that emits current clusters from wallet_intel.db.

### LANE 3 — CONVERGENCE HUB (panel-omokoda, branch: hub)
Owns: fusing the ensemble into ONE early-entry verdict.
Question: *what does the ensemble say about token X right now?*
- Define the EARLY-ENTRY SCORE: a single 0-100 number combining
  engine signals + radar clusters + existing fusion picks (S_signal,
  S_wallet) with explainable components
- Build the convergence endpoint: given a token address, return the
  full early-entry bundle (score, components, which lanes fired)
- Maintain this STRATEGY.md + `hub/verdicts/` — the running log of
  ensemble verdicts vs what actually happened (calibration)
Deliverable: `hub/score_schema.md` (the EARLY-ENTRY SCORE definition),
`hub/verdicts/` (daily verdict log), and the convergence endpoint.

## The convergence protocol (how the ensemble works)

1. Each panel pushes to its branch: `engine/`, `radar/`, `hub/`
2. The HUB panel (lane 3) merges lanes 1+2 into the score schema and
   publishes `hub/score_schema.md` — THE canonical definition
3. Signal engine and whale radar read the score schema and align their
   outputs to it (same JSON shape, same 0-100 scale)
4. Iterate: panels comment on each other's branches via README notes or
   `proposals/` files when they disagree. Orchestrator (Fold4) mediates
   disagreements and locks the final strategy
5. Lock signal: `hub/score_schema.md` marked `STATUS: LOCKED` + all three
   branches merged to main. Until then, STATUS: DRAFT

## Ground rules (ALL panels)
- Data sources (read freely): /opt/ares/wallet_intel/wallet_intel.db,
  /opt/ares/Vantage/data/vantage.db, gmgn-cli (6-key pool, use
  gmgn_cli_proxy.run_cli when IP-banned), DexScreener public API,
  GeckoTerminal (pace ~30 req/min), ares-signal-fusion picks :8003,
  pool health :8004. Solscan is paywalled/dormant — skip it.
- PAPER ONLY. No real trades, no order placement, ever.
- Never print full API keys in logs or commits.
- Every claim must be backed by real data (run the query, show the output).
- Own your lane's directory; read others'. Merge only via the hub.

## Reporting
Each panel ends sessions with: what you did, live-verified evidence,
commits pushed (SHAs), and the next iteration you recommend.
