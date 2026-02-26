import os
import alpaca_trade_api as tradeapi
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def close_all_shorts():
    print("--- Closing All Short Positions ---")
    
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    base_url = "https://paper-api.alpaca.markets"
    
    if not api_key or "REPLACE" in api_key:
        print("❌ Error: API Keys not found in .env")
        return

    try:
        api = tradeapi.REST(api_key, secret_key, base_url, api_version='v2')
        positions = api.list_positions()
        
        shorts_found = 0
        for p in positions:
            qty = int(p.qty)
            if qty < 0:
                shorts_found += 1
                cover_qty = abs(qty)
                print(f"📉 Found Short: {p.symbol} ({qty} shares). Buying to cover...")
                
                try:
                    order = api.submit_order(
                        symbol=p.symbol,
                        qty=cover_qty,
                        side='buy',
                        type='market',
                        time_in_force='day'
                    )
                    print(f"   🚀 Cover Order Submitted: {order.id} (Status: {order.status})")
                except Exception as e:
                    print(f"   ❌ Failed to cover {p.symbol}: {e}")
        
        if shorts_found == 0:
            print("✅ No short positions found.")
            
    except Exception as e:
        print(f"❌ API Error: {e}")

if __name__ == "__main__":
    close_all_shorts()
