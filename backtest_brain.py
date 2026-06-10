#!/usr/bin/env python3
"""
backtest_brain.py — Adaptive Trading Strategy Optimizer v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tests 7 institutional intraday strategies across 11 tickers using
walk-forward validation. Scores each by win rate, profit factor,
and expectancy. Outputs brain_state.json consumed by scanner.html.

Strategies tested:
  EMA_CROSS     — EMA5×EMA20 crossover above VWAP (baseline)
  VWAP_RECLAIM  — VWAP reclaim with volume spike + slope filter
  ORB_5         — 5-min opening range breakout + RVOL + VWAP
  ORB_15        — 15-min opening range breakout + RVOL + VWAP
  GAP_GO        — Gap ≥2% + ORB15 aligned + RVOL ≥2.0
  EMA_PULLBACK  — First pullback to 9/20 EMA zone in uptrend
  HOD_BREAK     — Intraday HOD break with RVOL + VWAP confirm

Exit logic: ATR-based 2:1 R:R (stop = 0.8×ATR14, target = 1.6×ATR14)
Walk-forward: 9 months training / 3 months OOS

Usage:
  # Set API key then run:
  POLYGON_API_KEY=your_key python3 backtest_brain.py

  # Or create .env file:
  echo "POLYGON_API_KEY=your_key" > .env
  python3 backtest_brain.py

  # Schedule via cron (runs every weekday at 7:30 AM ET):
  30 7 * * 1-5 cd /path/to/trading-suite && POLYGON_API_KEY=your_key python3 backtest_brain.py

  # Or let GitHub Actions run it automatically (see .github/workflows/update_brain.yml)
  # Add POLYGON_API_KEY as a repository secret in GitHub Settings → Secrets → Actions
"""

import pandas as pd
import numpy as np
import requests
import json
import os
import sys
import time
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# ━━━ CONFIGURATION ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TICKERS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA', 'AMD', 'SPY', 'QQQ', 'IWM']

OUTPUT_PATH = Path(__file__).parent / "brain_state.json"

# Walk-forward windows (in trading days)
HISTORY_DAYS    = 375   # ~15 months of data fetched
OOS_DAYS        = 63    # ~3 months OOS (what we report)
TRAIN_DAYS      = 189   # ~9 months training

# Exit parameters (all strategies use consistent 2:1 R:R)
STOP_ATR_MULT   = 0.8   # stop = 0.8 × ATR14 from entry
TARGET_ATR_MULT = 1.6   # target = 1.6 × ATR14  →  2:1 R:R
FORWARD_BARS    = 12    # max 12 × 5-min bars = 60 min to exit

MIN_TRADES      = 12    # skip strategies with fewer OOS trades

# Strategy configurations
STRATEGY_CONFIGS = {
    'EMA_CROSS':    {'ema_fast': 5,  'ema_slow': 20, 'rvol_min': 1.0},
    'VWAP_RECLAIM': {'rvol_min': 2.0, 'slope_bars': 3},
    'ORB_5':        {'orb_bars': 1,  'rvol_min': 1.5},   # 1 × 5-min bar
    'ORB_15':       {'orb_bars': 3,  'rvol_min': 1.5},   # 3 × 5-min bars
    'GAP_GO':       {'orb_bars': 3,  'rvol_min': 2.0, 'gap_min': 0.02},
    'EMA_PULLBACK': {'ema_fast': 9,  'ema_slow': 20, 'rvol_min': 1.2},
    'HOD_BREAK':    {'rvol_min': 1.5, 'min_bar': 6},     # 30+ min into session
}

STRATEGY_NAMES  = list(STRATEGY_CONFIGS.keys())
API_DELAY       = 0.25  # seconds between ticker fetches (rate limit)


# ━━━ API KEY ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_api_key() -> str:
    key = os.getenv("POLYGON_API_KEY", "").strip()
    if not key:
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("POLYGON_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return key


# ━━━ DATA FETCHING ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_bars(ticker: str, api_key: str) -> Optional[pd.DataFrame]:
    """Fetch 5-min bars from Polygon.io for the past HISTORY_DAYS days."""
    end_dt   = date.today()
    start_dt = end_dt - timedelta(days=HISTORY_DAYS + 20)

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/5/minute"
        f"/{start_dt.isoformat()}/{end_dt.isoformat()}"
        f"?adjusted=true&sort=asc&limit=50000&apiKey={api_key}"
    )

    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if data.get("resultsCount", 0) == 0 or not data.get("results"):
            print(f"    ⚠  No data returned for {ticker}")
            return None

        df = pd.DataFrame(data["results"])
        df["ts"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert("America/New_York")
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df = df.set_index("ts").sort_index()
        df = df.between_time("09:30", "15:55")           # market hours only
        df = df[["open", "high", "low", "close", "volume"]]

        # Trim to exactly HISTORY_DAYS trading days
        unique_days = sorted(df.index.date)
        if len(unique_days) > HISTORY_DAYS:
            keep_from = unique_days[-HISTORY_DAYS]
            df = df[df.index.date >= keep_from]

        print(f"    ✓  {ticker}: {len(df):,} bars  ({df.index.date.min()} → {df.index.date.max()})")
        return df

    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response else "?"
        if code == 403:
            print(f"    ✗  {ticker}: API key invalid or insufficient tier (403)")
        else:
            print(f"    ✗  {ticker}: HTTP {code}")
        return None
    except Exception as e:
        print(f"    ✗  {ticker}: {type(e).__name__}: {e}")
        return None


# ━━━ INDICATORS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def atr14(df: pd.DataFrame) -> pd.Series:
    hl  = df["high"] - df["low"]
    hc  = (df["high"] - df["close"].shift(1)).abs()
    lc  = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(14, min_periods=5).mean()


def vwap_daily(df: pd.DataFrame) -> pd.Series:
    """Intraday VWAP resetting each calendar day."""
    df  = df.copy()
    day = df.index.date
    typ = (df["high"] + df["low"] + df["close"]) / 3
    cum_tv  = pd.Series(index=df.index, dtype=float)
    cum_vol = pd.Series(index=df.index, dtype=float)

    for d in np.unique(day):
        mask = day == d
        tv   = (typ[mask] * df["volume"][mask]).cumsum()
        v    = df["volume"][mask].cumsum()
        cum_tv[mask]  = tv.values
        cum_vol[mask] = v.values

    return cum_tv / cum_vol.replace(0, np.nan)


def rvol_rolling(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """RVOL = current bar volume / rolling avg volume of same time-of-day over past N days."""
    tof   = df.index.time
    vol   = df["volume"].copy()
    rvol  = pd.Series(1.0, index=df.index)

    for t in np.unique(tof):
        mask    = tof == t
        v_slot  = vol[mask]
        avg     = v_slot.rolling(lookback, min_periods=5).mean().shift(1)
        rvol[mask] = (v_slot / avg.replace(0, np.nan)).fillna(1.0)

    return rvol


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Core indicators
    df["ema5"]  = ema(df["close"], 5)
    df["ema9"]  = ema(df["close"], 9)
    df["ema20"] = ema(df["close"], 20)
    df["atr"]   = atr14(df)
    df["vwap"]  = vwap_daily(df)
    df["rvol"]  = rvol_rolling(df)

    # Session metadata
    df["date"]       = df.index.date
    df["bar_of_day"] = df.groupby("date").cumcount()   # 0-indexed: bar 0 = 9:30 bar

    # Daily gap (open vs prior close)
    daily_first  = df.groupby("date")["open"].first()
    daily_last   = df.groupby("date")["close"].last()
    prev_close   = daily_last.shift(1)
    gap_pct      = (daily_first - prev_close) / prev_close
    df["gap_pct"] = df["date"].map(gap_pct).fillna(0.0)

    # Opening range high/low (5-min = bar 0; 15-min = bars 0-2)
    for label, max_bar in [("or5", 0), ("or15", 2)]:
        or_h = df[df["bar_of_day"] <= max_bar].groupby("date")["high"].max()
        or_l = df[df["bar_of_day"] <= max_bar].groupby("date")["low"].min()
        df[f"{label}_h"] = df["date"].map(or_h)
        df[f"{label}_l"] = df["date"].map(or_l)

    # Running HOD / LOD (exclusive of current bar via shift)
    df["hod"] = df.groupby("date")["high"].transform(
        lambda x: x.expanding().max().shift(1)
    )
    df["lod"] = df.groupby("date")["low"].transform(
        lambda x: x.expanding().min().shift(1)
    )

    return df.dropna(subset=["ema5", "ema20", "atr", "vwap"])


# ━━━ SIGNAL GENERATORS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def sig_ema_cross(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """EMA5 crosses EMA20, price above VWAP."""
    fast, slow = cfg["ema_fast"], cfg["ema_slow"]
    fast_col   = f"ema{fast}"
    slow_col   = f"ema{slow}"

    long  = ((df[fast_col] >  df[slow_col]) & (df[fast_col].shift(1) <= df[slow_col].shift(1)) &
             (df["close"] > df["vwap"]) & (df["rvol"] >= cfg["rvol_min"]))
    short = ((df[fast_col] <  df[slow_col]) & (df[fast_col].shift(1) >= df[slow_col].shift(1)) &
             (df["close"] < df["vwap"]) & (df["rvol"] >= cfg["rvol_min"]))

    s = pd.Series(0, index=df.index)
    s[long]  =  1
    s[short] = -1
    return s


def sig_vwap_reclaim(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Price crosses VWAP from below with volume spike and rising slope."""
    sb   = cfg["slope_bars"]
    slope_up   = df["vwap"] > df["vwap"].shift(sb)
    slope_down = df["vwap"] < df["vwap"].shift(sb)

    long  = ((df["close"]       > df["vwap"]) & (df["close"].shift(1)       <= df["vwap"].shift(1)) &
             (df["rvol"] >= cfg["rvol_min"]) & slope_up   & (df["bar_of_day"] >= 3))
    short = ((df["close"]       < df["vwap"]) & (df["close"].shift(1)       >= df["vwap"].shift(1)) &
             (df["rvol"] >= cfg["rvol_min"]) & slope_down & (df["bar_of_day"] >= 3))

    s = pd.Series(0, index=df.index)
    s[long]  =  1
    s[short] = -1
    return s


def sig_orb(df: pd.DataFrame, cfg: dict, label: str) -> pd.Series:
    """Opening range breakout — close above OR high or below OR low."""
    min_bar = cfg["orb_bars"]
    h_col   = f"{label}_h"
    l_col   = f"{label}_l"

    long  = ((df["bar_of_day"] > min_bar) &
             (df["close"]       > df[h_col]) & (df["close"].shift(1) <= df[h_col]) &
             (df["close"]       > df["vwap"]) & (df["rvol"] >= cfg["rvol_min"]))
    short = ((df["bar_of_day"] > min_bar) &
             (df["close"]       < df[l_col]) & (df["close"].shift(1) >= df[l_col]) &
             (df["close"]       < df["vwap"]) & (df["rvol"] >= cfg["rvol_min"]))

    s = pd.Series(0, index=df.index)
    s[long]  =  1
    s[short] = -1
    return s


def sig_gap_go(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Gap ≥2% + ORB15 breakout aligned with gap direction."""
    min_bar = cfg["orb_bars"]
    gm      = cfg["gap_min"]

    long  = ((df["gap_pct"] >= gm) & (df["bar_of_day"] > min_bar) &
             (df["close"]   > df["or15_h"]) & (df["close"].shift(1) <= df["or15_h"]) &
             (df["close"]   > df["vwap"])   & (df["rvol"] >= cfg["rvol_min"]))
    short = ((df["gap_pct"] <= -gm) & (df["bar_of_day"] > min_bar) &
             (df["close"]   < df["or15_l"]) & (df["close"].shift(1) >= df["or15_l"]) &
             (df["close"]   < df["vwap"])   & (df["rvol"] >= cfg["rvol_min"]))

    s = pd.Series(0, index=df.index)
    s[long]  =  1
    s[short] = -1
    return s


def sig_ema_pullback(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """First pullback to 9/20 EMA zone after prior momentum leg, with drying volume."""
    # Prior momentum: price was ≥1.5% above ema20 within last 6 bars
    prior_high = df["close"].rolling(6).max().shift(1)
    had_momentum_long  = (prior_high / df["ema20"]) >= 1.015
    had_momentum_short = (df["close"].rolling(6).min().shift(1) / df["ema20"]) <= 0.985

    # In the 9/20 zone
    in_zone_long  = (df["close"] >= df["ema20"] * 0.998) & (df["close"] <= df["ema9"] * 1.003)
    in_zone_short = (df["close"] <= df["ema20"] * 1.002) & (df["close"] >= df["ema9"] * 0.997)

    long  = (had_momentum_long  & in_zone_long  & (df["ema9"] > df["ema20"]) &
             (df["close"] > df["vwap"]) & (df["bar_of_day"] >= 6))
    short = (had_momentum_short & in_zone_short & (df["ema9"] < df["ema20"]) &
             (df["close"] < df["vwap"]) & (df["bar_of_day"] >= 6))

    s = pd.Series(0, index=df.index)
    s[long]  =  1
    s[short] = -1
    return s


def sig_hod_break(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """HOD/LOD break with RVOL confirm and VWAP alignment."""
    min_bar = cfg["min_bar"]

    long  = ((df["bar_of_day"] >= min_bar) &
             (df["close"] > df["hod"]) & (df["close"].shift(1) <= df["hod"].shift(1)) &
             (df["close"] > df["vwap"]) & (df["rvol"] >= cfg["rvol_min"]))
    short = ((df["bar_of_day"] >= min_bar) &
             (df["close"] < df["lod"]) & (df["close"].shift(1) >= df["lod"].shift(1)) &
             (df["close"] < df["vwap"]) & (df["rvol"] >= cfg["rvol_min"]))

    s = pd.Series(0, index=df.index)
    s[long]  =  1
    s[short] = -1
    return s


def get_signals(df: pd.DataFrame) -> Dict[str, pd.Series]:
    cfg = STRATEGY_CONFIGS
    return {
        "EMA_CROSS":    sig_ema_cross(df,   cfg["EMA_CROSS"]),
        "VWAP_RECLAIM": sig_vwap_reclaim(df, cfg["VWAP_RECLAIM"]),
        "ORB_5":        sig_orb(df,  cfg["ORB_5"],  "or5"),
        "ORB_15":       sig_orb(df,  cfg["ORB_15"], "or15"),
        "GAP_GO":       sig_gap_go(df,  cfg["GAP_GO"]),
        "EMA_PULLBACK": sig_ema_pullback(df, cfg["EMA_PULLBACK"]),
        "HOD_BREAK":    sig_hod_break(df,   cfg["HOD_BREAK"]),
    }


# ━━━ BACKTESTING ENGINE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def backtest(df: pd.DataFrame, signals: pd.Series) -> Dict:
    """ATR-based 2:1 R:R exit. Returns performance metrics dict."""
    closes = df["close"].values
    atrs   = df["atr"].values
    sigs   = signals.values
    n      = len(sigs)

    trades = []
    i = 0
    while i < n - FORWARD_BARS:
        if sigs[i] != 0:
            direction   = int(sigs[i])
            entry_price = closes[i]
            atr_val     = atrs[i]

            if np.isnan(atr_val) or atr_val <= 0 or np.isnan(entry_price):
                i += 1
                continue

            stop   = entry_price - direction * STOP_ATR_MULT   * atr_val
            target = entry_price + direction * TARGET_ATR_MULT * atr_val
            rr_win = TARGET_ATR_MULT / STOP_ATR_MULT  # = 2.0

            result_r = None
            for j in range(1, FORWARD_BARS + 1):
                idx = i + j
                if idx >= n:
                    break
                c = closes[idx]
                if direction == 1:
                    if c <= stop:   result_r = -1.0; break
                    if c >= target: result_r =  rr_win; break
                else:
                    if c >= stop:   result_r = -1.0; break
                    if c <= target: result_r =  rr_win; break

            # Time exit if neither hit
            if result_r is None:
                exit_idx  = min(i + FORWARD_BARS, n - 1)
                exit_p    = closes[exit_idx]
                result_r  = direction * (exit_p - entry_price) / (STOP_ATR_MULT * atr_val)

            trades.append(result_r)
            i += FORWARD_BARS   # skip forward to avoid overlapping trades
        else:
            i += 1

    if len(trades) < MIN_TRADES:
        return {
            "win_rate": 0.0, "profit_factor": 0.0, "avg_win_r": 0.0,
            "avg_loss_r": 0.0, "expectancy": -9.0, "trade_count": len(trades), "score": 0.0
        }

    wins   = [r for r in trades if r > 0]
    losses = [r for r in trades if r <= 0]

    win_rate      = len(wins) / len(trades)
    gross_profit  = sum(wins)
    gross_loss    = abs(sum(losses)) if losses else 1e-9
    profit_factor = gross_profit / gross_loss
    avg_win_r     = np.mean(wins) if wins else 0.0
    avg_loss_r    = abs(np.mean(losses)) if losses else 0.0
    expectancy    = (win_rate * avg_win_r) - ((1 - win_rate) * avg_loss_r)

    # Composite score: profit factor carries most weight (edge driver)
    pf_norm  = min(profit_factor / 3.0, 1.0)
    exp_norm = min(max(expectancy, 0) / 2.0, 1.0)
    score    = win_rate * 0.30 + pf_norm * 0.40 + exp_norm * 0.30

    return {
        "win_rate":      round(float(win_rate),      4),
        "profit_factor": round(float(profit_factor), 3),
        "avg_win_r":     round(float(avg_win_r),     3),
        "avg_loss_r":    round(float(avg_loss_r),    3),
        "expectancy":    round(float(expectancy),    4),
        "trade_count":   int(len(trades)),
        "score":         round(float(score),         4),
    }


# ━━━ WALK-FORWARD ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def walk_forward(df: pd.DataFrame) -> Dict[str, Dict]:
    """
    Split data: first TRAIN_DAYS = training, last OOS_DAYS = out-of-sample.
    Run all 7 strategies on OOS period. Return per-strategy results.
    """
    trading_days = sorted(df.index.date)
    if len(trading_days) < TRAIN_DAYS + OOS_DAYS:
        return {}

    split_date = trading_days[-(OOS_DAYS)]
    oos_df   = df[df.index.date >= split_date].copy()
    train_df = df[df.index.date <  split_date].copy()

    if len(oos_df) < OOS_DAYS * 5:
        return {}

    results  = {}
    oos_sigs = get_signals(oos_df)

    for name in STRATEGY_NAMES:
        oos_res   = backtest(oos_df,   oos_sigs[name])
        train_res = backtest(train_df, get_signals(train_df)[name])
        results[name] = {"oos": oos_res, "train": train_res}

    return results


# ━━━ STRATEGY SELECTION ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def best_strategy(results: Dict[str, Dict]) -> Tuple[str, float]:
    """Return (strategy_name, score) for the best OOS strategy."""
    best_name, best_score = "EMA_CROSS", 0.0
    for name, res in results.items():
        oos = res.get("oos", {})
        if oos.get("trade_count", 0) < MIN_TRADES:
            continue
        s = oos.get("score", 0.0)
        if s > best_score:
            best_score = s
            best_name  = name
    return best_name, best_score


# ━━━ MAIN ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_brain():
    api_key = get_api_key()
    if not api_key:
        print("❌  No POLYGON_API_KEY found.")
        print("    Set env var:  export POLYGON_API_KEY=your_key")
        print("    Or create:    echo 'POLYGON_API_KEY=your_key' > .env")
        sys.exit(1)

    print(f"\n🧠  Backtest Brain v1.0  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"    Tickers  : {', '.join(TICKERS)}")
    print(f"    Strategies: {', '.join(STRATEGY_NAMES)}")
    print(f"    Walk-fwd : {TRAIN_DAYS}d train  /  {OOS_DAYS}d OOS  |  2:1 R:R  |  60-min exits\n")

    brain = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "config": {
            "strategies": STRATEGY_NAMES,
            "oos_days":   OOS_DAYS,
            "train_days": TRAIN_DAYS,
            "stop_atr_mult":   STOP_ATR_MULT,
            "target_atr_mult": TARGET_ATR_MULT,
            "forward_bars":    FORWARD_BARS,
            "min_trades":      MIN_TRADES,
        },
        "tickers": {},
        "strategy_leaderboard": [],
    }

    # Accumulators for cross-ticker leaderboard
    lb: Dict[str, Dict] = {s: {"score": 0, "win_rate": 0, "pf": 0, "exp": 0, "n": 0}
                            for s in STRATEGY_NAMES}

    for ticker in TICKERS:
        print(f"  📊  {ticker}")
        df = fetch_bars(ticker, api_key)
        if df is None or len(df) < (TRAIN_DAYS + OOS_DAYS) * 5:
            print(f"       ↳ Skipped (insufficient data)\n")
            continue
        time.sleep(API_DELAY)

        try:
            df = add_all_indicators(df)
        except Exception as e:
            print(f"       ↳ Indicator error: {e}\n")
            continue

        try:
            results = walk_forward(df)
        except Exception as e:
            print(f"       ↳ Backtest error: {e}\n")
            continue

        if not results:
            print(f"       ↳ No results\n")
            continue

        # Best strategy for this ticker
        bname, bscore = best_strategy(results)
        boos           = results[bname]["oos"]

        # Build per-ticker summary
        all_strat = {}
        for sname, sres in results.items():
            oos   = sres["oos"]
            train = sres["train"]
            all_strat[sname] = {
                "oos_win_rate":      oos["win_rate"],
                "oos_profit_factor": oos["profit_factor"],
                "oos_expectancy":    oos["expectancy"],
                "oos_avg_win_r":     oos["avg_win_r"],
                "oos_trades":        oos["trade_count"],
                "oos_score":         oos["score"],
                "train_win_rate":    train["win_rate"],
                "train_profit_factor": train["profit_factor"],
            }
            if oos["trade_count"] >= MIN_TRADES:
                lb[sname]["score"]    += oos["score"]
                lb[sname]["win_rate"] += oos["win_rate"]
                lb[sname]["pf"]       += oos["profit_factor"]
                lb[sname]["exp"]      += oos["expectancy"]
                lb[sname]["n"]        += 1

        brain["tickers"][ticker] = {
            "best_strategy":     bname,
            "oos_win_rate":      boos["win_rate"],
            "oos_profit_factor": boos["profit_factor"],
            "oos_expectancy":    boos["expectancy"],
            "oos_avg_win_r":     boos["avg_win_r"],
            "oos_trades":        boos["trade_count"],
            "oos_score":         boos["score"],
            "all_strategies":    all_strat,
        }

        print(f"       ↳ Best: {bname:<15}  WR: {boos['win_rate']:.1%}  "
              f"PF: {boos['profit_factor']:.2f}  Exp: {boos['expectancy']:+.3f}R  "
              f"Trades: {boos['trade_count']}\n")

    # Build global leaderboard
    leaderboard = []
    for sname, acc in lb.items():
        if acc["n"] == 0:
            continue
        n = acc["n"]
        leaderboard.append({
            "strategy":            sname,
            "avg_score":           round(acc["score"]    / n, 4),
            "avg_win_rate":        round(acc["win_rate"] / n, 4),
            "avg_profit_factor":   round(acc["pf"]       / n, 3),
            "avg_expectancy":      round(acc["exp"]       / n, 4),
            "ticker_count":        n,
        })
    leaderboard.sort(key=lambda x: x["avg_score"], reverse=True)
    brain["strategy_leaderboard"] = leaderboard

    # Save output
    OUTPUT_PATH.write_text(json.dumps(brain, indent=2, default=str))
    print(f"✅  Saved → {OUTPUT_PATH}\n")

    # Print leaderboard
    print("🏆  Strategy Leaderboard (ranked by avg composite score across all tickers):")
    print(f"    {'Rank':<5}{'Strategy':<17}{'Avg WR':>8}{'Avg PF':>8}{'Avg Exp':>9}{'Tickers':>9}")
    print(f"    {'-'*52}")
    for i, row in enumerate(leaderboard, 1):
        print(f"    {i:<5}{row['strategy']:<17}{row['avg_win_rate']:.1%}{row['avg_profit_factor']:>8.2f}"
              f"{row['avg_expectancy']:>+9.3f}R{row['ticker_count']:>8}")

    print(f"\n📈  Best Strategy Per Ticker:")
    print(f"    {'Ticker':<8}{'Strategy':<17}{'OOS WR':>8}{'OOS PF':>8}{'Expectancy':>11}{'Trades':>8}")
    print(f"    {'-'*60}")
    for tk, data in brain["tickers"].items():
        print(f"    {tk:<8}{data['best_strategy']:<17}{data['oos_win_rate']:.1%}"
              f"{data['oos_profit_factor']:>8.2f}{data['oos_expectancy']:>+11.3f}R"
              f"{data['oos_trades']:>8}")

    return brain


if __name__ == "__main__":
    run_brain()
