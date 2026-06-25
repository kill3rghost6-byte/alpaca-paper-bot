import time
import requests
import pandas as pd
from datetime import datetime, timedelta
import pytz

# ==========================================
# ⚙️ پیکربندی ربات (Configuration)
# ==========================================
ALPACA_API_KEY = "PKONIITAGGZFAJYSSLO3OPTVAT"
ALPACA_SECRET_KEY = "JBxeu1bKjGRXK3LGKffJrpyPfrogkkNfK9y44cDg9YWY"
ALPACA_BASE_URL = "https://paper-api.alpaca.markets/v2"

POLYGON_API_KEY = "VGqkhp8QU3aQiQbMOjJ4BRgAVLOnOMnp"

# 5 سهم برتر بر اساس بک‌تست تاریخی
SYMBOLS = ['STX', 'WDC', 'MU', 'HUT', 'AAOI']

POSITION_SIZE_PCT = 0.20  # ورود با 20 درصد سرمایه برای هر سهم
TAKE_PROFIT_PCT = 0.20    # حد سود 20 درصدی

ALPACA_HEADERS = {
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY
}

import yfinance as yf

# ==========================================
# 📊 توابع دریافت داده از Yahoo Finance (دیتای لایو بدون تاخیر)
# ==========================================
def get_daily_sma200(symbol):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1y", interval="1d")
        if len(df) < 200:
            return None
        sma200 = df['Close'].rolling(window=200).mean().iloc[-1]
        return sma200
    except:
        return None

def get_intraday_data(symbol):
    try:
        ticker = yf.Ticker(symbol)
        # استفاده از prepost=True بسیار حیاتی است تا دیتای قبل از بازار (Pre-market) را دریافت کنیم
        df = ticker.history(period="5d", interval="5m", prepost=True)
        if df.empty:
            return None, None, None
            
        df.index = df.index.tz_convert('America/New_York')
        unique_days = df.index.normalize().unique()
        if len(unique_days) < 2:
            return None, None, None
            
        today_date = unique_days[-1]
        prev_date = unique_days[-2]
        
        prev_day_data = df.loc[str(prev_date.date())]
        prev_reg = prev_day_data.between_time('09:30', '15:59')
        prev_hod = prev_reg['High'].max() if not prev_reg.empty else None
        
        today_data = df.loc[str(today_date.date())]
        today_pm = today_data.between_time('04:00', '09:29')
        pmh = today_pm['High'].max() if not today_pm.empty else None
        
        current_price = df['Close'].iloc[-1]
        
        return current_price, pmh, prev_hod
    except:
        return None, None, None

# ==========================================
# 💰 توابع معاملاتی Alpaca
# ==========================================
def get_account_info():
    resp = requests.get(f"{ALPACA_BASE_URL}/account", headers=ALPACA_HEADERS)
    return resp.json()

def get_open_positions():
    resp = requests.get(f"{ALPACA_BASE_URL}/positions", headers=ALPACA_HEADERS)
    return [p['symbol'] for p in resp.json()] if resp.status_code == 200 else []

def has_traded_today(symbol):
    """ بررسی می‌کند که آیا امروز برای این سهم اردر خریدی ثبت شده یا نه تا از خرید مجدد در یک روز جلوگیری شود """
    ny_time = datetime.now(pytz.timezone('America/New_York'))
    today_str = ny_time.strftime('%Y-%m-%d')
    resp = requests.get(f"{ALPACA_BASE_URL}/orders?status=all&symbols={symbol}&after={today_str}T00:00:00Z", headers=ALPACA_HEADERS)
    if resp.status_code == 200:
        orders = resp.json()
        for o in orders:
            if o['side'] == 'buy':
                return True
    return False

def place_buy_order_with_tp(symbol, qty, current_price):
    tp_price = round(current_price * (1 + TAKE_PROFIT_PCT), 2)
    
    order_data = {
        "symbol": symbol,
        "qty": str(qty),
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "order_class": "oto",
        "take_profit": {
            "limit_price": str(tp_price)
        }
    }
    
    resp = requests.post(f"{ALPACA_BASE_URL}/orders", headers=ALPACA_HEADERS, json=order_data)
    if resp.status_code in [200, 201]:
        print(f"✅ [ORDER SUCCESS] Bought {qty} shares of {symbol} at ~${current_price}. TP set at ${tp_price}.")
    else:
        print(f"❌ [ORDER FAILED] {symbol}: {resp.text}")

# ==========================================
# 🚀 موتور اصلی ربات (برای GitHub Actions)
# ==========================================
def run_bot():
    print("🚀 Starting Trend Join Gapper Live Paper Trading Bot (Cron Mode)...")
    
    ny_time = datetime.now(pytz.timezone('America/New_York'))
    print(f"[{ny_time.strftime('%Y-%m-%d %H:%M:%S')} ET] Checking market conditions...")
    
    if not (9 <= ny_time.hour <= 16):
        print("💤 Market is closed. Exiting.")
        return
        
    if ny_time.hour == 9 and ny_time.minute < 30:
        print("💤 Pre-market. Exiting.")
        return

    account = get_account_info()
    if 'equity' not in account:
        print("❌ Failed to fetch Alpaca account. Exiting.")
        return
        
    equity = float(account['equity'])
    open_positions = get_open_positions()
    
    for symbol in SYMBOLS:
        if symbol in open_positions:
            print(f"  ⏭️ {symbol}: Already in position. Waiting for Take Profit.")
            continue
            
        if has_traded_today(symbol):
            print(f"  🛑 {symbol}: Already traded today. Waiting for tomorrow to prevent over-trading.")
            continue
            
        sma200 = get_daily_sma200(symbol)
        if not sma200:
            continue
            
        current_price, pmh, prev_hod = get_intraday_data(symbol)
        
        valid_highs = [h for h in (pmh, prev_hod) if h is not None]
        
        if current_price and valid_highs:
            target_breakout = max(valid_highs)
            
            print(f"  📊 {symbol} | Price: ${current_price:.2f} | Breakout Level: ${target_breakout:.2f} | SMA200: ${sma200:.2f}")
            
            if current_price > target_breakout and current_price > sma200:
                print(f"  🔥 SIGNAL TRIGGERED FOR {symbol}! Breakout detected.")
                
                position_value = equity * POSITION_SIZE_PCT
                qty = int(position_value / current_price)
                
                if qty > 0:
                    place_buy_order_with_tp(symbol, qty, current_price)
                else:
                    print(f"  ⚠️ Not enough equity to buy even 1 share of {symbol}.")
        
        time.sleep(25) 

    print("✅ Scan complete. Exiting script.")

if __name__ == "__main__":
    run_bot()
