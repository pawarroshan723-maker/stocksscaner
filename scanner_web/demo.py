"""Clearly fictional sample snapshots; never written as real market data."""
import math

SAMPLES = [
    ("DEMO-ALPHA", "TECHNOLOGY", 1284.20, "BREAKOUT", 78, 62.4, 1.64),
    ("DEMO-BETA", "FINANCIALS", 846.75, "BREAKOUT", 72, 58.1, 1.32),
    ("DEMO-GAMMA", "ENERGY", 2318.40, "SIDEWAYS", 48, 51.6, 0.92),
    ("DEMO-DELTA", "CONSUMER", 562.35, "BREAKDOWN", 69, 39.8, 1.47),
    ("DEMO-EPSILON", "INDUSTRIALS", 1789.15, "BREAKOUT", 67, 56.7, 1.22),
    ("DEMO-ZETA", "HEALTHCARE", 1045.80, "SIDEWAYS", 43, 49.2, 0.86),
    ("DEMO-ETA", "MATERIALS", 718.55, "BREAKDOWN", 64, 42.1, 1.31),
    ("DEMO-THETA", "AUTO", 1520.00, "BREAKOUT", 61, 54.9, 1.18),
    ("DEMO-IOTA", "FINANCIALS", 937.65, "SIDEWAYS", 46, 50.3, 0.94),
    ("DEMO-KAPPA", "ENERGY", 2036.90, "BREAKDOWN", 58, 44.4, 1.08),
    ("DEMO-LAMBDA", "TECHNOLOGY", 681.45, "BREAKOUT", 59, 56.2, 1.14),
    ("DEMO-MU", "CONSUMER", 1182.30, "SIDEWAYS", 41, 48.8, 0.82),
]


def snapshots(tf):
    result = []
    for i, (symbol, sector, price, signal, score, rsi, rvol) in enumerate(SAMPLES):
        # The same example is intentionally used across timeframes; it is not a
        # simulation of current NSE prices or a backtest.
        support = round(price * .98, 2)
        resistance = round(price * 1.022, 2)
        if signal == "BREAKDOWN":
            targets = {"target1": support, "target2": round(price*.955, 2), "stop": resistance}
            rr = round((price - support) / (resistance - price), 2)
        elif signal == "BREAKOUT":
            targets = {"target1": resistance, "target2": round(price*1.045, 2), "stop": support}
            rr = round((resistance - price) / (price - support), 2)
        else:
            targets, rr = {}, None
        entry = {"signal": signal, "price": price, "composite_score": score, "rsi": rsi,
                 "rel_vol": rvol, "atr": round(price*.018, 2), "risk_reward": rr,
                 "support": [support], "resistance": [resistance], "targets": targets,
                 "updated": "2026-10-29 16:12", "candle_date": "2026-10-29",
                 "reason": "Illustrative sample, not exchange data."}
        bars = []
        for n in range(42):
            drift = (n-41) * (0.00085 if signal != "BREAKDOWN" else -0.0007)
            wave = (math.sin(n*.45+i)*.009 + math.sin(n*.13+i)*.006)
            last_wave = (math.sin(41*.45+i)*.009 + math.sin(41*.13+i)*.006)
            close = price * (1 + drift + wave - last_wave)
            bars.append({"time": f"Sample {n+1}", "close": round(close, 2)})
        result.append({"symbol": symbol, "sector": sector, "entry": entry,
                       "candles": bars, "source": "fictional demo"})
    return result
