#!/usr/bin/env python3


import time, requests, pandas as pd, sys, traceback
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# ─── CONFIG ─────────────────────────────────────────────────────────
TOP_SOL_TOKENS      = 100          # tokens per scan
MAX_CPM             = 40           # keep <50 CoinGecko calls/min
TOKEN_PAUSE_SEC     = 35           # visible wait after each token
MAX_CAPITAL_USD     = 1_000        # never deploy more than this
VOLUME_FRACTION     = 0.002        # trade ≤0.2 % of 24 h volume
FEE_RATE            = 0.003        # taker fee per leg (0.30 %)
SLIPPAGE_SCALER     = 0.10         # 1 % slippage for 10 % of 24 h vol
DEX_WORDS = [
    "dex","swap","router","raydium","orca","meteora","lifinity",
    "phoenix","cykura","jupiter","pancakeswap","birdeye","thruster","goosefx"
]
MAX_RETRIES_429     = 3
# ────────────────────────────────────────────────────────────────────

_calls, _start = 0, time.perf_counter()

def cg_get(url, label="", retries=MAX_RETRIES_429):
    """CoinGecko GET with minute-bucket rate limiting and 429 back-off."""
    global _calls, _start
    if _calls >= MAX_CPM:
        elapsed = time.perf_counter() - _start
        if elapsed < 60:
            time.sleep(60 - elapsed)
        _calls, _start = 0, time.perf_counter()

    resp = requests.get(url, timeout=10)
    _calls += 1

    if resp.status_code == 429:
        if retries <= 0:
            print(f"⚠️  skip {label} after too many 429s")
            resp.raise_for_status()
        wait = int(resp.headers.get("Retry-After", 10))
        wait = min(max(wait, 5), 60)
        print(f"429 on {label or url[:40]} – sleeping {wait}s")
        time.sleep(wait)
        return cg_get(url, label, retries-1)

    resp.raise_for_status()
    return resp

def wait_with_countdown(seconds: int):
    for t in range(seconds, 0, -1):
        print(f"\r⏳  next token in {t:2d}s ", end="", flush=True)
        time.sleep(1)
    print("\r", end="")              # clear line

def tickers_df(ticks):
    rows = []
    for t in ticks:
        price = t.get("converted_last", {}).get("usd") or 0.0
        if price == 0:
            continue
        rows.append({
            "Market": t["market"]["name"],
            "Pair"  : f"{t['base']}/{t['target']}",
            "Price" : float(price),
            "Volume": float(t.get("converted_volume", {}).get("usd", 0))
        })
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.groupby("Market", as_index=False).agg(
            Pair=("Pair","first"),
            Price=("Price","mean"),
            Volume=("Volume","sum"))
    return df[df["Market"].str.lower().apply(
            lambda s: any(w in s for w in DEX_WORDS))]\
             .sort_values("Volume", ascending=False)

def analyse(name, cid):
    print(f"\n── {name} ({cid}) ──")
    ticks_url = f"https://api.coingecko.com/api/v3/coins/{cid}/tickers"
    ticks     = cg_get(ticks_url, cid).json()["tickers"]
    df        = tickers_df(ticks)
    if df.empty:
        print("No DEX markets found."); return

    print(df.to_string(index=False,
          columns=["Market","Pair","Price","Volume"],
          formatters={"Price":"${:,.6f}".format,
                      "Volume":"${:,.0f}".format}))

    if len(df) < 2:
        print("\nOnly one DEX venue – no intra-DEX arbitrage.")
        return

    low , high  = df.loc[df.Price.idxmin()], df.loc[df.Price.idxmax()]
    spread_pct  = (high.Price - low.Price) / low.Price * 100

    base_buy,  quote_buy  = low.Pair.split("/")
    base_sell, quote_sell = high.Pair.split("/")

    vol_buy  = max(low.Volume , 1)
    vol_sell = max(high.Volume, 1)
    trade_cap = min(MAX_CAPITAL_USD,
                    vol_buy  * VOLUME_FRACTION,
                    vol_sell * VOLUME_FRACTION)

    if trade_cap < 1:
        print("\nTrade size would be < $1 — skipping.")
        return

    fee_buy  = trade_cap              * FEE_RATE
    tokens   = (trade_cap - fee_buy) / low.Price

    gross_rev = tokens * high.Price
    fee_sell  = gross_rev             * FEE_RATE

    slip_buy  = trade_cap * SLIPPAGE_SCALER * (trade_cap / vol_buy)
    slip_sell = gross_rev * SLIPPAGE_SCALER * (trade_cap / vol_sell)

    net_profit = gross_rev - fee_sell - slip_buy - fee_buy - slip_sell - trade_cap
    roi_pct    = net_profit / trade_cap * 100

    print(
        f"\nArbitrage plan:\n"
        f"• Capital deployed      : ${trade_cap:,.2f}\n"
        f"• Expected spread       : {spread_pct:.2f} %\n"
        f"• Fees (both legs)      : ${fee_buy+fee_sell:,.2f}\n"
        f"• Slippage allowance    : ${slip_buy+slip_sell:,.2f}\n"
        f"→ Net P/L              : ${net_profit:,.2f}   ({roi_pct:.2f} % ROI)\n"
        f"Path: buy **{base_buy}/{quote_buy}** on **{low.Market}** "
        f"@ ${low.Price:,.6f}  →  sell on **{high.Market}** "
        f"@ ${high.Price:,.6f}"
    )

def ensure_vs_currency(url):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "vs_currency" not in qs:
        print("⚠️  URL missing required 'vs_currency' param, adding vs_currency=usd")
        qs["vs_currency"] = "usd"
    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))

def scan_once():
    global _calls, _start
    _calls, _start = 0, time.perf_counter()

    try:
        markets_url = input("Paste full /coins/markets API endpoint URL (including all query params):\n> ").strip()
    except Exception as e:
        print("Could not read input from user. If running in a restricted environment, pass the URL as a script argument instead.")
        sys.exit(1)

    if not markets_url.startswith("http"):
        print("Invalid URL. Exiting.")
        sys.exit(1)

    # Check and fix URL params
    markets_url = ensure_vs_currency(markets_url)

    try:
        markets = cg_get(markets_url, "market-caps").json()
    except Exception as e:
        print(f"❌ API error: {e}\nCheck your URL and parameters.")
        return

    markets = sorted(markets, key=lambda x: x.get("market_cap",0), reverse=True)
    top_tokens = [(m["name"], m["id"]) for m in markets[:TOP_SOL_TOKENS]]
    print(f"Analysing {len(top_tokens)} Solana tokens …")

    for n,c in top_tokens:
        analyse(n,c)
        wait_with_countdown(TOKEN_PAUSE_SEC)

if __name__ == "__main__":
    try:
        while True:
            cycle_start = time.perf_counter()
            try:
                scan_once()
            except Exception:
                print("\n‼️  Unhandled error; retrying in 5 s\n")
                traceback.print_exc()
                time.sleep(5)

            elapsed = time.perf_counter() - cycle_start
            spare   = max(0, 60 - elapsed)
            if spare:
                print(f"\n--- cycle done; sleeping {spare:.1f}s "
                      "(rate-limit) ---\n")
                time.sleep(spare)
            else:
                print("\n--- cycle done; immediately restarting ---\n")

    except KeyboardInterrupt:
        print("\n👋  Stopping scanner. Bye!")
        sys.exit(0)
