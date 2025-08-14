#!/usr/bin/env python3
import time
import threading
import logging
import os
import pytz
from datetime import datetime
import requests
import pandas as pd
import yfinance as yf
from flask import Flask

# -------------------------
# CONFIGURATION
# -------------------------
TELEGRAM_TOKEN = "8021318198:AAExTUdHDFZS5fKSsNABOmWaV8DLffZxNFo"
TELEGRAM_CHAT_ID = 5930379340
TIMEFRAME = "15m"
SYMBOL = "^NSEI"  # NIFTY 50 Index
IST = pytz.timezone("Asia/Kolkata")

# -------------------------
# Logging
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# -------------------------
# Telegram Helper
# -------------------------
def send_telegram_message(text):
    try:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        r = requests.post(TELEGRAM_URL, data=payload, timeout=10)
        r.raise_for_status()
        logging.info("Telegram message sent.")
    except Exception as e:
        logging.exception("Failed to send Telegram message: %s", e)

# -------------------------
# Candle Features
# -------------------------
def add_candle_features(df):
    df = df.copy()
    df.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df.columns]

    df['body'] = (df['close'] - df['open']).abs()
    df['range'] = df['high'] - df['low']
    df['range'] = df['range'].replace(0, 1e-9)

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
    last = df.iloc[-1]      # latest fully closed candle
    prev = df.iloc[-2]      # one before that

    if last['lower_wick_ratio'] >= 0.6 and last['upper_wick_ratio'] <= 0.2 and last['body_ratio'] <= 0.35:
        patterns.append("Hammer (bullish)")
    if last['upper_wick_ratio'] >= 0.6 and last['lower_wick_ratio'] <= 0.2 and last['body_ratio'] <= 0.35:
        patterns.append("Inverted Hammer (bearish)")
    if last['body_ratio'] <= 0.25 and last['upper_wick'] > 0 and last['lower_wick'] > 0:
        patterns.append("Spinning Top (indecision)")
    if last['body_ratio'] <= 0.05:
        patterns.append("Doji (indecision)")

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
    bullish = ["Hammer (bullish)", "Bullish Engulfing"]
    bearish = ["Inverted Hammer (bearish)", "Bearish Engulfing"]

    if any(p in patterns for p in bullish):
        return "BUY"
    elif any(p in patterns for p in bearish):
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
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
    return df

# -------------------------
# Wait for Next Candle Close
# -------------------------
def wait_until_next_candle(interval_minutes):
    now = datetime.now(IST)
    minute = (now.minute // interval_minutes + 1) * interval_minutes
    if minute >= 60:
        next_candle_time = now.replace(hour=(now.hour + 1) % 24, minute=0, second=5, microsecond=0)
    else:
        next_candle_time = now.replace(minute=minute, second=5, microsecond=0)
    sleep_time = (next_candle_time - now).total_seconds()
    logging.info(f"Sleeping {sleep_time:.1f}s until next candle close at {next_candle_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    time.sleep(sleep_time)

# -------------------------
# Bot Loop
# -------------------------
def bot_loop():
    logging.info("Starting NIFTY 15-min pattern detector")
    last_alerted_idx = None

    while True:
        wait_until_next_candle(15)

        try:
            df = fetch_recent_candles()
            if df.empty or len(df) < 3:  # need at least 3 for engulfing
                continue

            df = add_candle_features(df)

            # Use second-last candle to ensure it's fully closed
            last_idx = df.index[-2]
            if last_alerted_idx == last_idx:
                continue

            patterns = detect_patterns(df.iloc[:-1])  # exclude last forming candle
            signal = determine_signal(patterns)

            if patterns:
                last_candle = df.iloc[-2]
                time_str = last_idx.tz_convert(IST).strftime("%Y-%m-%d %H:%M:%S %Z")
                perc_change = (last_candle['close'] - last_candle['open']) / last_candle['open'] * 100
                msg = (
                    f"📊 NIFTY Pattern: {', '.join(patterns)}\n"
                    f"Signal: {signal}\n"
                    f"Time: {time_str}\n"
                    f"Open: {last_candle['open']:.2f}  High: {last_candle['high']:.2f}  "
                    f"Low: {last_candle['low']:.2f}  Close: {last_candle['close']:.2f}\n"
                    f"Change: {perc_change:.3f}%"
                )
                send_telegram_message(msg)
                last_alerted_idx = last_idx

        except Exception as exc:
            logging.exception("Error in bot loop: %s", exc)

# -------------------------
# Flask App
# -------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running and monitoring NIFTY."

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
