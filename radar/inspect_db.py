#!/usr/bin/env python3
"""radar/inspect_db.py — READ-ONLY inspection of the radar lane's data sources.

Opens wallet_intel.db and vantage.db in mode=ro (never writes).
"""
import json
import sqlite3
from collections import Counter

WALLET_INTEL = "/opt/ares/wallet_intel/wallet_intel.db"
VANTAGE = "/opt/ares/Vantage/data/vantage.db"


def connect(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def j(v):
    if v is None:
        return None
    try:
        return json.loads(v)
    except Exception:
        return v


def main():
    # ---------- wallet_intel deep dive ----------
    con = connect(WALLET_INTEL)
    print("=== WALLET_INTEL ===")
    print("\n-- wallets tag histogram (ALL rows) --")
    tagc = Counter()
    for (raw,) in con.execute("SELECT tags FROM wallets WHERE tags IS NOT NULL AND tags != ''"):
        for tg in j(raw) or [raw]:
            tagc[tg] += 1
    print(dict(tagc.most_common(30)))

    print("\n-- wallets buys/sells/volume distribution --")
    for label, q in [
        ("wallets with buys>0", "SELECT COUNT(*) FROM wallets WHERE buys > 0"),
        ("wallets with sells>0", "SELECT COUNT(*) FROM wallets WHERE sells > 0"),
        ("wallets with volume>1000", "SELECT COUNT(*) FROM wallets WHERE volume_usd > 1000"),
        ("wallets with distinct_tokens>1", "SELECT COUNT(*) FROM wallets WHERE distinct_tokens > 1"),
    ]:
        print(f"   {label}: {con.execute(q).fetchone()[0]}")
    print("   top by buys:", con.execute(
        "SELECT address, buys, sells, volume_usd, distinct_tokens, tags FROM wallets ORDER BY buys DESC LIMIT 5").fetchall())
    print("   top by volume:", con.execute(
        "SELECT address, buys, sells, volume_usd, distinct_tokens, tags FROM wallets ORDER BY volume_usd DESC LIMIT 5").fetchall())
    print("   top by distinct_tokens:", con.execute(
        "SELECT address, buys, sells, volume_usd, distinct_tokens, tags FROM wallets ORDER BY distinct_tokens DESC LIMIT 5").fetchall())

    print("\n-- token_stats --")
    print("   distinct_wallets histogram:",
          con.execute("SELECT distinct_wallets, COUNT(*) FROM token_stats GROUP BY distinct_wallets ORDER BY distinct_wallets LIMIT 12").fetchall())
    print("   mint count:", con.execute("SELECT COUNT(DISTINCT mint) FROM token_stats").fetchone()[0])
    print("   rows with first_buyers len>1:",
          con.execute("SELECT COUNT(*) FROM token_stats WHERE first_buyers LIKE '%,%'").fetchone()[0])
    print("   buy_volume stats:", con.execute(
        "SELECT COUNT(*), SUM(buy_volume), AVG(buy_volume), MAX(buy_volume) FROM token_stats").fetchone())
    print("   ts range:",
          con.execute("SELECT MIN(first_buy_ts), MAX(first_buy_ts), MIN(updated_at), MAX(updated_at) FROM token_stats").fetchone())

    print("\n-- wallet_tokens --")
    print("   distinct wallets:", con.execute("SELECT COUNT(DISTINCT wallet) FROM wallet_tokens").fetchone()[0])
    print("   distinct mints:", con.execute("SELECT COUNT(DISTINCT mint) FROM wallet_tokens").fetchone()[0])
    print("   ts range:", con.execute("SELECT MIN(first_buy_ts), MAX(first_buy_ts) FROM wallet_tokens").fetchone())
    print("   multi-wallet tokens (>=3 wallets):",
          con.execute("SELECT mint, COUNT(*) c FROM wallet_tokens GROUP BY mint HAVING c >= 3 ORDER BY c DESC LIMIT 10").fetchall())
    print("   top wallet by token count:",
          con.execute("SELECT wallet, COUNT(*) c FROM wallet_tokens GROUP BY wallet ORDER BY c DESC LIMIT 10").fetchall())

    print("\n-- overlap: wallet_tokens wallets vs wallets.tags smart/smart_degen/alpha --")
    smartish = con.execute("""SELECT COUNT(DISTINCT wt.wallet) FROM wallet_tokens wt
        JOIN wallets w ON w.address = wt.wallet
        WHERE w.tags LIKE '%smart%' OR w.tags LIKE '%alpha%' OR w.tags LIKE '%backtrack%'""").fetchone()[0]
    print("   wallet_tokens wallets with smart/alpha/backtrack tag:", smartish)
    con.close()

    # ---------- vantage ----------
    con = connect(VANTAGE)
    print("\n=== VANTAGE ===")
    allt = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print("tables:", ", ".join(allt))
    for t in ("alpha_wallets", "wallet_reputation", "alpha_signals_log"):
        if t in allt:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
            print(f"\n-- {t}: {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]} rows")
            print(f"   cols: {cols}")
    if "alpha_wallets" in allt:
        rows = con.execute("SELECT * FROM alpha_wallets ORDER BY score DESC LIMIT 5").fetchall()
        cols = [r[1] for r in con.execute("PRAGMA table_info(alpha_wallets)")]
        for r in rows:
            print("   top-score:", dict(zip(cols, r)))
        print("   tier hist:", con.execute("SELECT tier, COUNT(*) FROM alpha_wallets GROUP BY tier").fetchall())
        print("   source hist:", con.execute("SELECT source, COUNT(*) FROM alpha_wallets GROUP BY source").fetchall())
        print("   chain hist:", con.execute("SELECT chain, COUNT(*) FROM alpha_wallets GROUP BY chain").fetchall())
        print("   score range:", con.execute("SELECT MIN(score), MAX(score), AVG(score) FROM alpha_wallets").fetchone())
        print("   early_entry_rate range:", con.execute(
            "SELECT MIN(early_entry_rate), MAX(early_entry_rate) FROM alpha_wallets").fetchone())
    if "alpha_signals_log" in allt:
        print("\n-- alpha_signals_log sample --")
        rows = con.execute("SELECT id, ts, kind, substr(token_or_wallet,1,20), substr(payload,1,120) FROM alpha_signals_log ORDER BY id DESC LIMIT 5").fetchall()
        for r in rows:
            print("   ", r)
        print("   kind hist:", con.execute("SELECT kind, COUNT(*) FROM alpha_signals_log GROUP BY kind").fetchall())
    con.close()


if __name__ == "__main__":
    main()
