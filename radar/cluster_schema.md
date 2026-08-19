# BUY-CLUSTER EVENT SCHEMA — whale radar (lane 2)

**Owner:** LANE 2 — Whale Radar (panel-axiom, branch `radar`)
**STATUS: DRAFT** — locked via the convergence protocol (STRATEGY.md §5) when
all three lanes land and `hub/score_schema.md` goes LOCKED.
**Consumed by:** LANE 3 — Convergence Hub (`hub/score.py` radar component,
`hub/score_schema.md` §2.2) and any panel reading `radar/clusters/`.

Every claim in this document is backed by the live probe:
`python3 radar/cluster_detect.py` against `/opt/ares/wallet_intel/wallet_intel.db`.

---

## 1. What a cluster is

A **buy-cluster** = `>= N` distinct watchlist wallets (conviction >= 0.50,
see `radar/watchlist.json`) whose **first recorded buy** of the same token
(`wallet_tokens.first_buy_ts`) falls inside the cluster window. The cluster
is the "crowd hasn't arrived yet" proof: when several ranked first-movers
independently buy the same token within minutes, that is the early-entry
signal the ensemble scores.

Wallet membership of a token is defined exactly as in `hub/score_schema.md`
§2.2: a wallet belongs when its row in `wallet_tokens(wallet, mint,
first_buy_ts)` is within the window — no other tables are consulted for
membership. `token_stats.buy_volume` is joined for context only.

## 2. Event JSON shape (every consumer uses this)

`radar/cluster_detect.py` prints one JSON wrapper to stdout and mirrors it to
`radar/clusters/latest.json` (+ a timestamped copy in live mode):

```json
{
  "schema_version": 1,
  "generated_at": "2026-08-19T20:12:57.586684Z",
  "ts": 1787170371.687,
  "mode": "live",
  "min_conviction": 0.5,
  "count": 33,
  "events": [
    {
      "token": "3Sa6oR...pump",
      "symbol": "POND",
      "wallet_count": 4,
      "conviction": 0.9008,
      "window_min": 180.0,
      "spread_min": 0.0,
      "volume_usd": 835.19,
      "band": "CRITICAL",
      "members": [
        {
          "wallet": "8dej9xeuvxsjwicypta9mhsu2rn2pdx37kwzkdkexhu6",
          "conviction": 0.9328,
          "first_buy_ts": 1787159807.588,
          "age_min": 176.1
        }
      ]
    }
  ]
}
```

### 2.1 Wrapper fields

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | int | 1 |
| `generated_at` | str | UTC ISO-8601 of the run |
| `ts` | float | epoch seconds of the run |
| `mode` | str | `live` \| `since` \| `all` (see §4) |
| `min_conviction` | float | conviction floor applied (default 0.50) |
| `count` | int | number of cluster events in `events` |
| `events` | array | cluster events, sorted by `wallet_count` desc, then `conviction` desc |

### 2.2 Cluster event fields

| Field | Type | Meaning |
|---|---|---|
| `token` | str | mint address (Solana) |
| `symbol` | str \| null | from `token_stats.symbol` |
| `wallet_count` | int | distinct watchlist wallets in the cluster (`n`) |
| `conviction` | float | mean conviction of cluster members (0..1) — feeds `k2·mean_conv` in the hub's `C_radar` |
| `window_min` | float \| null | cluster window in minutes (`null` in `all` mode) |
| `spread_min` | float | first-to-last member buy gap in minutes (cohesion) |
| `volume_usd` | float | `token_stats.buy_volume` for the token (0.0 if unknown) |
| `band` | str | `CRITICAL` \| `STRONG` \| `WEAK` (see §3) |
| `members` | array | one entry per watchlist wallet in the cluster |

Member fields: `wallet` (lowercased address), `conviction` (0..1),
`first_buy_ts` (epoch seconds), `age_min` (minutes since that buy at run
time).

## 3. Bands (severity of the alert)

| Band | Condition | Meaning |
|---|---|---|
| `CRITICAL` | `n >= 3` | three+ ranked first-movers co-bought — the ensemble treats this as the strongest radar evidence |
| `STRONG` | `n == 2` | two first-movers agree — real cluster, EARLY-capable per hub banding |
| `WEAK` | `n == 1` | single-wallet evidence — real but weak; never EARLY alone (score_schema.md §5) |

## 4. Modes and windows

| Mode | Window semantics | Typical use |
|---|---|---|
| `live` (default) | members whose `first_buy_ts` in `[now - window_min, now + 10min slack]`; `window_min` default 180 (hub's `RADAR_WINDOW_MIN`) | the real-time probe; exactly what `hub/score.py` `radar_component` consumes per token |
| `--since HOURS` | same logic, cutoff `now - HOURS*3600` | catching up after downtime |
| `--all` | no window filter; full stored history; `spread_min` is the co-buy cohesion | proving detection on the stored window, backfill, calibration |

`--min-wallets N` filters the emitted events (live default reports `n >= 1`
so weak single-wallet evidence is visible; `all`/`since` default to 2).
`--no-write` prints to stdout only.

## 5. Data sources and quality notes

- **Membership:** `wallet_intel.db` → `wallet_tokens(wallet, mint,
  first_buy_ts)` — read-only (`mode=ro`). This is the first recorded buy of
  that wallet for that mint, collected by the wallet_intel scanner's early
  window. **Semantic caveat:** it is the scanner's *first observed* buy, not
  a chain-level "genesis buy"; treat as *first within observed coverage*.
- **Watchlist:** `radar/watchlist.json` (address → conviction, built by
  `radar/build_watchlist.py`; hub's `load_watchlist()` lowercases addresses,
  so this probe lowercases too).
- **Volume:** `token_stats.buy_volume` — USDT-ish units as stored by the
  collector; context only, never gating.
- **Timestamp quirk:** `first_buy_ts` is ISO-8601 in current rows but epoch
  float in legacy rows; both are normalized (`ts_to_epoch`). Rows that parse
  to neither are skipped.
- **Exclusions:** WSOL/USDC/USDT mints and symbols are dropped — they are
  trading pairs, not first-mover targets.
- **Read-only:** the probe never writes to either database. Outputs go to
  `radar/clusters/` (gitignored — regenerated every run).

## 6. Alignment contract with the hub (do not drift)

`hub/score.py` computes `C_radar` from `wallet_tokens` ∩ watchlist with the
same membership rule, `RADAR_MIN_CONVICTION = 0.50`, `RADAR_WINDOW_MIN =
180`. This probe's `live` mode must stay field-for-field consistent:
`wallet_count` == hub `cluster.wallet_count`, mean `conviction` == hub
`cluster.conviction`, `volume_usd` == hub `cluster.volume_usd`. Any change
to window/conviction knobs must be mirrored in `hub/score.py` constants.

## 7. Changelog

- 2026-08-19 — v1 DRAFT: initial schema + live probe (`cluster_detect.py`),
  watchlist v1 (132 wallets, S/A/B tiers) via `build_watchlist.py`.
