#!/usr/bin/env python3
"""
engine/backtest.py — lead-time + precision collection for the early-entry signal stack.

Sources (all public / read-only, per STRATEGY.md):
  - DexScreener /token-profiles/latest/v1  -> universe of recent launches
  - DexScreener /tokens/v1/solana/<mint>   -> pair stats (liq, vol, txns)
  - GeckoTerminal pools + 1m OHLCV          -> launch time (pool_created_at),
                                              crowd-volume arrival, forward returns
  - gmgn-cli token info (probe, few tokens) -> bundler / holder / dev fields

Pacing: GeckoTerminal ~2.2s/host (≈30 req/min), DexScreener 1s/host.

Output: engine/leadtime_results.json + a printed summary table.
Re-run: python3 engine/backtest.py [--limit N]
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from statistics import median

GT_BASE = "https://api.geckoterminal.com/api/v2"
DEX_BASE = "https://api.dexscreener.com"
GT_NETWORK = "solana"
UA = "strategy-lab-engine/1.0 (early-entry-signal-backtest)"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leadtime_results.json")

PACE = {"api.geckoterminal.com": 2.2, "api.dexscreener.com": 1.0}
_last = {}


def fetch(url, retries=3):
    host = url.split("/")[2]
    wait = PACE.get(host, 1.5) - (time.time() - _last.get(host, 0))
    if wait > 0:
        time.sleep(wait + 0.15)
    _last[host] = time.time()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503):
                time.sleep(min(2 ** attempt, 8))
                continue
            if e.code == 404:
                return {}
            return {}
        except Exception:
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 8))
                continue
            return {}
    return {}


def iso_ts(s):
    try:
        return time.mktime(time.strptime(s.replace("Z", "+0000").split("+")[0], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def gt_pools(mint):
    d = fetch(f"{GT_BASE}/networks/{GT_NETWORK}/tokens/{mint}/pools?page=1")
    pools = d.get("data") or []
    pools.sort(key=lambda p: float((p.get("attributes") or {}).get("reserve_in_usd") or 0), reverse=True)
    return pools


def gt_ohlcv(pool, tf="minute", limit=1000):
    d = fetch(f"{GT_BASE}/networks/{GT_NETWORK}/pools/{pool}/ohlcv/{tf}?aggregate=1&limit={limit}")
    rows = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    rows = [r for r in rows if isinstance(r, list) and len(r) >= 6]
    rows.sort(key=lambda r: r[0])
    return rows  # [ts, o, h, l, c, v]


def dex_pairs(mint):
    d = fetch(f"{DEX_BASE}/tokens/v1/{GT_NETWORK}/{mint}")
    if isinstance(d, list):
        d.sort(key=lambda p: (p.get("liquidity") or {}).get("usd", 0) or 0, reverse=True)
        return d
    return []


def gmgn_info(mint):
    """Probe gmgn-cli token info; returns raw dict or {} on failure."""
    try:
        out = subprocess.run(
            ["gmgn-cli", "token", "info", "--chain", "sol", "--address", mint, "--raw"],
            capture_output=True, text=True, timeout=45,
        )
        if out.returncode == 0 and out.stdout.strip():
            return json.loads(out.stdout)
    except Exception:
        pass
    return {}


def analyze(mint, symbol):
    rec = {"mint": mint, "symbol": symbol, "errors": []}
    pairs = dex_pairs(mint)
    if pairs:
        p = pairs[0]
        rec["pair"] = {
            "dex": p.get("dexId"), "liq_usd": (p.get("liquidity") or {}).get("usd"),
            "vol_h24": (p.get("volume") or {}).get("h24"),
            "txns_buy": ((p.get("txns") or {}).get("h24") or {}).get("buys"),
            "txns_sell": ((p.get("txns") or {}).get("h24") or {}).get("sells"),
        }
    pools = gt_pools(mint)
    if not pools:
        rec["errors"].append("no GT pool")
        return rec
    a = pools[0].get("attributes") or {}
    rec["pool"] = a.get("address")
    rec["pool_created_at"] = a.get("pool_created_at")
    launch = iso_ts(a.get("pool_created_at") or "")
    rows = gt_ohlcv(rec["pool"]) if rec["pool"] else []
    if not rows:
        rec["errors"].append("no ohlcv")
        return rec

    ts = [r[0] for r in rows]
    vol = [float(r[5] or 0) for r in rows]
    close = [float(r[4] or 0) for r in rows]
    t0 = ts[0]
    if launch:
        rec["age_hours_at_sample"] = round((t0 - launch) / 3600, 2)
    # first traded price (first candle with volume > 0)
    entry_i = next((i for i, v in enumerate(vol) if v > 0), None)
    if entry_i is None:
        rec["errors"].append("no traded candles")
        return rec
    entry = close[entry_i]
    rec["entry_price"] = entry

    # crowd-volume arrival: first 1m candle where trailing-5m volume >= $5k
    # AND >= 5x the median trailing-5m volume of the first 30 minutes
    base = []
    for i in range(entry_i, min(len(vol), entry_i + 30)):
        base.append(sum(vol[max(entry_i, i - 4): i + 1]))
    med5 = median(base) if base else 0
    crowd_i = None
    for i in range(entry_i, len(vol)):
        s5 = sum(vol[max(entry_i, i - 4): i + 1])
        if s5 >= 5000 and s5 >= 5 * max(med5, 1):
            crowd_i = i
            break
    rec["crowd_ts"] = ts[crowd_i] if crowd_i is not None else None
    rec["crowd_lead_min"] = round((ts[crowd_i] - t0) / 60, 1) if crowd_i is not None else None

    # first 2x price pump (relative to entry)
    pump_i = next((i for i in range(entry_i, len(close)) if close[i] >= 2 * entry), None)
    rec["pump2x_ts"] = ts[pump_i] if pump_i is not None else None
    rec["pump2x_min"] = round((ts[pump_i] - t0) / 60, 1) if pump_i is not None else None
    rec["volume_leads_price_min"] = (
        round((ts[pump_i] - ts[crowd_i]) / 60, 1)
        if pump_i is not None and crowd_i is not None and ts[pump_i] > ts[crowd_i]
        else None
    )

    # forward returns at 3h/6h/12h from launch
    for h in (3, 6, 12):
        target = t0 + h * 3600
        idx = next((i for i in range(entry_i, len(ts)) if ts[i] >= target), None)
        rec[f"r_{h}h_pct"] = round((close[idx] / entry - 1) * 100, 1) if idx is not None else None
    return rec


def main():
    limit = 20
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    profiles = fetch("https://api.dexscreener.com/token-profiles/latest/v1")
    if not isinstance(profiles, list):
        print("FATAL: token-profiles returned non-list", file=sys.stderr)
        sys.exit(1)
    sol = [p for p in profiles if p.get("chainId") == "solana"][:limit]
    print(f"profiles fetched={len(profiles)} solana={len(sol)} (limit {limit})")

    results = []
    gmgn_probes = []
    for p in sol:
        mint = p.get("tokenAddress")
        sym = p.get("symbol") or mint[:6]
        print(f"-> {sym} {mint[:12]}...", flush=True)
        rec = analyze(mint, sym)
        results.append(rec)
        # incremental save so a timeout never loses everything
        with open(OUT, "w") as f:
            json.dump({"collected_at": time.time(), "n": len(results),
                       "tokens": results, "gmgn_probes": gmgn_probes}, f, indent=1)
        if len(results) <= 3 and rec.get("pool"):
            info = gmgn_info(rec["mint"])
            if info:
                st = info.get("stat") or {}
                dv = info.get("dev") or {}
                pr = info.get("price") or {}
                gmgn_probes.append({
                    "symbol": rec["symbol"],
                    "top_bundler_trader_percentage": st.get("top_bundler_trader_percentage"),
                    "top_10_holder_rate": st.get("top_10_holder_rate"),
                    "creator_token_status": (dv.get("creator_token_status") or {}).get("is_creator_sold") if isinstance(dv.get("creator_token_status"), dict) else dv.get("creator_token_status"),
                    "volume_5m": pr.get("volume_5m"),
                    "holders": st.get("holder_count") or st.get("holder"),
                })
                print(f"   gmgn ok: bundler={gmgn_probes[-1]['top_bundler_trader_percentage']} "
                      f"top10={gmgn_probes[-1]['top_10_holder_rate']} dev_sold={gmgn_probes[-1]['creator_token_status']}")
            else:
                print(f"   gmgn: no data for {rec['symbol']}")

    out = {"collected_at": time.time(), "n": len(results),
           "signals_measured": ["new_pool_graduation", "volume_pressure",
                                "holder_concentration", "bundler_share", "dev_holding"],
           "tokens": results, "gmgn_probes": gmgn_probes}
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)
    print(f"\nwrote {OUT}")

    # summary table
    lead = [r["crowd_lead_min"] for r in results if r.get("crowd_lead_min") is not None]
    pump = [r["pump2x_min"] for r in results if r.get("pump2x_min") is not None]
    vlead = [r["volume_leads_price_min"] for r in results if r.get("volume_leads_price_min") is not None]
    print(f"\n== summary (n={len(results)}) ==")
    print(f"crowd-volume arrival after launch (min): n={len(lead)} median={median(lead) if lead else None}")
    print(f"first 2x pump after launch (min):        n={len(pump)} median={median(pump) if pump else None}")
    print(f"volume-leads-price (min):                n={len(vlead)} median={median(vlead) if vlead else None}")
    for h in (3, 6, 12):
        vals = [r[f"r_{h}h_pct"] for r in results if r.get(f"r_{h}h_pct") is not None]
        if vals:
            up = sum(1 for v in vals if v >= 10) / len(vals)
            print(f"P(return>=+10% at {h}h) = {up:.0%} (n={len(vals)}, median={median(vals):.1f}%)")
    print(f"\nerrors: {sum(len(r['errors']) for r in results)} across {sum(1 for r in results if r['errors'])} tokens")
    for r in results:
        if r["errors"]:
            print("  ", r["symbol"], r["errors"])


if __name__ == "__main__":
    main()
