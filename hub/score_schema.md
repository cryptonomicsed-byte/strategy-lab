# EARLY-ENTRY SCORE — canonical definition (v1)

**Owner:** LANE 3 — Convergence Hub (panel-omokoda, branch `hub`)
**STATUS: NEAR-LOCK** — hub lane finalization complete: schema v1 live-verified
against a real token (see §8) and `hub/score.py` records verdicts end-to-end.
The score degrades gracefully and reports exactly what it knows while lane 1/2
deliverables are outstanding.
**LOCKED** only when — per the convergence protocol — ALL of:
  1. lane 1 merges `engine/params.json` (+ optional `engine/fired_signals.json`)
  2. lane 2 merges `radar/watchlist.json` (repo copy replaces the axiom fallback)
  3. all three panels agree on this formula (disagreements via `proposals/`)
  4. all three branches merged to main and STRATEGY.md shows `STATUS: LOCKED`
Until then this document is the hub's final word; consumers can rely on it.

This document is the contract every consumer reads. It answers:
*what does the ensemble say about token X right now?*

---

## 1. The one number

    EARLY-ENTRY SCORE (0–100) = 100 × Σ(present_i × w_i × C_i) / Σ(present_i × w_i)

- `C_i` — each **component** normalized to [0, 1].
- `w_i` — component **weight** (below).
- `present_i` — the component has enough data to speak. Absent components are
  "no opinion", NOT negative evidence (same convention as ares-signal-fusion
  `scoring.py`). A component that is absent never drags the score down —
  but its absence is always reported in `data_quality`.
- If **no** component is present → `score = null`, verdict `NO`,
  reason `insufficient_data`.

### Weights (DRAFT — calibration will move them)

| Component | Weight | Source lane | What it measures |
|---|---|---|---|
| `engine`  | 0.35 | 1 (engine) | threshold-firing of early-entry signals from `engine/params.json` |
| `radar`   | 0.35 | 2 (radar)   | first-mover wallet cluster buying the token inside the window |
| `fusion`  | 0.30 | existing    | ares-signal-fusion pick: S_signal + S_wallet (S_market = gate context) |

Weights are equal-ish until real outcomes say otherwise. Calibration procedure
in §7. Any proposal to change weights goes through `proposals/` and is decided
by the orchestrator (Fold4) — never silently edited.

## 2. Components

### 2.1 `engine` — lane 1 signal stack

Reads `engine/params.json` (lane 1's scored signal stack) and, when lane 1
publishes live firings, `engine/fired_signals.json`.

**Contract with lane 1** — `engine/params.json` must be:

```json
{
  "schema_version": 1,
  "half_life_hours": 2.0,
  "signals": [
    {
      "id": "new_pool_graduation",
      "weight": 0.25,
      "direction": 1,
      "threshold": {"minutes_since_launch_max": 5},
      "half_life_hours": 2.0
    }
  ]
}
```

`engine/fired_signals.json` (optional live feed, one per token) must be:

```json
{"ts": 1787124715.0, "token": "0x…", "fired": [
  {"id": "new_pool_graduation", "strength": 0.8, "age_min": 3}
]}
```

**Value:**

    C_engine = logistic( Σ fired_i: w_i × strength_i × direction_i × decay(age_i) , scale=2.5 )

- `decay(age) = exp(-ln2 · age / half_life)` (true half-life).
- `present = true` iff `params.json` exists AND ≥ 1 signal could be evaluated.

**Status today:** lane 1 has not landed `params.json` → `engine.present = false`,
`engine.note = "params.json not landed"`. The score runs on radar+fusion only
and says so in `data_quality`.

### 2.2 `radar` — lane 2 first-mover cluster

Reads `radar/watchlist.json` (lane 2's ranked first-movers) — until it lands in
the repo, falls back to `/opt/ares/axiom/data/watchlist.json` and flags the
fallback. Cluster evidence is computed live from `wallet_intel.db`
(`wallet_tokens.first_buy_ts` ∩ watchlist, `token_stats.buy_volume`).

**Cluster** = distinct watchlist wallets with `conviction ≥ min_conviction`
whose first recorded buy of the token is within `cluster_window_min` (default
180 min) of `now`.

    n     = cluster size
    conv  = mean conviction of cluster wallets
    vol   = log1p(token buy_volume_usd)
    S_raw = k1·n + k2·conv + k3·vol/8        (k1=0.6, k2=1.2, k3=0.5)
    C_radar = logistic(S_raw, scale=2.5) × freshness_decay
    freshness_decay = exp(-ln2 · age_of_oldest_buy_min / 60)

- `present = true` iff ≥ 1 watchlist wallet bought the token inside the window.
- Single-wallet evidence is real but weak: by banding rule (§4) it can never
  produce `EARLY` by itself (`radar_cluster_min = 2` for EARLY).

### 2.3 `fusion` — existing ares-signal-fusion

Reads the latest pick row for the token from `ares_picks.db`
(`/opt/ares/ares-signal-fusion/ares_picks.db`, same store the :8003
`/api/picks` sidecar serves).

    S_signal, S_wallet = stored components (logistic-normalized 0..1, see
    ares-signal-fusion/signal_fusion/scoring.py — S_signal: net weighted pool
    agreement; S_wallet: net smart flow with quality×size×decay)

    C_fusion = (S_signal + S_wallet) / 2   when both present
             = the present one             when exactly one present
             = absent                      when neither present

- S_market is **not** averaged into C_fusion; it is gate context (§3) —
  liquidity floor and concentration penalties already live inside it.

## 3. Gates (vetoes)

A veto anywhere ⇒ `gates.passed = false` ⇒ verdict `NO` regardless of score.

| Gate | Source | Veto condition |
|---|---|---|
| `fusion_vetoes` | pick row `gates.vetoes` | any fusion veto present |
| `fusion_liq_floor` | S_market driver `liquidity_usd` | < $5,000 when S_market present |
| `insufficient_data` | data_quality | zero components present |

## 4. Verdict bands

| Verdict | Condition |
|---|---|
| `EARLY` | score ≥ 70 AND no vetoes AND ≥1 of: engine fired, radar cluster ≥ 2 wallets, fusion rank ≤ 3 |
| `WATCH` | score ≥ 40 AND < 70, no vetoes |
| `NO` | score < 40, OR any veto, OR insufficient data |

Verdict is the single word consumers act on; `score` + `fired_signals` +
`components[].drivers` are the proof.

## 5. Minimum data requirements

The score must say "I don't know" rather than guess:

- **Zero components present** → `score: null`, `verdict: NO`,
  `reason: insufficient_data`.
- **One component present** → score computed but `data_quality.min_data =
  "partial"`, and the verdict can never be `EARLY` (single-lane evidence).
- **Engine absent + radar cluster < 2 + fusion rank > 3** → verdict capped
  at `WATCH` by the banding rules above.
- Every bundle carries `data_quality: {engine_available, radar_available,
  fusion_available, min_data}` so consumers see the epistemic state, not just
  the number.

## 6. JSON shape (every consumer uses this)

```json
{
  "schema_version": 1,
  "status": "NEAR-LOCK",
  "token": "7c9sXHS879M9fq9Gex4pCSHYGeBDVK7yErKGMAxypump",
  "symbol": "CATURN",
  "ts": 1787124715.41,
  "score": 62.5,
  "verdict": "WATCH",
  "reason": "fusion fired, radar below cluster threshold",
  "components": {
    "engine": {"present": false, "value": null, "weight": 0.35,
               "drivers": [], "note": "params.json not landed"},
    "radar":  {"present": true, "value": 0.61, "weight": 0.35,
               "cluster": {"wallet_count": 2, "conviction": 0.62,
                           "window_min": 180, "volume_usd": 583.95},
               "drivers": [{"wallet": "GKCU…", "conviction": 0.62,
                            "first_buy_ts": 1787124400.0}]},
    "fusion": {"present": true, "value": 0.66, "weight": 0.30,
               "pick_id": 45, "rank": 1, "entry_price": 0.0001892,
               "drivers": {"S_signal": {"value": 0.5925, "present": true},
                           "S_wallet": {"value": 0.7832, "present": true},
                           "S_market": {"value": 0.467, "present": true}}}
  },
  "fired_signals": [
    "fusion:S_wallet=0.78 (4 smart wallets)",
    "radar:cluster=2 wallets within 180min"
  ],
  "gates": {"passed": true, "vetoes": []},
  "data_quality": {"engine_available": false, "radar_available": true,
                   "fusion_available": true, "min_data": "partial"},
  "followups": {"6h": null, "24h": null}
}
```

- `followups` is the calibration hook: filled by `hub/score.py --mark 6h|24h`
  with `{"price": …, "return_pct": …, "ts": …}` or `{"error": …}`.
- Verdicts are appended to `hub/verdicts/YYYY-MM-DD.json` — the running
  calibration ledger (see hub/README.md).

## 7. Calibration (prove it works over time)

1. Every verdict records `entry_price` (from the fusion pick when available).
2. Follow-up hook refetches price at 6h / 24h (`gmgn-cli token info --raw`).
3. Once ≥ 20 resolved verdicts exist, the hub computes mean return by dominant
   component (mirroring `PickStore.calibration_report()`) and writes a
   calibration note to `proposals/` — REPORT ONLY, weights change only via the
   orchestrator.
4. Lock signal: schema `STATUS: LOCKED` + all three branches merged to main.

## 8. Changelog

- 2026-08-19 — v1 DRAFT: initial canonical definition (hub lane seed).
- 2026-08-19 — v1 FINALIZATION: hub lane schema complete → `STATUS:
  NEAR-LOCK`. Live-verified: `hub/score.py` ran against real token
  Kirkiversary (`915YcCbtupYLTz3RwfKjYngzBG2Bk2RX8We4D2HSpump`, fusion rank 1)
  → score 41.94, verdict WATCH, gates passed, verdict appended to the ledger.
  Full LOCK pending lane 1/2 deliverables + 3-panel sign-off (see header).
