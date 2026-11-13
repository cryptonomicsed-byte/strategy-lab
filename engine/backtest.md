# ENGINE BACKTEST — early-entry signal lead times (v1)

**Owner:** LANE 1 — SIGNAL ENGINE (panel-loom, branch: engine)
**STATUS: DRAFT** — lead times measured on a live sample; precision at 3/6/12h
pending resolved outcomes (see §5). Calibration continues via the hub verdicts
ledger (`hub/verdicts/`).

**The question:** what measurable signals fire BEFORE the crowd buys, and how
many minutes of lead time does each give? Data: real launches only.

---

## 1. Method

1. Universe: DexScreener `/token-profiles/latest/v1` (Solana only) — the most
   recent real launches at collection time.
2. For each mint: DexScreener pair stats (dex, liquidity, volume, txns) +
   GeckoTerminal pools (`pool_created_at` = launch time) + GeckoTerminal 1m
   OHLCV (up to 1000 candles ≈ 16.7h after launch).
3. **Launch time** `t0` = first OHLCV candle of the top pool.
4. **Crowd-volume arrival** = first 1m candle where trailing-5m volume ≥ $5k
   AND ≥ 5× the median trailing-5m volume of the first 30 minutes.
5. **2x pump** = first close ≥ 2× the first traded price.
6. **Lead time** = minutes from launch to the event. Forward returns are
   computed at 3h/6h/12h from launch where the history exists.
7. gmgn-cli probe (`token info --raw`) attempted on the first 3 tokens for the
   bundler/holder/dev fields — see §4 for what came back.

Collection script: `engine/backtest.py` (paced: GeckoTerminal ~2.2s/req).
Raw per-token output: `engine/leadtime_results.json`.

## 2. Dataset

- **20 launches** sampled (Solana, all pump.fun-family: pumpfun / pumpswap).
- 2 excluded (no OHLCV yet at sample time) → **18 usable**.
- Median pool age at sample: ~0h (brand-new launches, minutes old — exactly the
  early-entry regime).
- Median 24h volume across sampled tokens: ~$30–130k range; buys ≈ sells
  (retail-chasing profile, i.e. the crowd these signals must beat).

## 3. Measured lead times

| Signal (id) | Lead time measured | Median | n | Notes |
|---|---|---|---|---|
| `new_pool_graduation` | launch → crowd-volume arrival | **1.5 min** | 8 | Fires at t=0 by construction; crowd volume arrives a median 1.5 min later. This IS the raw early-entry window. |
| `volume_pressure` | volume surge → 2x price | **not separable** (n=0) | 0 | No token showed pump AFTER the crowd threshold tripped — pumps and crowd volume are near-simultaneous at launch. Volume-leads-price gap is ≤ 1 min in this regime, not minutes. |
| `new_pool_graduation` (pump catch) | launch → first 2x | **2.5 min** | 6 | Graduation freshness catches the pump leg at median 2.5 min; 6/18 sampled tokens (33%) doubled within ~17 min of launch. |
| `bundler_share` | — | — | 0 | Field unavailable at sample time (§4). Threshold from GMGN reference, unverified. |
| `dev_holding` | — | — | 0 | Field unavailable at sample time (§4). |
| `holder_concentration` | — | — | 0 | Field unavailable at sample time (§4). |

**Takeaway:** on fresh pump.fun-family launches the crowd arrives within ~1–3
minutes of pool creation. There is no minutes-long "volume before price" gap in
this sample — the edge is being *already positioned* at t≈0 with quality
filters (dev holding, bundler share, concentration) that keep you out of the
rugs, not in late-detection. `new_pool_graduation` is the timing signal; the
others are risk filters that gate the entry.

## 4. Field availability (honest gap)

The gmgn-cli probe (`token info --raw`) returned **no data for any of the 3
tokens probed** at collection time (fresh tokens, GMGN rate-limit/banned state
at 2026-08-19 collection window — the 6-key pool and proxy exist for retry).
Consequences:

- `bundler_share`, `dev_holding`, `holder_concentration` thresholds in
  `params.json` are taken from the GMGN response-field reference
  (gmgn-token skill): `stat.top_bundler_trader_percentage`, 
  `dev.creator_token_status`, `stat.top_10_holder_rate` /
  `stat.dev_team_hold_rate`. **Lead times and precision for these three are
  NOT yet measured** — marked unverified; first calibration pass is the
  immediate next iteration.
- The OHLCV/DexScreener signals (`new_pool_graduation`, `volume_pressure`) are
  fully measured on the 18-token sample above.

## 5. Precision at 3/6/12h

| Horizon | P(return ≥ +10%) | n | Median return | Status |
|---|---|---|---|---|
| 3h | 0% | 1 | −6.1% | anecdotal — 1 resolved outcome |
| 6h | 100% | 1 | +42.3% | anecdotal — 1 resolved outcome |
| 12h | — | 0 | — | no history yet (tokens ~0h old at sample) |

**n=1 is not precision.** The 18 sampled launches are minutes old; 3h/6h/12h
outcomes simply haven't happened yet. Precision will come from the running
calibration ledger: every hub verdict (`hub/verdicts/`) records entry price +
6h/24h follow-ups, and the engine fields above (`r_3h_pct`, `r_6h_pct`,
`r_12h_pct`) are computed by `engine/backtest.py` on every re-run. First
report once ≥ 20 verdicts resolve (hub calibration §7).

## 6. Reproduce

```bash
cd /opt/ares/strategy-lab
python3 engine/backtest.py --limit 20   # regenerates engine/leadtime_results.json
```

Pacing is built in (GeckoTerminal ≈30 req/min). No API keys required.

## 7. Changelog

- 2026-08-19 — v1 DRAFT: 20-launch sample (18 usable), crowd-arrival median
  1.5 min, pump-catch median 2.5 min, gmgn field gap documented.
