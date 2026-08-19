#!/usr/bin/env python3
"""CONVERGENCE HUB — the ensemble's single source of truth for token X.

Lane 3 (panel-omokoda, branch: hub) of the strategy-lab early-entry ensemble.

Given a token address, fetches engine params (lane 1), radar cluster
evidence (lane 2, computed live from wallet_intel.db) and the existing
ares-signal-fusion pick (S_signal/S_wallet/S_market) and returns the
EARLY-ENTRY bundle: {token, score, components, fired_signals[], verdict}.

Canonical definition of the score: hub/score_schema.md (STATUS: NEAR-LOCK).
PAPER ONLY — reads data, never places an order.

Usage:
  python3 hub/score.py <token_addr> [--no-record]   # one token, print bundle
  python3 hub/score.py top [N]                       # current fusion picks
  python3 hub/score.py --due                          # verdicts needing marks
  python3 hub/score.py --mark 6h|24h [--token ADDR]   # follow-up hook
  python3 hub/score.py serve [--port 8010]            # HTTP endpoint

HTTP:
  GET /api/convergence?token=ADDR&record=1
  GET /health
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import sqlite3
import sys
import time
import urllib.request

# --------------------------------------------------------------------- paths
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

FUSION_DB = os.environ.get(
    "FUSION_DB", "/opt/ares/ares-signal-fusion/ares_picks.db")
PICKS_API = os.environ.get("PICKS_API", "http://127.0.0.1:8003/api/picks")
WALLET_DB = os.environ.get("WALLET_DB", "/opt/ares/wallet_intel/wallet_intel.db")
AXIOM_WATCHLIST = os.environ.get(
    "AXIOM_WATCHLIST", "/opt/ares/axiom/data/watchlist.json")

ENGINE_PARAMS = os.environ.get(
    "ENGINE_PARAMS", os.path.join(REPO_ROOT, "engine", "params.json"))
ENGINE_FIRED = os.environ.get(
    "ENGINE_FIRED", os.path.join(REPO_ROOT, "engine", "fired_signals.json"))
RADAR_WATCHLIST = os.environ.get(
    "RADAR_WATCHLIST", os.path.join(REPO_ROOT, "radar", "watchlist.json"))

SCHEMA_VERSION = 1
STATUS = "NEAR-LOCK"  # -> LOCKED when engine+radar land and all 3 lanes agree

# ------------------------------------------------------------------ weights
# DRAFT weights — calibration (§7 of score_schema.md) may move them, only
# via proposals/ + orchestrator. Mirror of hub/score_schema.md §1.
WEIGHTS = {"engine": 0.35, "radar": 0.35, "fusion": 0.30}

# radar cluster knobs (score_schema.md §2.2)
RADAR_MIN_CONVICTION = 0.50
RADAR_WINDOW_MIN = 180        # cluster window: first buys within N minutes
RADAR_K = (0.6, 1.2, 0.5)     # k1·n + k2·mean_conv + k3·log1p(vol)/8
RADAR_SCALE = 2.5
RADAR_FRESH_HL_MIN = 60.0     # freshness half-life of the cluster age

# fusion gate context (score_schema.md §3)
FUSION_LIQ_FLOOR = 5000.0

VERDICT_DIR = os.path.join(HERE, "verdicts")
MARK_OFFSETS = {"6h": 6 * 3600, "24h": 24 * 3600}


def decay(age_seconds: float, half_life_hours: float) -> float:
    """True half-life decay: weight == 0.5 when age == half_life."""
    if half_life_hours <= 0 or age_seconds <= 0:
        return 1.0
    return math.exp(-math.log(2) * age_seconds / (half_life_hours * 3600))


def logistic(x: float, scale: float = 1.0) -> float:
    return 1.0 / (1.0 + math.exp(-x / scale))


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _ts_to_epoch(iso: str) -> float | None:
    """'2026-08-19T07:38:41.869420+00:00' -> epoch. None on garbage."""
    if not iso:
        return None
    try:
        return _dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


# ------------------------------------------------------------------- fusion
def fetch_picks_http() -> list[dict]:
    """Current picks via the :8003 sidecar (HTTP path — the brief's default)."""
    with urllib.request.urlopen(PICKS_API, timeout=10) as r:
        data = json.loads(r.read().decode())
    return data.get("picks", [])


def fusion_pick_for_token(addr: str) -> dict | None:
    """Latest pick row for the token from ares_picks.db (per-token lookup)."""
    if not os.path.exists(FUSION_DB):
        return None
    conn = sqlite3.connect(f"file:{FUSION_DB}?mode=ro", uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM picks WHERE token_addr = ? ORDER BY ts DESC LIMIT 1",
            (addr,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["components"] = json.loads(d["components"])
        d["gates"] = json.loads(d["gates"])
        return d
    finally:
        conn.close()


def fusion_component(pick: dict | None, now: float) -> dict:
    """C_fusion = mean(S_signal, S_wallet) over PRESENT ones; S_market = gate
    context only. score_schema.md §2.3."""
    if not pick:
        return {"present": False, "value": None, "weight": WEIGHTS["fusion"],
                "pick_id": None, "rank": None, "entry_price": None,
                "drivers": {}, "note": "no fusion pick for token"}
    comps = pick.get("components", {})
    s_sig = comps.get("S_signal", {})
    s_wal = comps.get("S_wallet", {})
    s_mkt = comps.get("S_market", {})
    vals = [c["value"] for c in (s_sig, s_wal) if c.get("present")]
    drivers = {
        "S_signal": {"value": s_sig.get("value"), "present": bool(s_sig.get("present"))},
        "S_wallet": {"value": s_wal.get("value"), "present": bool(s_wal.get("present"))},
        "S_market": {"value": s_mkt.get("value"), "present": bool(s_mkt.get("present")),
                     "liquidity_usd": (s_mkt.get("drivers") or [{}])[0].get("liquidity_usd") if s_mkt.get("drivers") else None},
    }
    if not vals:
        return {"present": False, "value": None, "weight": WEIGHTS["fusion"],
                "pick_id": pick.get("id"), "rank": pick.get("rank"),
                "entry_price": pick.get("entry_price"), "drivers": drivers,
                "note": "fusion pick exists but S_signal/S_wallet absent"}
    return {"present": True, "value": sum(vals) / len(vals), "weight": WEIGHTS["fusion"],
            "pick_id": pick.get("id"), "rank": pick.get("rank"),
            "entry_price": pick.get("entry_price"), "drivers": drivers}


# ------------------------------------------------------------------- engine
def load_engine_files() -> tuple[dict | None, dict]:
    """params.json + fired_signals.json as loaded from lane 1. fired may be
    a single-token object or {token: [...]} map — both accepted."""
    params = None
    if os.path.exists(ENGINE_PARAMS):
        try:
            with open(ENGINE_PARAMS) as f:
                params = json.load(f)
        except Exception:
            params = None
    fired: dict[str, list] = {}
    if os.path.exists(ENGINE_FIRED):
        try:
            with open(ENGINE_FIRED) as f:
                raw = json.load(f)
            f_map = raw.get("fired", raw) if isinstance(raw, dict) else raw
            if isinstance(f_map, dict):
                if "token" in f_map and isinstance(f_map.get("fired"), list):
                    fired[f_map["token"]] = f_map["fired"]
                else:
                    for tok, lst in f_map.items():
                        if isinstance(lst, list):
                            fired[tok] = lst
            elif isinstance(f_map, list):
                for item in f_map:
                    if isinstance(item, dict) and item.get("token"):
                        fired.setdefault(item["token"], []).append(item)
        except Exception:
            fired = {}
    return params, fired


def engine_component(addr: str, now: float) -> dict:
    """C_engine = logistic(Σ w_i·strength_i·direction_i·decay_i). Absent until
    lane 1 lands engine/params.json — the score must say what it doesn't know."""
    params, fired = load_engine_files()
    if not params or not params.get("signals"):
        return {"present": False, "value": None, "weight": WEIGHTS["engine"],
                "drivers": [], "note": "engine/params.json not landed (lane 1 pending)"}
    hl_cfg = float(params.get("half_life_hours") or 2.0)
    fired_here = fired.get(addr, [])
    fired_by_id = {f.get("id"): f for f in fired_here if isinstance(f, dict)}
    net, drivers = 0.0, []
    for sig in params.get("signals", []):
        sid = sig.get("id")
        f = fired_by_id.get(sid)
        if not f:
            continue
        strength = float(f.get("strength") or sig.get("weight", 1.0))
        direction = float(sig.get("direction", 1.0))
        age_s = now - float(f.get("ts") or f.get("fired_ts") or now)
        hl = float(sig.get("half_life_hours") or hl_cfg)
        contrib = float(sig.get("weight", 1.0)) * strength * direction * decay(age_s, hl)
        net += contrib
        drivers.append({"id": sid, "strength": strength, "direction": direction,
                        "age_min": round(age_s / 60, 1), "contrib": round(contrib, 4)})
    if not drivers:
        return {"present": False, "value": None, "weight": WEIGHTS["engine"],
                "drivers": [], "note": "params landed, no fired signals for token"}
    return {"present": True, "value": logistic(net, scale=2.5),
            "weight": WEIGHTS["engine"], "drivers": drivers}


# -------------------------------------------------------------------- radar
def load_watchlist() -> tuple[dict[str, float], str]:
    """address(lower) -> conviction. Repo radar/watchlist.json wins; until
    lane 2 lands it, fall back to the axiom working copy (flagged)."""
    path = RADAR_WATCHLIST
    source = "repo:radar/watchlist.json"
    if not os.path.exists(path):
        path, source = AXIOM_WATCHLIST, "fallback:/opt/ares/axiom/data/watchlist.json"
    wl: dict[str, float] = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            for entry in data if isinstance(data, list) else []:
                if isinstance(entry, dict) and entry.get("address"):
                    wl[entry["address"].lower()] = float(entry.get("conviction") or 0.0)
        except Exception:
            pass
    return wl, source


def radar_component(addr: str, now: float) -> dict:
    """Cluster = watchlist wallets whose first buy of the token is within the
    window. C_radar = logistic(k1·n + k2·mean_conv + k3·log1p(vol)/8, 2.5)
    × freshness_decay(age of oldest buy, 60min half-life)."""
    watchlist, source = load_watchlist()
    if not watchlist:
        return {"present": False, "value": None, "weight": WEIGHTS["radar"],
                "drivers": [], "note": "watchlist not available (lane 2 pending)",
                "cluster": {"wallet_count": 0}}
    if not os.path.exists(WALLET_DB):
        return {"present": False, "value": None, "weight": WEIGHTS["radar"],
                "drivers": [], "note": "wallet_intel.db unavailable"}
    conn = sqlite3.connect(f"file:{WALLET_DB}?mode=ro", uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        cutoff = now - RADAR_WINDOW_MIN * 60
        rows = conn.execute(
            "SELECT wallet, first_buy_ts FROM wallet_tokens WHERE mint = ? AND first_buy_ts IS NOT NULL",
            (addr,)).fetchall()
        vol_row = conn.execute(
            "SELECT buy_volume FROM token_stats WHERE mint = ?", (addr,)).fetchone()
    finally:
        conn.close()
    vol = float(vol_row["buy_volume"]) if vol_row and vol_row["buy_volume"] else 0.0

    members = []
    for r in rows:
        conv = watchlist.get((r["wallet"] or "").lower())
        if conv is None or conv < RADAR_MIN_CONVICTION:
            continue
        t = _ts_to_epoch(r["first_buy_ts"])
        if t is None or t < cutoff or t > now + 600:  # +10min clock slack
            continue
        members.append({"wallet": r["wallet"], "conviction": round(conv, 4),
                        "first_buy_ts": round(t, 3), "age_min": round(max(0.0, now - t) / 60, 1)})
    if not members:
        return {"present": False, "value": None, "weight": WEIGHTS["radar"],
                "drivers": [], "note": "no watchlist cluster in window",
                "cluster": {"wallet_count": 0, "window_min": RADAR_WINDOW_MIN}}
    members.sort(key=lambda m: -m["conviction"])
    n = len(members)
    mean_conv = sum(m["conviction"] for m in members) / n
    k1, k2, k3 = RADAR_K
    raw = k1 * n + k2 * mean_conv + k3 * math.log1p(max(vol, 0.0)) / 8
    oldest_age_min = max(m["age_min"] for m in members)
    fresh = math.exp(-math.log(2) * oldest_age_min / RADAR_FRESH_HL_MIN)
    value = _clamp01(logistic(raw, scale=RADAR_SCALE) * fresh)
    return {"present": True, "value": value, "weight": WEIGHTS["radar"],
            "cluster": {"wallet_count": n, "conviction": round(mean_conv, 4),
                        "window_min": RADAR_WINDOW_MIN, "volume_usd": round(vol, 2)},
            "drivers": members, "watchlist_source": source}


# ------------------------------------------------------------------- bundle
def compute_bundle(addr: str, now: float | None = None) -> dict:
    """The full early-entry bundle per score_schema.md §6."""
    now = now if now is not None else time.time()
    pick = fusion_pick_for_token(addr)
    comps = {
        "engine": engine_component(addr, now),
        "radar": radar_component(addr, now),
        "fusion": fusion_component(pick, now),
    }
    present = {k: c for k, c in comps.items() if c.get("present")}

    # ---- gates / vetoes
    vetoes: list[dict] = []
    if pick:
        for v in pick.get("gates", {}).get("vetoes", []) or []:
            vetoes.append({"gate": f"fusion:{v.get('gate')}", "reason": v.get("reason")})
        s_mkt = comps["fusion"]["drivers"].get("S_market", {})
        liq = s_mkt.get("liquidity_usd") if s_mkt.get("present") else None
        if liq is not None and liq < FUSION_LIQ_FLOOR:
            vetoes.append({"gate": "fusion_liq_floor",
                           "reason": f"liquidity ${liq:,.0f} < ${FUSION_LIQ_FLOOR:,.0f}"})
    if not present:
        vetoes.append({"gate": "insufficient_data",
                       "reason": "zero ensemble components present"})
    gates = {"passed": not vetoes, "vetoes": vetoes}

    # ---- score (present-aware weighted mean)
    if present:
        num = sum(c["value"] * c["weight"] for c in present.values())
        den = sum(c["weight"] for c in present.values())
        score = round(100.0 * num / den, 2)
    else:
        score = None

    # ---- fired signals (human-readable proof)
    fired: list[str] = []
    if comps["fusion"]["present"]:
        d = comps["fusion"]["drivers"]
        if d["S_signal"]["present"]:
            fired.append(f"fusion:S_signal={d['S_signal']['value']:.2f}")
        if d["S_wallet"]["present"]:
            n_drv = len(pick.get("components", {}).get("S_wallet", {}).get("drivers", []))
            fired.append(f"fusion:S_wallet={d['S_wallet']['value']:.2f} ({n_drv} wallet drivers)")
        if comps["fusion"].get("rank") and comps["fusion"]["rank"] <= 3:
            fired.append(f"fusion:rank={comps['fusion']['rank']} (top pick)")
    if comps["radar"]["present"]:
        cl = comps["radar"]["cluster"]
        fired.append(f"radar:cluster={cl['wallet_count']} watchlist wallets within {cl['window_min']}min")
    if comps["engine"]["present"]:
        fired.append(f"engine:{len(comps['engine']['drivers'])} signal(s) fired")

    # ---- verdict bands (score_schema.md §4)
    dq = {
        "engine_available": bool(comps["engine"]["present"]),
        "radar_available": bool(comps["radar"]["present"]),
        "fusion_available": bool(comps["fusion"]["present"]),
        "min_data": "ok" if len(present) >= 2 else ("partial" if present else "none"),
    }
    reason = ""
    if not gates["passed"]:
        verdict = "NO"
        reason = "; ".join(v["reason"] for v in vetoes)
    elif score is None:
        verdict = "NO"
        reason = "insufficient data"
    else:
        radar_n = comps["radar"]["cluster"]["wallet_count"] if comps["radar"]["present"] else 0
        strong = (comps["engine"]["present"]
                  or radar_n >= 2
                  or (comps["fusion"].get("rank") and comps["fusion"]["rank"] <= 3))
        if score >= 70 and strong and len(present) >= 2:
            verdict = "EARLY"
        elif score >= 40:
            verdict = "WATCH"
        else:
            verdict = "NO"
        bits = []
        if comps["engine"]["present"]:
            bits.append("engine fired")
        if comps["radar"]["present"]:
            bits.append(f"radar cluster {radar_n} wallet(s)")
        if comps["fusion"]["present"]:
            bits.append(f"fusion rank {comps['fusion']['rank']}")
        reason = " + ".join(bits) if bits else "no component evidence"

    symbol = (pick.get("symbol") if pick else None) or _symbol_from_stats(addr)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": STATUS,
        "token": addr,
        "symbol": symbol,
        "ts": round(now, 3),
        "score": score,
        "verdict": verdict,
        "reason": reason,
        "components": comps,
        "fired_signals": fired,
        "gates": gates,
        "data_quality": dq,
        "followups": {"6h": None, "24h": None},
        "pick_ref": {"fusion_pick_id": comps["fusion"].get("pick_id"),
                     "rank": comps["fusion"].get("rank"),
                     "entry_price": comps["fusion"].get("entry_price")},
    }


def _symbol_from_stats(addr: str) -> str | None:
    if not os.path.exists(WALLET_DB):
        return None
    try:
        conn = sqlite3.connect(f"file:{WALLET_DB}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute("SELECT symbol FROM token_stats WHERE mint = ?", (addr,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


# ---------------------------------------------------------------- verdicts
def verdicts_path(ts: float | None = None) -> str:
    day = _dt.datetime.fromtimestamp(ts if ts is not None else time.time(),
                                     tz=_dt.timezone.utc).strftime("%Y-%m-%d")
    return os.path.join(VERDICT_DIR, f"{day}.json")


def append_verdict(bundle: dict) -> None:
    path = verdicts_path(bundle["ts"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entries = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                entries = json.load(f)
        except Exception:
            entries = []
    key = (bundle["token"], bundle["ts"])
    if any((e["token"], e["ts"]) == key for e in entries):
        return
    entries.append(bundle)
    entries.sort(key=lambda e: (-e["ts"]))
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(entries, f, indent=2)
    os.replace(tmp, path)


def load_entries(ts: float | None = None) -> list[dict]:
    path = verdicts_path(ts)
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return []


def due_followups(now: float | None = None) -> list[dict]:
    now = now if now is not None else time.time()
    due = []
    for e in load_entries(now):
        for mark, off in MARK_OFFSETS.items():
            if e["followups"].get(mark) is None and now >= e["ts"] + off:
                due.append({"token": e["token"], "symbol": e.get("symbol"),
                            "mark": mark, "ts": e["ts"],
                            "entry_price": (e.get("pick_ref") or {}).get("entry_price"),
                            "score": e.get("score"), "verdict": e.get("verdict")})
    return due


# ------------------------------------------------------------------ price
def fetch_price(addr: str) -> float | None:
    """Realtime price via gmgn-cli (raw JSON), proxied when the VPS IP is
    banned. Returns None when unavailable — never fabricates a number."""
    cmd = ["gmgn-cli", "token", "info", "--chain", "sol", "--address", addr, "--raw"]
    try:
        out = subprocess_run(cmd)
        if out and out.returncode == 0 and out.stdout.strip():
            return _extract_price(out.stdout)
    except Exception:
        pass
    # proxy path (gmgn_cli_proxy.run_cli) — read-only, no keys touched
    try:
        sys.path.insert(0, "/opt/ares/wallet_intel")
        from gmgn_cli_proxy import run_cli  # noqa: PLC0415
        out = run_cli(cmd, timeout=60)
        if out and getattr(out, "returncode", -1) == 0 and (out.stdout or "").strip():
            return _extract_price(out.stdout)
    except Exception:
        pass
    return None


def _extract_price(stdout: str) -> float | None:
    """gmgn token info --raw returns JSON; the price hides in various nests.
    Dig defensively for a plausible float."""
    try:
        data = json.loads(stdout)
    except Exception:
        return None
    for node in (data,
                 data.get("token") if isinstance(data, dict) else None,
                 data.get("data") if isinstance(data, dict) else None):
        if not isinstance(node, dict):
            continue
        for k in ("price", "priceUsd", "currentPrice", "lastPrice"):
            v = node.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        if isinstance(node.get("token"), dict):
            for k in ("price", "priceUsd", "currentPrice"):
                v = node["token"].get(k)
                if isinstance(v, (int, float)) and v > 0:
                    return float(v)
    return None


def apply_marks(mark: str, token: str | None = None,
                now: float | None = None) -> list[dict]:
    """Follow-up hook: fetch current price for due verdicts and record what
    happened. Returns a list of {token, mark, return_pct, error?}."""
    now = now if now is not None else time.time()
    due = [d for d in due_followups(now) if d["mark"] == mark
           and (token is None or d["token"].lower() == token.lower())]
    results = []
    for d in due:
        price = fetch_price(d["token"])
        if price is None:
            results.append({"token": d["token"], "mark": mark, "error": "price unavailable"})
            continue
        ret = None
        if d["entry_price"]:
            ret = round(100.0 * (price - d["entry_price"]) / d["entry_price"], 2)
        _store_mark(d["token"], d["ts"], mark, price, ret, now)
        results.append({"token": d["token"], "symbol": d["symbol"], "mark": mark,
                        "price": price, "return_pct": ret})
    return results


def _store_mark(token: str, ts: float, mark: str, price: float, ret: float | None,
                now: float) -> None:
    path = verdicts_path(ts)
    if not os.path.exists(path):
        return
    with open(path) as f:
        entries = json.load(f)
    for e in entries:
        if e["token"] == token and abs(e["ts"] - ts) < 1e-6:
            e["followups"][mark] = {"price": price, "return_pct": ret, "ts": round(now, 3)}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(entries, f, indent=2)
    os.replace(tmp, path)


def subprocess_run(cmd: list[str]):
    import subprocess
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except Exception:
        return None


# -------------------------------------------------------------------- http
def serve(port: int = 8010) -> None:
    import http.server

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            from urllib.parse import parse_qs, urlparse
            path = urlparse(self.path).path
            if path == "/health":
                return self._json(200, {"ok": True, "service": "strategy-lab-hub",
                                        "schema_status": STATUS})
            if path == "/api/convergence":
                q = parse_qs(urlparse(self.path).query)
                tok = (q.get("token") or [""])[0]
                if not tok:
                    return self._json(400, {"error": "token query param required"})
                bundle = compute_bundle(tok)
                if (q.get("record") or ["0"])[0] == "1":
                    append_verdict(bundle)
                return self._json(200, bundle)
            return self._json(404, {"error": "not found"})

        def _json(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"hub convergence listening on 127.0.0.1:{port} (schema {STATUS})", flush=True)
    srv.serve_forever()


# ------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description="convergence hub endpoint")
    ap.add_argument("target", nargs="?", help="token address, or 'top' [N]")
    ap.add_argument("n", nargs="?", type=int, default=5, help="count for 'top'")
    ap.add_argument("--mark", choices=sorted(MARK_OFFSETS), help="follow-up hook: record 6h/24h marks")
    ap.add_argument("--token", help="restrict --mark to one token")
    ap.add_argument("--due", action="store_true", help="list verdicts with due follow-ups")
    ap.add_argument("--serve", action="store_true", help="run HTTP endpoint")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--no-record", action="store_true", help="do not append to verdict log")
    args = ap.parse_args()

    if args.serve:
        return serve(args.port)
    if args.due:
        for d in due_followups():
            print(json.dumps(d))
        return
    if args.mark:
        results = apply_marks(args.mark, args.token)
        print(json.dumps({"mark": args.mark, "updated": results}, indent=2))
        if not results:
            print("(no due verdicts for this mark — nothing recorded)")
        return
    if not args.target:
        ap.error("need a token address, 'top', or --due/--mark/serve")

    if args.target == "top":
        picks = fetch_picks_http()
        print(f"# {len(picks)} current fusion picks (source: {PICKS_API})", flush=True)
        for p in picks[: max(1, args.n)]:
            b = compute_bundle(p["token_addr"])
            if not args.no_record:
                append_verdict(b)
            print(json.dumps(b, indent=2))
            print("---")
        return

    b = compute_bundle(args.target)
    if not args.no_record:
        append_verdict(b)
    print(json.dumps(b, indent=2))


if __name__ == "__main__":
    main()
