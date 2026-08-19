#!/usr/bin/env python3
"""radar/cluster_detect.py — live probe: emit buy-clusters of first-mover wallets.

Lane 2 (radar) deliverable per STRATEGY.md. Reads radar/watchlist.json
(ranked first-movers, conviction 0..1) and wallet_intel.db (read-only) and
emits CLUSTER EVENTS: tokens where watchlist wallets were among the early
buyers within the window. Cluster semantics follow hub/score_schema.md §2.2:
a wallet "belongs" to a token when wallet_tokens.first_buy_ts for that mint
is within the cluster window of `now` (live mode) — the crowd-hasn't-arrived
proof the ensemble consumes.

Event schema: radar/cluster_schema.md (canonical).

Modes
-----
live (default) : clusters with first buys in [now - window_min, now + slack].
                 This is the real-time probe the hub's C_radar consumes.
--since HOURS  : same cluster logic restricted to the last HOURS.
--all          : full historical scan — every token with >= min_wallets
                 watchlist wallets co-buying, spread = first..last buy gap.
                 Use this to prove detection works on the stored window.

Output: JSON wrapper to stdout + radar/clusters/latest.json (and a
timestamped copy). --no-write suppresses file output. PAPER ONLY.
"""
import argparse
import datetime
import json
import math
import os
import sqlite3
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
WALLET_INTEL = os.environ.get(
    "WALLET_DB", "/opt/ares/wallet_intel/wallet_intel.db")
WATCHLIST = os.environ.get(
    "RADAR_WATCHLIST", os.path.join(HERE, "watchlist.json"))
CLUSTER_DIR = os.path.join(HERE, "clusters")

SCHEMA_VERSION = 1

# Tokens that are trading pairs / stables, never first-mover targets.
STABLE_MINTS = {
    "So11111111111111111111111111111111111111112",  # WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}
STABLE_SYMS = {"WSOL", "USDC", "USDT", "SOL", "USDS"}

BANDS = [(3, "CRITICAL"), (2, "STRONG"), (1, "WEAK")]


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


def load_watchlist(path):
    """address(lower) -> (conviction, entry). Same contract as hub load_watchlist."""
    with open(path) as f:
        data = json.load(f)
    wl = {}
    for e in data.get("watchlist", []) if isinstance(data, dict) else data:
        if isinstance(e, dict) and e.get("address"):
            wl[e["address"].lower()] = (float(e.get("conviction") or 0.0), e)
    return wl


def band(n):
    for k, b in BANDS:
        if n >= k:
            return b
    return "WEAK"


def build_events(mode, now, min_conv, min_wallets, window_min, slack_min=10):
    wl = load_watchlist(WATCHLIST)
    if not wl:
        sys.exit("watchlist empty/unreadable: " + WATCHLIST)
    conv_of = {addr: c for addr, (c, _) in wl.items()}

    con = sqlite3.connect(f"file:{WALLET_INTEL}?mode=ro", uri=True, timeout=10)
    try:
        sym = {}
        for m, s in con.execute("SELECT mint, symbol FROM token_stats"):
            sym[m] = s
        vol = {}
        for m, v in con.execute("SELECT mint, buy_volume FROM token_stats"):
            vol[m] = v

        # mint -> {wallet_lower: first_buy_ts_epoch} for watchlist wallets
        per_mint = defaultdict(dict)
        for w, m, t in con.execute(
                "SELECT wallet, mint, first_buy_ts FROM wallet_tokens "
                "WHERE first_buy_ts IS NOT NULL"):
            if m in STABLE_MINTS or sym.get(m) in STABLE_SYMS:
                continue
            wl_lower = w.lower()
            if wl_lower not in conv_of:
                continue
            e = ts_to_epoch(t)
            if e is None:
                continue
            per_mint[m][wl_lower] = e
    finally:
        con.close()

    events = []
    for mint, buys in per_mint.items():
        members = []
        for wl_lower, t in buys.items():
            conv = conv_of[wl_lower]
            if conv < min_conv:
                continue
            members.append({"wallet": wl_lower, "conviction": round(conv, 4),
                            "first_buy_ts": round(t, 3),
                            "age_min": round(max(0.0, now - t) / 60.0, 1)})
        if not members:
            continue
        members.sort(key=lambda m: m["first_buy_ts"])
        spread = members[-1]["first_buy_ts"] - members[0]["first_buy_ts"]
        if mode == "live":
            # cluster window: first buys within [now - window_min, now + slack]
            members = [m for m in members if now - window_min * 60 <= m["first_buy_ts"] <= now + slack_min * 60]
            if not members:
                continue
            spread = members[-1]["first_buy_ts"] - members[0]["first_buy_ts"]
            oldest = max(m["first_buy_ts"] for m in members)
            if oldest > now + slack_min * 60:
                continue
        elif mode == "since":
            members = [m for m in members if m["first_buy_ts"] >= now - window_min * 60]
            if not members:
                continue
            spread = members[-1]["first_buy_ts"] - members[0]["first_buy_ts"]
        # all mode: no window filter, full stored history
        if len(members) < min_wallets:
            continue
        members.sort(key=lambda m: -m["conviction"])
        n = len(members)
        mean_conv = sum(m["conviction"] for m in members) / n
        events.append({
            "token": mint,
            "symbol": sym.get(mint),
            "wallet_count": n,
            "conviction": round(mean_conv, 4),
            "window_min": window_min if mode != "all" else None,
            "spread_min": round(spread / 60.0, 2),
            "volume_usd": round(float(vol.get(mint) or 0.0), 2),
            "band": band(n),
            "members": members,
        })

    events.sort(key=lambda e: (-e["wallet_count"], -e["conviction"]))
    return events


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true",
                    help="full historical scan (all stored clusters)")
    ap.add_argument("--since", type=float, default=None, metavar="HOURS",
                    help="clusters with buys within the last HOURS")
    ap.add_argument("--window-min", type=float, default=180.0,
                    help="cluster window minutes (default 180, hub default)")
    ap.add_argument("--min-conv", type=float, default=0.50,
                    help="minimum watchlist conviction (default 0.50, hub value)")
    ap.add_argument("--min-wallets", type=int, default=2,
                    help="minimum watchlist wallets for --all/--since (default 2)")
    ap.add_argument("--no-write", action="store_true",
                    help="stdout only, no radar/clusters/ files")
    args = ap.parse_args()

    now = time.time()
    if args.all:
        mode, window_min = "all", None
    elif args.since is not None:
        mode, window_min = "since", args.since * 60
    else:
        mode, window_min = "live", args.window_min

    events = build_events(mode, now, args.min_conv,
                          args.min_wallets if mode != "live" else 1,
                          window_min if window_min else args.window_min)
    out = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "ts": round(now, 3),
        "mode": mode,
        "min_conviction": args.min_conv,
        "count": len(events),
        "events": events,
    }
    print(json.dumps(out, indent=2))

    if not args.no_write:
        os.makedirs(CLUSTER_DIR, exist_ok=True)
        stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        latest = os.path.join(CLUSTER_DIR, "latest.json")
        with open(latest, "w") as f:
            json.dump(out, f, indent=2)
        if mode == "live":
            with open(os.path.join(CLUSTER_DIR, f"{stamp}.json"), "w") as f:
                json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()
