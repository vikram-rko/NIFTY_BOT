#!/usr/bin/env python3
import time
import logging
from datetime import timezone
import requests
import pandas as pd
import yfinance as yf

# -------------------------
# CONFIGURATION
# -------------------------
TELEGRAM_TOKEN = "8021318198:AAExTUdHDFZS5fKSsNABOmWaV8DLffZxNFo"
TELEGRAM_CHAT_ID = 5930379340
TIMEFRAME = "15m"
POLL_INTERVAL = 910  # slightly more than 15 min to ensure candle close
SYMBOL = "^NSEI"

# -------------------------
# Logging
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

def send_telegram_message(text):
    try:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        r = requests.post(TELEGRAM_URL, data=payload, timeout=10)
        r.raise_for_status()
        logging.info("Telegram message sent.")
    except Exception as e:
        logging.exception("Failed to send Telegram message: %s", e)

# -------------------------
# Candlestick Features (Vectorized, robust)
# -------------------------
def add_candle_features(df):
    df = df.copy()

    # Ensure columns are single-level strings (handles yfinance multi-index)
    df.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df.columns]

    df['body'] = (df['close'] - df['open']).abs()
    df['range'] = df['high'] - df['low']
    df['range'] = df['range'].replace(0, 1e-9)  # avoid division by zero

    # compute upper and lower wick safely as Series
    max_oc = df[['open', 'close']].max(axis=1).astype(float)
    min_oc = df[['open', 'close']].min(axis=1).astype(float)

    df['upper_wick'] = (df['high'].astype(float) - max_oc)
    df['lower_wick'] = (min_oc - df['low'].astype(float))

    df['body_ratio'] = df['body'] / df['range']
    df['upper_wick_ratio'] = df['upper_wick'] / df['range']
    df['lower_wick_ratio'] = df['lower_wick'] / df['range']

    return df

# -------------------------
# Pattern Detection
# -------------------------
def detect_patterns(df):
    patterns = []

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last

    # Single-candle patterns
    if last['lower_wick_ratio'] >= 0.6 and last['upper_wick_ratio'] <= 0.2 and last['body_ratio'] <= 0.35:
        patterns.append("Hammer (bullish)")
    if last['upper_wick_ratio'] >= 0.6 and last['lower_wick_ratio'] <= 0.2 and last['body_ratio'] <= 0.35:
        patterns.append("Inverted Hammer (bearish)")
    if last['body_ratio'] <= 0.25 and last['upper_wick'] > 0 and last['lower_wick'] > 0:
        patterns.append("Spinning Top (indecision)")
    if last['body_ratio'] <= 0.05:
        patterns.append("Doji (indecision)")

    # Multi-candle patterns
    if prev['close'] < prev['open'] and last['close'] > last['open']:
        if last['close'] > prev['open'] and last['open'] < prev['close']:
            patterns.append("Bullish Engulfing")
    if prev['close'] > prev['open'] and last['close'] < last['open']:
        if last['open'] > prev['close'] and last['close'] < prev['open']:
            patterns.append("Bearish Engulfing")

    return patterns

# -------------------------
# Determine Signal
# -------------------------
def determine_signal(patterns):
    bullish_patterns = ["Hammer (bullish)", "Bullish Engulfing"]
    bearish_patterns = ["Inverted Hammer (bearish)", "Bearish Engulfing"]

    if any(p in patterns for p in bullish_patterns):
        return "BUY"
    elif any(p in patterns for p in bearish_patterns):
        return "SELL"
    else:
        return "NEUTRAL"

# -------------------------
# Fetch Candles
# -------------------------
def fetch_recent_candles(symbol=SYMBOL, period="60d", interval=TIMEFRAME):
    df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=False)
    if df.empty:
        logging.warning("yfinance returned empty dataframe for %s", symbol)
        return df
    df = df.rename(columns={"Open":"open","High":"high","Low":"low","Close":"close","Volume":"volume"})
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
    return df

# -------------------------
# Main Loop
# -------------------------
def main_loop():
    logging.info("Starting optimized NIFTY 15-min pattern detector")
    last_alerted_idx = None

    while True:
        try:
            df = fetch_recent_candles()
            if df.empty or len(df) < 2:
                logging.info("Not enough data yet. Sleeping %s seconds.", POLL_INTERVAL)
                time.sleep(POLL_INTERVAL)
                continue

            df = add_candle_features(df)
            last_idx = df.index[-1]

            if last_alerted_idx == last_idx:
                time.sleep(POLL_INTERVAL)
                continue

            patterns = detect_patterns(df)
            signal = determine_signal(patterns)

            if patterns:
                last_candle = df.iloc[-1]
                time_str = last_idx.strftime("%Y-%m-%d %H:%M:%S UTC")
                perc_change = (last_candle['close'] - last_candle['open']) / last_candle['open'] * 100
                msg = f"📈 NIFTY Pattern detected: {', '.join(patterns)}\n" \
                      f"Signal: {signal}\n" \
                      f"Time (candle close): {time_str}\n" \
                      f"Open: {last_candle['open']:.2f}  High: {last_candle['high']:.2f}  " \
                      f"Low: {last_candle['low']:.2f}  Close: {last_candle['close']:.2f}\n" \
                      f"Change: {perc_change:.3f}%"
                send_telegram_message(msg)
                last_alerted_idx = last_idx

        except Exception as exc:
            logging.exception("Error in main loop: %s", exc)

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main_loop()
