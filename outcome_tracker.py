"""
Outcome Tracker — Fills in ground-truth price data for past decisions.

Run periodically (e.g., weekly) to backfill 7-day and 14-day returns
for completed BUY trades in trade_history.db.

Usage:
    python outcome_tracker.py
"""
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi
import trade_logger

load_dotenv()


def get_api():
    api_key = os.getenv("ALPACA_API_KEY", "REPLACE_ME")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "REPLACE_ME")
    base_url = "https://paper-api.alpaca.markets"
    
    if "REPLACE" in api_key:
        print("❌ API Keys not configured.")
        return None
    
    try:
        return tradeapi.REST(api_key, secret_key, base_url, api_version='v2')
    except Exception as e:
        print(f"❌ Alpaca connection failed: {e}")
        return None


def fetch_close_price(api, ticker, target_date):
    """Fetches the closing price for a ticker on a specific date."""
    try:
        start = target_date.strftime('%Y-%m-%d')
        end = (target_date + timedelta(days=3)).strftime('%Y-%m-%d')  # Buffer for weekends
        
        bars = api.get_bars(
            ticker,
            tradeapi.TimeFrame.Day,
            start=start,
            end=end,
            limit=3,
            feed='iex'
        ).df
        
        if bars.empty:
            return None
        
        return float(bars['close'].iloc[0])
    except Exception as e:
        print(f"  ⚠️ Could not fetch price for {ticker} on {start}: {e}")
        return None


def track_outcomes():
    print("--- Outcome Tracker ---")
    
    # Initialize DB (safe migration)
    trade_logger.init_db()
    
    api = get_api()
    if not api:
        return
    
    # Get pending decisions (older than 14 days, no outcome yet)
    pending = trade_logger.get_pending_outcomes(days_threshold=14)
    
    if not pending:
        print("ℹ️ No decisions pending outcome tracking.")
        return
    
    print(f"Found {len(pending)} decisions to track.\n")
    
    updated_count = 0
    
    for decision in pending:
        dec_id = decision['id']
        ticker = decision['ticker']
        filled_price = decision['filled_price']
        filled_at_str = decision['filled_at'] or decision['timestamp']
        
        print(f"📊 Tracking: {ticker} (Decision #{dec_id})")
        
        # Parse the filled_at date
        try:
            if 'T' in filled_at_str:
                filled_date = datetime.fromisoformat(filled_at_str.replace('Z', '+00:00').split('+')[0])
            else:
                filled_date = datetime.strptime(filled_at_str[:19], '%Y-%m-%d %H:%M:%S')
        except (ValueError, TypeError):
            print(f"  ⚠️ Could not parse date: {filled_at_str}")
            continue
        
        # Calculate target dates
        date_7d = filled_date + timedelta(days=7)
        date_14d = filled_date + timedelta(days=14)
        
        # Only process if 14 days have passed
        if datetime.now() < date_14d:
            print(f"  ⏳ Only {(datetime.now() - filled_date).days} days passed. Need 14.")
            continue
        
        # Fetch prices
        price_7d = fetch_close_price(api, ticker, date_7d)
        price_14d = fetch_close_price(api, ticker, date_14d)
        
        if price_14d is None:
            print(f"  ⚠️ Could not get 14-day price for {ticker}. Skipping.")
            continue
        
        # Calculate outcome
        if filled_price and filled_price > 0:
            outcome_pnl_pct = ((price_14d - filled_price) / filled_price) * 100
        else:
            outcome_pnl_pct = 0.0
        
        # Update DB
        trade_logger.update_outcome(dec_id, price_7d, price_14d, outcome_pnl_pct)
        
        # Display result
        direction = "📈" if outcome_pnl_pct > 0 else "📉"
        p7_display = f"${price_7d:.2f}" if price_7d else "N/A"
        print(f"  {direction} Filled: ${filled_price:.2f} → 7d: {p7_display} → 14d: ${price_14d:.2f}")
        print(f"     Return: {outcome_pnl_pct:+.2f}%")
        
        updated_count += 1
    
    print(f"\n✅ Updated {updated_count}/{len(pending)} outcomes.")


if __name__ == "__main__":
    track_outcomes()
