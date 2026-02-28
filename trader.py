import json
import os
import time
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
import config
import trade_logger

def execute_trades():
    print("--- Starting Automated Trader ---")
    
    # 1. Setup Alpaca Connection
    api_key = os.getenv("ALPACA_API_KEY", "REPLACE_WITH_YOUR_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "REPLACE_WITH_YOUR_SECRET")

    if "REPLACE" in api_key:
        print("❌ Error: API Keys not set in environment or config.py.")
        return

    try:
        client = TradingClient(api_key, secret_key, paper=True)
        account = client.get_account()
        print(f"✅ Connected to Alpaca. Account Status: {account.status}")
        print(f"   Buying Power: ${account.buying_power}")
    except Exception as e:
        print(f"❌ Connection Failed: {e}")
        return

    # Initialize DB (safe migration)
    trade_logger.init_db()

    # 2. Load Execution Plan
    plan_file = 'execution_plan.json'
    try:
        with open(plan_file, 'r') as f:
            orders = json.load(f)
    except FileNotFoundError:
        print(f"⚠️ No {plan_file} found. Run logic_engine.py first.")
        return
    except json.JSONDecodeError:
        print(f"❌ Error decoding {plan_file}.")
        return

    if not orders:
        print("ℹ️ Execution plan is empty. No trades to make.")
        return

    print(f"\nFound {len(orders)} orders in plan. Processing...")

    # --- Read Safe Hold state ---
    safe_hold_mode = False
    try:
        with open('sentiment_data.json', 'r') as f:
            sentiment_data = json.load(f)
            if sentiment_data.get('global_env_bias', 1.0) == 0.0:
                safe_hold_mode = True
    except Exception:
        pass
        
    if safe_hold_mode:
        print("🚨 SAFE HOLD MODE ACTIVE: trader.py will reject ALL BUY orders.")

    # 3. Execute Orders
    for order in orders:
        ticker = order.get('ticker')
        action = order.get('action') # 'buy' or 'sell'
        qty = order.get('quantity')
        reason = order.get('reason', 'N/A')
        decision_id = order.get('decision_id')  # From logic_engine

        if not (ticker and action and qty > 0):
            print(f"⚠️ Skipping invalid order: {order}")
            continue
            
        # --- SAFE HOLD MODE Enforcement ---
        if action.lower() == 'buy':
            if safe_hold_mode or 'BRAIN_OFFLINE_PROTECTION' in reason:
                print(f"   🚨 SAFE HOLD MODE ACTIVE: Rejecting BUY order for {ticker}.")
                if decision_id:
                    trade_logger.update_execution(decision_id, None, 'rejected_safe_hold_mode')
                continue

        # Detect fractional order
        is_fractional = isinstance(qty, float) and qty < 1.0 and qty != int(qty)
        qty_label = f"{qty} fractional shares" if is_fractional else f"{int(qty) if isinstance(qty, (int, float)) and qty == int(qty) else qty} shares"
        print(f"\n📦 Preparing to {action.upper()} {qty_label} of {ticker}...")
        print(f"   Reason: {reason}")
        
        # --- Anti-Short-Selling Check ---
        if action == 'sell':
            try:
                position = client.get_open_position(ticker)
                current_qty = float(position.qty)
                
                if current_qty <= 0:
                    print(f"   ⚠️ Skipping SELL: No long position for {ticker} (Qty: {current_qty}).") 
                    if decision_id:
                        trade_logger.update_execution(decision_id, None, 'skipped_no_position')
                    continue
                
                if qty > current_qty:
                    print(f"   ⚠️ Adjusted SELL qty from {qty} to {current_qty} (Capped at Max Available).")
                    qty = current_qty

            except Exception as e:
                print(f"   ⚠️ Skipping SELL: No existing position for {ticker} found in Alpaca.")
                if decision_id:
                    trade_logger.update_execution(decision_id, None, 'skipped_no_position')
                continue

        # --- Submit Order ---
        try:
            order_type = order.get('order_type', 'market')
            limit_price = order.get('limit_price')
            side = OrderSide.BUY if action.lower() == 'buy' else OrderSide.SELL
            
            # Alpaca: fractional shares MUST use market orders
            if is_fractional and order_type != 'market':
                print(f"   ⚠️ Fractional qty detected — forcing market order (Alpaca requirement).")
                order_type = 'market'
            
            if order_type == 'limit' and limit_price:
                # PATCH B: Dynamic Limit Price Buffer
                # SELL: 0.5% below market → ensures fill in falling market
                # BUY: 0.5% above market → ensures fill in rising market
                if action == 'sell':
                    limit_price = round(float(limit_price) * 0.995, 2)
                else:
                    limit_price = round(float(limit_price) * 1.005, 2)
                print(f"   🔒 Limit Order: ${limit_price:.2f} ({'↓0.5% buffer' if action == 'sell' else '↑0.5% buffer'})")
                order_request = LimitOrderRequest(
                    symbol=ticker,
                    qty=float(qty),
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=limit_price
                )
            else:
                print(f"   📊 Market Order: {qty_label}")
                order_request = MarketOrderRequest(
                    symbol=ticker,
                    qty=float(qty),
                    side=side,
                    time_in_force=TimeInForce.DAY
                )

            submitted_order = client.submit_order(order_request)
            alpaca_order_id = str(submitted_order.id)
            print(f"   🚀 Order Submitted! ID: {alpaca_order_id}")
            print(f"      Status: {submitted_order.status}")
            
            # --- Log Submission ---
            if decision_id:
                trade_logger.update_execution(decision_id, alpaca_order_id, 'submitted')
            
            # --- 5-Second Polling for Fill ---
            print(f"   ⏳ Waiting 5s for fill confirmation...")
            time.sleep(5)
            
            try:
                updated_order = client.get_order_by_id(alpaca_order_id)
                fill_status = updated_order.status
                print(f"   📋 Order Status: {fill_status}")
                
                if fill_status == 'filled':
                    filled_price = float(updated_order.filled_avg_price) if updated_order.filled_avg_price else None
                    filled_qty = float(updated_order.filled_qty) if updated_order.filled_qty else None
                    filled_at = str(updated_order.filled_at) if updated_order.filled_at else None
                    
                    print(f"   ✅ FILLED: {filled_qty} shares @ ${filled_price:.2f}")
                    
                    if decision_id:
                        trade_logger.update_execution(
                            decision_id, alpaca_order_id, 'filled',
                            filled_price, filled_qty, filled_at
                        )
                elif fill_status in ('partially_filled',):
                    filled_price = float(updated_order.filled_avg_price) if updated_order.filled_avg_price else None
                    filled_qty = float(updated_order.filled_qty) if updated_order.filled_qty else None
                    print(f"   ⚠️ PARTIAL FILL: {filled_qty} shares @ ${filled_price}")
                    
                    if decision_id:
                        trade_logger.update_execution(
                            decision_id, alpaca_order_id, 'partial_fill',
                            filled_price, filled_qty
                        )
                elif fill_status in ('cancelled', 'expired', 'rejected'):
                    print(f"   ❌ Order {fill_status.upper()}")
                    if decision_id:
                        trade_logger.update_execution(decision_id, alpaca_order_id, fill_status)
                else:
                    # Still pending (accepted, pending_new, etc.)
                    print(f"   ⏳ Order still pending: {fill_status}")
                    if decision_id:
                        trade_logger.update_execution(decision_id, alpaca_order_id, fill_status)
                        
            except Exception as poll_err:
                print(f"   ⚠️ Could not poll order status: {poll_err}")
            
        except Exception as e:
            print(f"   ❌ Order Failed: {e}")
            if decision_id:
                trade_logger.update_execution(decision_id, None, 'rejected')

        time.sleep(1) # Rate limit politeness

    print("\n--- Trading Session Complete ---")
    trade_logger.backup_db()

if __name__ == "__main__":
    execute_trades()
