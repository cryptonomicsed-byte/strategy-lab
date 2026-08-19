#!/usr/bin/env python3
"""radar/build_watchlist.py — build the first-mover watchlist (lane 2, radar).

Reads wallet_intel.db (wallet_tokens early-buy records, token_stats first
buyers, wallets tags) + Vantage vantage.db (alpha_wallets scores,
wallet_reputation copy-trade) and emits radar/watchlist.json:
ranked first-mover wallets with conviction on the 0-1 scale the hub
consumes (hub/score_schema.md §2.2: RADAR_MIN_CONVICTION = 0.50).

PAPER ONLY — read-only on both databases (mode=ro), writes only the JSON.

Conviction methodology (v1, see watchlist.json "meta" for the live copy):
  evidence bar : early_buys >= 3  OR  (early_buys >= 2 AND first_buyer_hits >= 1)
  freq         : logistic(log1p(early_buys + 2*first_buyer_hits), scale=1.8)
  speed        : 1.00 if first_buyer_hits >= 2
                 0.92 if first_buyer_hits == 1
                 1/(1+median_lag_min/15) if lag measurable
                 0.70 otherwise
  recency      : exp(-days_since_last_buy / 14)
  reputation   : 0.5*tag_pts + 0.3*vantage_score_norm + 0.2*copy_trade_norm
  raw          : 0.50*freq + 0.25*speed + 0.10*recency + 0.15*reputation
  conviction   : min-max calibrate raw over the qualified set onto [0.50, 0.95]
  tiers        : S >= 0.80, A >= 0.65, B >= 0.50
"""
import argparse
import datetime
import json
import math
import sqlite3
import time
from collections import Counter, defaultdict

WALLET_INTEL = "/opt/ares/wallet_intel/wallet_intel.db"
VANTAGE = "/opt/ares/Vantage/data/vantage.db"
DEFAULT_OUT = "radar/watchlist.json"

# Tokens that are trading pairs / stables, never first-mover targets.
STABLE_MINTS = {
    "So11111111111111111111111111111111111111112",  # WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}
STABLE_SYMS = {"WSOL", "USDC", "USDT", "SOL", "USDS"}

# Wallet tags -> reputation points (0..1). Max wins (a wallet is as good as
# its strongest label); deployer/wash_trader carry no credit.
TAG_PTS = {
    "smart_degen": 1.0, "smart": 1.0, "backtrack_winner": 1.0,
    "early_buyer": 1.0,
    "axiom": 0.85, "kol": 0.8, "kol:telegram": 0.8, "alpha:C": 0.8,
    "style:SNIPER": 0.75, "app_smart_money": 0.7, "style:ACCUMULATOR": 0.65,
    "launchpad_smart": 0.65, "gmgn": 0.6, "padre": 0.6, "photon": 0.6,
    "trojan": 0.6, "bullx": 0.6, "style:SWING": 0.55, "whale_trader": 0.55,
    "top_holder": 0.5, "top_followed": 0.5, "top_renamed": 0.5,
}


def connect(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)


def ts_to_epoch(v):
    """wallet_tokens.first_buy_ts is ISO-8601 or epoch float (legacy rows)."""
    if v is None:
        return None
    s = str(v).strip()
    try:
        if "T" in s:
            return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        f = float(s)
        return f if f > 1e9 else None
    except Exception:
        return None


def logistic(x, scale=1.0):
    return 1.0 / (1.0 + math.exp(-x / scale))


def clamp01(x):
    return max(0.0, min(1.0, x))


def load_wallet_intel():
    con = connect(WALLET_INTEL)
    try:
        sym = {m: s for m, s in con.execute("SELECT mint, symbol FROM token_stats")}

        # wallet -> {mint: first_buy_ts_epoch} over non-stable tokens
        wt = defaultdict(dict)
        for w, m, t in con.execute(
                "SELECT wallet, mint, first_buy_ts FROM wallet_tokens"):
            if m in STABLE_MINTS or sym.get(m) in STABLE_SYMS:
                continue
            e = ts_to_epoch(t)
            if e:
                wt[w][m] = e

        # token -> first_buy_ts + the very first buyer(s)
        tok_first, tok_fb = {}, defaultdict(list)
        for mint, raw, fb_raw in con.execute(
                "SELECT mint, first_buy_ts, first_buyers FROM token_stats"):
            if mint in STABLE_MINTS or sym.get(mint) in STABLE_SYMS:
                continue
            e = ts_to_epoch(raw)
            if e:
                tok_first[mint] = e
            try:
                for w in json.loads(fb_raw) if fb_raw else []:
                    tok_fb[mint].append(w)
            except Exception:
                pass

        tags = {}
        for addr, raw in con.execute(
                "SELECT address, tags FROM wallets WHERE tags IS NOT NULL"):
            try:
                j = json.loads(raw)
                tags[addr] = j if isinstance(j, list) else [j]
            except Exception:
                tags[addr] = [raw]

        fb_hits = Counter()
        for ws in tok_fb.values():
            for w in set(ws):
                fb_hits[w] += 1
    finally:
        con.close()
    return wt, tok_first, tok_fb, tags, fb_hits, sym


def load_vantage(addresses):
    """alpha_wallets score/style + wallet_reputation copy_trade_score."""
    con = connect(VANTAGE)
    try:
        vantage = {}
        for r in con.execute(
                "SELECT address, score, win_rate, style, tier FROM alpha_wallets"):
            vantage[r[0]] = r
        rep = {}
        if addresses:
            q = ("SELECT wallet_address, copy_trade_score, first_buyer_count "
                 "FROM wallet_reputation WHERE wallet_address IN ({})".format(
                     ",".join("?" * len(addresses))))
            for r in con.execute(q, tuple(addresses)):
                rep[r[0]] = r
    finally:
        con.close()
    return vantage, rep


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    wt, tok_first, tok_fb, tags, fb_hits, sym = load_wallet_intel()
    all_wallets = set(wt)
    vantage, rep = load_vantage(all_wallets)

    now = time.time()
    rows = []
    for w, mints in wt.items():
        n = len(mints)
        hits = fb_hits.get(w, 0)
        if not (n >= 3 or (n >= 2 and hits >= 1)):
            continue  # evidence bar: proven first-mover
        lags = []
        for m in mints:
            tf, tw = tok_first.get(m), wt[w][m]
            if tf and tw:
                lags.append((tw - tf) / 60.0)
        med_lag = sorted(lags)[len(lags) // 2] if lags else None
        last = max(wt[w].values())
        days = (now - last) / 86400.0

        freq = logistic(math.log1p(n + 2 * hits), scale=1.8)
        if hits >= 2:
            speed = 1.0
        elif hits == 1:
            speed = 0.92
        elif med_lag is not None:
            speed = 1.0 / (1.0 + med_lag / 15.0)
        else:
            speed = 0.7
        recency = math.exp(-days / 14.0)

        tset = set(tags.get(w, []))
        tag_pts = max((TAG_PTS.get(t, 0.3) for t in tset), default=0.3)
        v = vantage.get(w)
        vnorm = clamp01(v[1] / 80.0) if v and v[1] is not None else 0.0
        rp = rep.get(w)
        cnorm = clamp01(math.log1p(rp[1]) / 10.0) if rp and rp[1] else 0.0
        reputation = 0.5 * tag_pts + 0.3 * vnorm + 0.2 * cnorm

        raw = 0.50 * freq + 0.25 * speed + 0.10 * recency + 0.15 * reputation
        rows.append({
            "w": w, "n": n, "hits": hits, "med_lag": med_lag, "days": days,
            "tags": sorted(tset), "raw": raw,
            "vscore": v[1] if v else None, "vstyle": v[3] if v else None,
            "vwin": v[2] if v else None, "copy": rp[1] if rp else None,
        })

    rows.sort(key=lambda r: -r["raw"])
    raws = [r["raw"] for r in rows]
    lo, hi = min(raws), max(raws)
    span = hi - lo if hi > lo else 1.0

    def calibrate(raw):
        return 0.50 + 0.45 * (raw - lo) / span

    def tier(conv):
        return "S" if conv >= 0.80 else ("A" if conv >= 0.65 else "B")

    entries = []
    for r in rows:
        conv = round(calibrate(r["raw"]), 4)
        entries.append({
            "address": r["w"],
            "conviction": conv,
            "tier": tier(conv),
            "evidence": {
                "early_buys": r["n"],
                "first_buyer_hits": r["hits"],
                "median_lag_min": round(r["med_lag"], 2) if r["med_lag"] is not None else None,
                "days_since_last_buy": round(r["days"], 2),
                "tags": r["tags"],
                "vantage_score": r["vscore"],
                "vantage_style": r["vstyle"],
                "copy_trade_score": r["copy"],
            },
        })

    out = {
        "schema_version": 1,
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "meta": {
            "lane": "radar",
            "purpose": "ranked first-mover wallets feeding hub radar component "
                       "(score_schema.md §2.2; min_conviction 0.50)",
            "conviction_scale": "0..1 (hub-normalized)",
            "evidence_bar": "early_buys >= 3 OR (early_buys >= 2 AND first_buyer_hits >= 1)",
            "scoring": {
                "freq": "logistic(log1p(early_buys + 2*first_buyer_hits), 1.8)",
                "speed": "1.0 hits>=2 / 0.92 hits==1 / 1/(1+median_lag/15) / 0.7",
                "recency": "exp(-days_since_last_buy/14)",
                "reputation": "0.5*tag_pts + 0.3*vantage_norm + 0.2*copy_norm",
                "raw": "0.50*freq + 0.25*speed + 0.10*recency + 0.15*reputation",
                "calibration": "min-max of raw onto [0.50, 0.95] over qualified set",
            },
            "tiers": {"S": ">= 0.80", "A": ">= 0.65", "B": ">= 0.50"},
            "counts": {"qualified": len(entries),
                       "by_tier": dict(Counter(e["tier"] for e in entries))},
            "sources": [
                "wallet_intel.db: wallet_tokens.first_buy_ts (early buys)",
                "wallet_intel.db: token_stats.first_buyers (THE first buyer)",
                "wallet_intel.db: wallets.tags (smart money / KOL labels)",
                "vantage.db: alpha_wallets.score/style (Vantage reputation)",
                "vantage.db: wallet_reputation.copy_trade_score",
            ],
        },
        "watchlist": entries,
    }

    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
    if not args.quiet:
        print(f"wrote {args.out}: {len(entries)} wallets "
              f"(S={out['meta']['counts']['by_tier'].get('S', 0)}, "
              f"A={out['meta']['counts']['by_tier'].get('A', 0)}, "
              f"B={out['meta']['counts']['by_tier'].get('B', 0)})")


if __name__ == "__main__":
    main()
