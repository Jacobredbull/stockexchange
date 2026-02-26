import alpaca_trade_api as tradeapi
import os

# --- CONFIGURATION ---
# REPLACE THESE WITH YOUR ACTUAL KEYS
API_KEY = "PK5N2KC7WPQAMEN7UFQLRBKTCC"
SECRET_KEY = "4ZNycwbcyE82roFnyEj7hWeVj9AQMRrYNEgW91EAHfPm"
BASE_URL = "https://paper-api.alpaca.markets"

# Allow environment variables to override placeholders (optional convenience)
API_KEY = os.getenv("ALPACA_API_KEY", API_KEY)
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", SECRET_KEY)

def test_connection():
    print("--- Alpaca Connection Test ---")
    print(f"Target URL: {BASE_URL}")
    
    # Check if keys are still placeholders
    if "REPLACE" in API_KEY or "REPLACE" in SECRET_KEY:
        print("ERROR: Please update API_KEY and SECRET_KEY in the script or environment variables.")
        return

    try:
        # Initialize the REST API
        api = tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL, api_version='v2')
        
        # Request Account Information
        print("Connecting to server...")
        account = api.get_account()
        
        # Success Output
        print("\n✅ SUCCESS! Connection Established.")
        print(f"Status: {account.status}")
        print(f"Cash Balance: ${account.cash}")
        print(f"Portfolio Value: ${account.portfolio_value}")
        print(f"Buying Power: ${account.buying_power}")

    except Exception as e:
        # Error Output
        print("\n❌ FAILED to connect.")
        print(f"Error Type: {type(e).__name__}")
        print(f"Error Message: {e}")
        
        # Try to print more detail if it's an APIError
        if hasattr(e, '_response'):
            print(f"Server Response: {e._response.text}")

if __name__ == "__main__":
    test_connection()
