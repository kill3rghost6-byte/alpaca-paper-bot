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
TP1_PCT = 0.10            # تارگت اول 10 درصد (بستن 75% پوزیشن)
TP2_PCT = 0.20            # تارگت دوم 20 درصد
SL_PCT = 0.10             # حد ضرر اولیه 10 درصد

ALPACA_HEADERS = {
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY
}

import yfinance as yf
import os

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8731287031:AAF3-J-V6DEJ2iVLWinm05AMAfvtM3plsmE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "5708853617")

def send_telegram(message, max_retries=3):
    print(f"Telegram Log: {message}")
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        for attempt in range(max_retries):
            try:
                requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
                return
            except Exception as e:
                print(f"Telegram Error (Attempt {attempt+1}/{max_retries}): {e}")
                time.sleep(3)

# ==========================================
# 📊 توابع دریافت داده از Yahoo Finance (دیتای لایو بدون تاخیر)
# ==========================================
def get_intraday_data(symbol):
    try:
        ticker = yf.Ticker(symbol)
        # دریافت 5 روز اخیر تایم فریم 5 دقیقه‌ای (شامل Pre-market)
        df = ticker.history(period="5d", interval="5m", prepost=True)
        if df.empty or len(df) < 200:
            return None, None, None, None
            
        df.index = df.index.tz_convert('America/New_York')
        
        # محاسبه SMA 200 در تایم فریم 5 دقیقه‌ای (دقیقاً مشابه بک‌تست تریدینگ ویو)
        sma200 = df['Close'].rolling(window=200).mean().iloc[-1]
        
        unique_days = df.index.normalize().unique()
        if len(unique_days) < 2:
            return None, None, None, None
            
        today_date = unique_days[-1]
        prev_date = unique_days[-2]
        
        prev_day_data = df.loc[str(prev_date.date())]
        prev_reg = prev_day_data.between_time('09:30', '15:59')
        prev_hod = prev_reg['High'].max() if not prev_reg.empty else None
        
        today_data = df.loc[str(today_date.date())]
        today_pm = today_data.between_time('04:00', '09:29')
        pmh = today_pm['High'].max() if not today_pm.empty else None
        
        current_price = df['Close'].iloc[-1]
        
        return current_price, pmh, prev_hod, sma200
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")
        return None, None, None, None

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

def place_buy_order(symbol, qty, current_price):
    order_data = {
        "symbol": symbol,
        "qty": str(qty),
        "side": "buy",
        "type": "market",
        "time_in_force": "day"
    }
    
    resp = requests.post(f"{ALPACA_BASE_URL}/orders", headers=ALPACA_HEADERS, json=order_data)
    if resp.status_code in [200, 201]:
        msg = f"✅ **ORDER SUCCESS** ✅\nBought {qty} shares of {symbol} at ~${current_price}.\nTP1: +{TP1_PCT*100}% | TP2: +{TP2_PCT*100}%"
        print(msg)
        send_telegram(msg)
        
        # Save state
        state_file = 'alpaca_state.json'
        import json
        state = {}
        if os.path.exists(state_file):
            with open(state_file, 'r') as f:
                state = json.load(f)
        state[symbol] = {
            'tp1_done': False,
            'sl': current_price * (1 - SL_PCT),
            'entry': current_price
        }
        with open(state_file, 'w') as f:
            json.dump(state, f)
            
    else:
        msg = f"❌ **ORDER FAILED** ❌\n{symbol}: {resp.text}"
        print(msg)
        send_telegram(msg)

def place_sell_order(symbol, qty, reason):
    if qty <= 0: return True
    order_data = {
        "symbol": symbol,
        "qty": str(qty),
        "side": "sell",
        "type": "market",
        "time_in_force": "day"
    }
    resp = requests.post(f"{ALPACA_BASE_URL}/orders", headers=ALPACA_HEADERS, json=order_data)
    if resp.status_code in [200, 201]:
        msg = f"💰 **SELL SUCCESS ({reason})** 💰\nSold {qty} shares of {symbol}."
        print(msg)
        send_telegram(msg)
        return True
    else:
        msg = f"❌ **SELL FAILED ({reason})** ❌\n{symbol}: {resp.text}"
        print(msg)
        send_telegram(msg)
        return False

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
    
    # Manage Open Positions
    state_file = 'alpaca_state.json'
    import json
    state = {}
    if os.path.exists(state_file):
        with open(state_file, 'r') as f:
            state = json.load(f)
            
    pos_resp = requests.get(f"{ALPACA_BASE_URL}/positions", headers=ALPACA_HEADERS)
    if pos_resp.status_code == 200:
        positions = pos_resp.json()
        for p in positions:
            sym = p['symbol']
            qty = int(p['qty'])
            current_price = float(p['current_price'])
            avg_entry = float(p['avg_entry_price'])
            
            if sym not in state:
                state[sym] = {'tp1_done': False, 'sl': avg_entry * (1 - SL_PCT), 'entry': avg_entry}
                
            sym_state = state[sym]
            tp1_price = sym_state['entry'] * (1 + TP1_PCT)
            tp2_price = sym_state['entry'] * (1 + TP2_PCT)
            sl_price = sym_state['sl']
            
            # Check Stop Loss
            if current_price <= sl_price:
                place_sell_order(sym, qty, "STOP LOSS / BREAKEVEN")
                del state[sym]
                continue
                
            # Check TP2
            if current_price >= tp2_price:
                place_sell_order(sym, qty, "TAKE PROFIT 2")
                del state[sym]
                continue
                
            # Check TP1
            if current_price >= tp1_price and not sym_state['tp1_done']:
                sell_qty = int(qty * 0.75)
                success = place_sell_order(sym, sell_qty, "TAKE PROFIT 1 (75%)")
                if success:
                    sym_state['tp1_done'] = True
                    sym_state['sl'] = sym_state['entry'] # Move SL to Breakeven
                    send_telegram(f"🛡️ **{sym} SL Moved to Breakeven**")

    with open(state_file, 'w') as f:
        json.dump(state, f)
    
    for symbol in SYMBOLS:
        if symbol in open_positions:
            continue
        if has_traded_today(symbol):
            print(f"  🛑 {symbol}: Already traded today. Waiting for tomorrow to prevent over-trading.")
            continue
            
        current_price, pmh, prev_hod, sma200 = get_intraday_data(symbol)
        if not sma200:
            print(f"  ⚠️ Not enough data for 5m SMA200 on {symbol}.")
            continue
        
        valid_highs = [h for h in (pmh, prev_hod) if h is not None]
        
        if current_price and valid_highs:
            target_breakout = max(valid_highs)
            
            print(f"  📊 {symbol} | Price: ${current_price:.2f} | Breakout Level: ${target_breakout:.2f} | 5m SMA200: ${sma200:.2f}")
            
            if current_price > target_breakout and current_price > sma200:
                print(f"  🔥 SIGNAL TRIGGERED FOR {symbol}! Breakout detected.")
                
                position_value = equity * POSITION_SIZE_PCT
                qty = int(position_value / current_price)
                
                if qty > 0:
                    place_buy_order(symbol, qty, current_price)
                else:
                    print(f"  ⚠️ Not enough equity to buy even 1 share of {symbol}.")
        
        # یک مکث 1 ثانیه‌ای برای جلوگیری از اسپم شدن API یاهو
        time.sleep(1) 

    print("✅ Scan complete. Exiting script.")

def run_continuous():
    send_telegram("🚀 **Alpaca Stock Bot Started in Continuous Mode** 🚀")
    start_time = time.time()
    max_duration = 5.5 * 3600 # 5.5 hours max per GitHub Action
    
    last_heartbeat_time = 0
    
    while time.time() - start_time < max_duration:
        try:
            ny_time = datetime.now(pytz.timezone('America/New_York'))
            if 9 <= ny_time.hour <= 16:
                run_bot()
            else:
                print("💤 Market is closed. Sleeping...")
            
            # 15-Minute Heartbeat
            current_time = time.time()
            if current_time - last_heartbeat_time >= 15 * 60:
                account = get_account_info()
                if 'equity' in account:
                    bal = float(account['equity'])
                    msg = f"⏳ **Alpaca Status Update (15m)**\n💰 Equity: ${bal:,.2f}"
                    
                    # Get position details directly from Alpaca
                    pos_resp = requests.get(f"{ALPACA_BASE_URL}/positions", headers=ALPACA_HEADERS)
                    if pos_resp.status_code == 200:
                        positions = pos_resp.json()
                        if positions:
                            msg += "\n📂 Open Positions:"
                            for p in positions:
                                sym = p['symbol']
                                unrealized_pl = float(p['unrealized_pl'])
                                pl_pct = float(p['unrealized_plpc']) * 100
                                msg += f"\n  ▫️ {sym}: ${unrealized_pl:.2f} ({pl_pct:.2f}%)"
                        else:
                            msg += "\n📂 No active positions."
                            
                    send_telegram(msg)
                last_heartbeat_time = current_time
                
        except Exception as e:
            print("Error in run_bot:", e)
            
        now = datetime.now(pytz.timezone('America/New_York'))
        # Find next minute mark that is a multiple of 5 + 1 min padding (e.g., 01, 06, 11)
        minute = ((now.minute // 5) + 1) * 5 + 1
        
        if minute >= 60:
            next_run = now.replace(minute=minute-60, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_run = now.replace(minute=minute, second=0, microsecond=0)
            
        sleep_seconds = (next_run - now).total_seconds()
        if sleep_seconds > 0:
            print(f"Sleeping for {sleep_seconds:.1f} seconds until {next_run.strftime('%H:%M:%S')} NY Time...")
            time.sleep(sleep_seconds)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--continuous':
        run_continuous()
    else:
        run_bot()
