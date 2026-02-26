import json
import os
import math
import pandas as pd
import numpy as np
import alpaca_trade_api as tradeapi
import config
import trade_logger  # [NEW] Import Logger
from datetime import datetime, timedelta

class TradingLogic:
    def __init__(self, budget, risk_per_trade_percent, stop_loss_percent, max_concentration_percent):
        self.budget = budget
        self.risk_per_trade_percent = risk_per_trade_percent
        self.stop_loss_percent = stop_loss_percent  # Kept as fallback
        self.max_concentration_percent = max_concentration_percent
        
        # ATR-based stop config
        self.atr_multiplier = 2.0
        self.atr_period = 14
        
        # Trailing Take-Profit config
        self.trailing_activation_pct = 0.10   # Activate after 10% unrealized gain
        self.trailing_drop_pct = 0.03         # Trigger sell on 3% drop from peak
        
        # Alpaca Setup
        self.api_key = os.getenv("ALPACA_API_KEY", "REPLACE_ME")
        self.secret_key = os.getenv("ALPACA_SECRET_KEY", "REPLACE_ME")
        self.base_url = "https://paper-api.alpaca.markets"
        
        self.api = None
        if "REPLACE" not in self.api_key:
             try:
                 self.api = tradeapi.REST(self.api_key, self.secret_key, self.base_url, api_version='v2')
             except Exception as e:
                 print(f"Warning: Alpaca API failed to init: {e}")
        
        # Initialize Database
        trade_logger.init_db()
        
        # Ticker validation cache
        self._ticker_cache = {}

    def validate_ticker(self, ticker):
        """
        Validates that a ticker exists and is tradable on Alpaca.
        Returns True if valid, False otherwise. Results are cached.
        """
        if ticker in self._ticker_cache:
            return self._ticker_cache[ticker]
        
        if not self.api:
            self._ticker_cache[ticker] = True  # Can't validate without API, assume OK
            return True
        
        try:
            asset = self.api.get_asset(ticker)
            is_valid = asset.tradable and asset.status == 'active'
            self._ticker_cache[ticker] = is_valid
            if not is_valid:
                print(f"  ❌ {ticker}: Asset exists but not tradable (status: {asset.status})")
            return is_valid
        except Exception:
            print(f"  ❌ {ticker}: Asset not found on Alpaca — skipping.")
            self._ticker_cache[ticker] = False
            return False

    def fetch_price(self, ticker):
        """
        Fetches current price from Alpaca. Falls back to manual input if API fails.
        """
        price = None
        
        # 1. Try Alpaca
        if self.api:
            try:
                # Get last trade or quote
                trade = self.api.get_latest_trade(ticker)
                price = float(trade.price)
                print(f"  [API] Fetched {ticker} price: ${price:.2f}")
            except Exception as e:
                # print(f"  [API-Error] Could not fetch {ticker}: {e}")
                pass
        
        # 2. Manual Fallback
        if price is None:
            print(f"  [⚠️ WARNING] Price for {ticker} unavailable via API.")
            while True:
                try:
                    user_input = input(f"  >> Please enter CURRENT PRICE for {ticker} (or 'skip'): ")
                    if user_input.lower() == 'skip':
                        return None
                    price = float(user_input)
                    break
                except ValueError:
                    print("Invalid price. Try again.")
                    
        return price

    def fetch_history(self, ticker, days=60):
        """
        Fetches historical daily bars for technical analysis.
        Returns a DataFrame with 'high', 'low', 'close' columns (needed for ATR).
        """
        if not self.api:
            return None
            
        try:
            # Calculate start date
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days*2) # Fetch extra days to account for weekends/holidays
            
            bars = self.api.get_bars(
                ticker,
                tradeapi.TimeFrame.Day,
                start=start_date.strftime('%Y-%m-%d'),
                end=end_date.strftime('%Y-%m-%d'),
                limit=days,
                feed='iex'  # [FIX] Use IEX for free paper trading data
            ).df
            
            if bars.empty:
                return None
            
            # Return full OHLC DataFrame (high, low, close) for ATR calculation
            return bars[['high', 'low', 'close']]
            
        except Exception as e:
            # print(f"  [TA-Error] Could not fetch history for {ticker}: {e}")
            return None

    def calculate_atr(self, ohlc_df, period=14):
        """
        Calculates ATR (Average True Range) from an OHLC DataFrame.
        ATR = Rolling Mean of True Range over `period` days.
        True Range = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
        """
        if ohlc_df is None or len(ohlc_df) < period + 1:
            return None
        
        high = ohlc_df['high']
        low = ohlc_df['low']
        close = ohlc_df['close']
        prev_close = close.shift(1)
        
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = true_range.rolling(window=period).mean().iloc[-1]
        
        return atr if not np.isnan(atr) else None

    def calculate_rsi(self, series, period=14):
        """
        Calculates RSI for a pandas Series of prices.
        """
        if len(series) < period + 1:
            return None
            
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]

    def calculate_sma(self, series, period=20):
        """
        Calculates Simple Moving Average.
        """
        if len(series) < period:
            return None
        return series.rolling(window=period).mean().iloc[-1]

    def check_portfolio_risks(self, holdings_data):
        """
        Checks for stop-loss, trailing take-profit, and technical breakdown conditions.
        Uses LIVE Alpaca position data (avg_entry_price) for accuracy.
        
        Returns: (sell_orders, total_proceeds)
            sell_orders: list of SELL order dicts
            total_proceeds: float, estimated cash freed by risk sells (for liquidity recycling)
        """
        sell_orders = []
        total_proceeds = 0.0
        print("\n--- Checking Portfolio Risks (ATR Stop / Trailing TP / Whipsaw) ---")
        
        for ticker, data in holdings_data.items():
            buy_price = data.get('avg_entry', data.get('buy_price', 0))
            shares = int(data.get('qty', data.get('shares', 0)))
            
            if shares <= 0:
                continue
                
            current_price = data.get('current_price') or self.fetch_price(ticker)
            if not current_price:
                continue
            
            # Fetch historical data (OHLC) for ATR and SMA calculations
            ohlc = self.fetch_history(ticker)
            close_series = ohlc['close'] if ohlc is not None else None
            
            # Calculate indicators
            atr_14 = self.calculate_atr(ohlc, self.atr_period) if ohlc is not None else None
            sma_20 = self.calculate_sma(close_series, 20) if close_series is not None else None
            sma_50 = self.calculate_sma(close_series, 50) if close_series is not None else None
            
            sell_reason = None
            
            # ============================================================
            # PRIORITY 1: ATR-Based Dynamic Stop-Loss & Zero-Loss Rule
            # ============================================================
            if atr_14 and atr_14 > 0:
                stop_price = buy_price - (self.atr_multiplier * atr_14)
            else:
                # Fallback: use config's fixed stop-loss percent
                stop_price = buy_price * (1 - self.stop_loss_percent)
            
            # --- ZERO-LOSS RULE ---
            # If unrealized gain > 5%, guarantee a +0.5% buffer on the stop
            is_zero_loss_active = False
            if buy_price > 0:
                unrealized_gain_pct = (current_price - buy_price) / buy_price
                if unrealized_gain_pct > 0.05:
                    zero_loss_stop = buy_price * 1.005
                    if zero_loss_stop > stop_price:
                        stop_price = zero_loss_stop
                        is_zero_loss_active = True
            
            if current_price < stop_price:
                drop_pct = (1 - current_price / buy_price) * 100
                if is_zero_loss_active:
                    sell_reason = (
                        f"SELL: Protected Profit Stop hit (+0.5% Guaranteed) | "
                        f"Entry: ${buy_price:.2f} → Current: ${current_price:.2f} | Stop: ${stop_price:.2f}"
                    )
                elif atr_14:
                    sell_reason = (
                        f"SELL: ATR Stop triggered (-{drop_pct:.1f}%) | "
                        f"Entry: ${buy_price:.2f} → Current: ${current_price:.2f} | "
                        f"Stop: ${stop_price:.2f} (ATR: {atr_14:.2f}, Mult: {self.atr_multiplier})"
                    )
                else:
                    sell_reason = (
                        f"SELL: Hard Stop-Loss reached (-{drop_pct:.1f}%) | "
                        f"Entry: ${buy_price:.2f} → Current: ${current_price:.2f} | "
                        f"Threshold: -{self.stop_loss_percent*100:.0f}% (ATR unavailable)"
                    )
            
            # ============================================================
            # PRIORITY 2: Trailing Take-Profit (High Water Mark)
            # Activates when unrealized gain > 10%, triggers on 3% drop from peak
            # ============================================================
            if sell_reason is None and buy_price > 0:
                unrealized_gain_pct = (current_price - buy_price) / buy_price
                
                if unrealized_gain_pct > self.trailing_activation_pct:
                    # Estimate High Water Mark from recent highs (last 5 trading days)
                    high_water_mark = current_price  # default
                    if ohlc is not None and 'high' in ohlc.columns and len(ohlc) >= 5:
                        high_water_mark = float(ohlc['high'].iloc[-5:].max())
                    
                    # Also compare against current price (in case today is the peak)
                    high_water_mark = max(high_water_mark, current_price)
                    
                    trailing_stop = high_water_mark * (1 - self.trailing_drop_pct)
                    
                    if current_price < trailing_stop:
                        gain_pct = unrealized_gain_pct * 100
                        drop_from_peak = ((high_water_mark - current_price) / high_water_mark) * 100
                        sell_reason = (
                            f"SELL: Trailing Profit Taken ({drop_from_peak:.1f}% drop from peak of ${high_water_mark:.2f}) | "
                            f"Gain: +{gain_pct:.1f}% | Entry: ${buy_price:.2f} → Current: ${current_price:.2f}"
                        )
                    else:
                        print(f"  📈 {ticker}: Trailing TP active (Gain +{unrealized_gain_pct*100:.1f}%, Peak ${high_water_mark:.2f}, Trail Stop ${trailing_stop:.2f})")
            
            # ============================================================
            # PRIORITY 3: Whipsaw-Protected Trend Breakdown
            # BOTH conditions required: Price < SMA20 AND SMA20 < SMA50
            # ============================================================
            if sell_reason is None and sma_20 and sma_50:
                if current_price < sma_20 and sma_20 < sma_50:
                    # Grace period: skip for new holdings (< 24h)
                    last_buy = trade_logger.get_last_buy_time(ticker)
                    hours_held = 999  # Default: assume long-held
                    if last_buy:
                        hours_held = (datetime.now() - last_buy).total_seconds() / 3600
                    
                    if hours_held < 24 and not getattr(self, '_panic_mode', False):
                        gap_pct = ((sma_20 - current_price) / sma_20) * 100
                        print(f"  \U0001f6e1\ufe0f {ticker}: Whipsaw Breakdown detected (gap {gap_pct:.1f}%) but GRACE PERIOD active ({hours_held:.1f}h < 24h). Holding.")
                        trade_logger.log_decision({
                            'ticker': ticker, 'action': 'HOLD', 'quantity': shares,
                            'price': current_price, 'sma_20': sma_20, 'sma_50': sma_50,
                            'atr_14': atr_14,
                            'decision_reason': f'Grace Period ({hours_held:.1f}h): Whipsaw breakdown suppressed'
                        })
                        continue  # Skip to next holding
                    else:
                        gap_pct = ((sma_20 - current_price) / sma_20) * 100
                        sell_reason = (
                            f"SELL: Trend Breakdown (Price ${current_price:.2f} < SMA20 ${sma_20:.2f} < SMA50 ${sma_50:.2f}, gap {gap_pct:.1f}%)"
                        )
            elif sell_reason is None and sma_20 and not sma_50:
                # SMA50 data unavailable — log but do NOT trigger sell (whipsaw protection)
                if current_price < sma_20:
                    gap_pct = ((sma_20 - current_price) / sma_20) * 100
                    print(f"  ⚠️ {ticker}: Price < SMA20 (gap {gap_pct:.1f}%) but SMA50 unavailable — Whipsaw protection: HOLDING.")
            
            # Calculate P&L for SELL
            pnl = (current_price - buy_price) * shares
            pnl_pct = ((current_price - buy_price) / buy_price) * 100 if buy_price > 0 else 0
            
            if sell_reason:
                estimated_proceeds = current_price * shares
                total_proceeds += estimated_proceeds
                
                print(f"  🚨 SELL ALERT for {ticker}: {sell_reason}")
                print(f"     P&L: ${pnl:.2f} ({pnl_pct:.2f}%) | Est. Proceeds: ${estimated_proceeds:.2f}")
                
                # Determine high_water_mark for logging
                log_hwm = None
                if ohlc is not None and 'high' in ohlc.columns and len(ohlc) >= 5:
                    log_hwm = float(ohlc['high'].iloc[-5:].max())
                    log_hwm = max(log_hwm, current_price)
                
                risk_decision_id = trade_logger.log_decision({
                    'ticker': ticker,
                    'action': 'SELL',
                    'quantity': shares,
                    'price': current_price,
                    'sma_20': sma_20,
                    'sma_50': sma_50,
                    'atr_14': atr_14,
                    'high_water_mark': log_hwm,
                    'decision_reason': sell_reason,
                    'entry_price': buy_price,
                    'exit_price': current_price,
                    'pnl': pnl,
                    'pnl_percent': pnl_pct
                })
                
                sell_orders.append({
                    "ticker": ticker,
                    "action": "sell",
                    "quantity": shares,
                    "order_type": "limit",
                    "limit_price": current_price,
                    "reason": sell_reason,
                    "decision_id": risk_decision_id
                })
            else:
                ta_status = ""
                if sma_20: ta_status += f" | SMA20: ${sma_20:.2f}"
                if sma_50: ta_status += f" | SMA50: ${sma_50:.2f}"
                if atr_14: ta_status += f" | ATR: {atr_14:.2f}"
                
                status_msg = f"  ✅ {ticker} safe. (Curr: ${current_price} > Stop: ${stop_price:.2f}"
                if is_zero_loss_active:
                    status_msg += " 🛡️ Protected Profit"
                status_msg += f"{ta_status})"
                print(status_msg)
                
                trade_logger.log_decision({
                    'ticker': ticker,
                    'action': 'HOLD',
                    'quantity': shares,
                    'price': current_price,
                    'sma_20': sma_20,
                    'sma_50': sma_50,
                    'atr_14': atr_14,
                    'decision_reason': f"Safe from ATR Stop & Trailing TP & Whipsaw Breakdown{ta_status}"
                })
                
        return sell_orders, total_proceeds

    def generate_plan(self, sentiment_data, portfolio, env_bias=1.0, macro_reason=''):
        """
        Generates execution plan based on HOLISTIC Portfolio Management.
        - Cost-Basis Budgeting (Gravity-Adjusted by env_bias)
        - Global Ranking (Sentiment * Duration)
        - Smart Partial Rebalancing (50% Swap)
        - Real-Time Liquidity Recycling
        - Defense Mode (env_bias < 0.5): Freeze buys, tighten stops
        """
        print("\n--- Generating Execution Plan (Holistic Manager) ---")
        orders = []
        
        # Store env context for logging
        self._env_bias = env_bias
        self._macro_reason = macro_reason
        
        # GRAVITY-ADJUSTED BUDGET
        effective_budget = self.budget * env_bias
        
        # Defense Mode detection
        safe_hold_mode = (env_bias == 0.0)
        defense_mode = env_bias < 0.5
        self._panic_mode = env_bias < 0.3
        
        if safe_hold_mode:
            print(f"  🚨 SAFE HOLD MODE ACTIVE (BRAIN OFFLINE)")
            print(f"     Macro Reason: {macro_reason}")
            print(f"     → All new buys STRICTLY FORBIDDEN")
            print(f"     → Remaining defensively positioned")
            self.atr_multiplier *= 0.5  # Max tightening
        elif defense_mode:
            print(f"  🚨 DEFENSE MODE ACTIVE (env_bias={env_bias:.2f})")
            print(f"     Macro Reason: {macro_reason}")
            print(f"     → All new buys FROZEN")
            print(f"     → ATR multiplier tightened by 30%")
            # Tighten ATR stop by 30%
            self.atr_multiplier *= 0.7
            
        if self._panic_mode and not safe_hold_mode:
            print(f"  💥 TOTAL PANIC MODE — Grace period OVERRIDDEN")

        # 1. Calculate Cost-Basis Usage
        # We need to query Alpaca for actual avg_entry_price to be accurate.
        cost_basis_total = 0.0
        current_holdings_data = {} # Map ticker -> {qty, avg_price, market_value}
        
        if self.api:
            try:
                positions = self.api.list_positions()
                for p in positions:
                    qty = float(p.qty)
                    avg_entry = float(p.avg_entry_price)
                    market_val = float(p.market_value)
                    
                    cost_basis_total += (qty * avg_entry)
                    current_holdings_data[p.symbol] = {
                        'qty': qty,
                        'avg_entry': avg_entry,
                        'market_value': market_val,
                        'current_price': float(p.current_price)
                    }
            except Exception as e:
                print(f"  ⚠️ Error fetching positions: {e}")
                # Fallback to portfolio file if API fails (Mock mode)
                for ticker, data in portfolio.get('positions', {}).items():
                    cost_basis_total += (data['shares'] * data['buy_price'])
                    current_holdings_data[ticker] = {
                        'qty': data['shares'],
                        'avg_entry': data['buy_price'],
                    }

        remaining_budget = effective_budget - cost_basis_total
        print(f"  💰 Total Budget (Principal): ${self.budget:.2f}")
        print(f"     Gravity-Adjusted Budget:  ${effective_budget:.2f} (×{env_bias:.2f})")
        print(f"     Used (Cost Basis):        ${cost_basis_total:.2f}")
        print(f"     Remaining (For Buys):     ${remaining_budget:.2f}")

        # 2. Build Global Rank List
        rank_list = []
        
        # DEFENSE / SAFE_HOLD MODE: Skip all new buy signals
        if defense_mode or safe_hold_mode:
            mode_name = "SAFE HOLD MODE" if safe_hold_mode else "Defense Mode"
            print(f"\n  🛡️ {mode_name}: Skipping all {len(sentiment_data)} new buy signals.")
            trade_logger.log_decision({
                'ticker': 'SYSTEM', 'action': 'DEFENSE_MODE', 'price': 0,
                'sentiment_score': 0, 'duration_score': 0,
                'decision_reason': f'{mode_name}: env_bias={env_bias:.2f}. All buys frozen. Reason: {macro_reason}',
                'env_bias': env_bias, 'macro_reason': macro_reason
            })
        else:
            # A. Add New Signals (normal mode)
            for signal in sentiment_data:
                if signal.get('action') == 'Buy':
                    ticker = signal.get('ticker')
                
                    # Validate ticker exists on Alpaca before processing
                    if not self.validate_ticker(ticker):
                        trade_logger.log_decision({
                            'ticker': ticker, 'action': 'SKIP', 'price': 0,
                            'sentiment_score': signal.get('sentiment_score', 0),
                            'duration_score': signal.get('duration_score', 0.5),
                            'decision_reason': f'SKIP: Ticker {ticker} not found/tradable on Alpaca'
                        })
                        continue
                
                    # Rank = Sentiment * Duration
                    sent_score = signal.get('sentiment_score', 0)
                    dur_score = signal.get('duration_score', 0.5) # Default to mid if missing
                    rank_score = sent_score * dur_score
                
                    rank_list.append({
                        'ticker': ticker,
                        'type': 'new_signal',
                        'rank_score': rank_score,
                        'sent_score': sent_score,
                        'dur_score': dur_score,
                        'price': self.fetch_price(ticker),
                        'reason': signal.get('reasoning')
                    })

        # B. Add Existing Holdings
        for ticker, data in current_holdings_data.items():
            if data['qty'] > 0:
                # Fetch last known scores
                scores = trade_logger.get_latest_scores(ticker)
                last_sent = scores['sentiment']
                last_dur = scores['duration']
                rank_score = last_sent * last_dur
                
                current_price = self.fetch_price(ticker)
                
                rank_list.append({
                    'ticker': ticker,
                    'type': 'holding',
                    'rank_score': rank_score,
                    'sent_score': last_sent,
                    'dur_score': last_dur,
                    'price': current_price,
                    'qty': data['qty'],
                    'reason': "Existing Position"
                })

        # C. Rank by Rank_Score (Descending)
        rank_list.sort(key=lambda x: x['rank_score'], reverse=True)
        
        print("\n--- Global Ranking (Top 5) [Score = Sent * Dur] ---")
        for i, item in enumerate(rank_list[:5]):
            print(f"  {i+1}. {item['ticker']} ({item['type']}) - Rank: {item['rank_score']:.3f} (S:{item['sent_score']:.1f} * D:{item['dur_score']:.1f})")

        # 3. Risk Check (Force Sells & Technicals) - using LIVE data
        #    Returns (sell_orders, total_proceeds) for liquidity recycling
        risk_sells, risk_proceeds = self.check_portfolio_risks(current_holdings_data)
        orders.extend(risk_sells)
        
        sold_tickers = [o['ticker'] for o in risk_sells]
        
        # LIQUIDITY RECYCLING: Add proceeds from risk sells to remaining budget
        if risk_proceeds > 0:
            remaining_budget += risk_proceeds
            print(f"\n  💰 Liquidity Recycling: +${risk_proceeds:.2f} from risk sells → Available budget now ${remaining_budget:.2f}")
        
        # 4. Smart Execution (Swap & Buy)
        
        for item in rank_list:
            if item['type'] != 'new_signal':
                continue
                
            new_ticker = item['ticker']
            new_rank = item['rank_score']
            new_price = item['price']
            
            if not new_price: continue

            # --- PATCH A: Cooldown & Concentration Guard ---
            # 1. 4-Hour Cooldown: Skip if traded recently
            if trade_logger.is_on_cooldown(new_ticker, hours=4):
                print(f"  ⏸️ Skipping {new_ticker}: On 4-hour cooldown (traded recently).")
                trade_logger.log_decision({
                    'ticker': new_ticker, 'action': 'SKIP', 'price': new_price,
                    'sentiment_score': item['sent_score'], 'duration_score': item['dur_score'],
                    'decision_reason': 'SKIP: 4-hour cooldown active'
                })
                continue
            
            # 2. Max Shares Per Ticker: Cap at TOTAL_BUDGET * MAX_CONCENTRATION / price
            max_shares_for_ticker = math.floor(
                (self.budget * self.max_concentration_percent) / new_price
            )
            current_held = current_holdings_data.get(new_ticker, {}).get('qty', 0)
            current_held = int(current_held)
            
            if current_held >= max_shares_for_ticker:
                print(f"  🚫 Skipping {new_ticker}: Already at max concentration ({current_held} shares, max {max_shares_for_ticker}).")
                trade_logger.log_decision({
                    'ticker': new_ticker, 'action': 'SKIP', 'price': new_price,
                    'sentiment_score': item['sent_score'], 'duration_score': item['dur_score'],
                    'decision_reason': f'SKIP: Max concentration reached ({current_held}/{max_shares_for_ticker} shares)'
                })
                continue

            # Technical Filter (SYNCED with Risk Check criteria)
            ohlc = self.fetch_history(new_ticker)
            rsi = None
            sma_20 = None
            sma_50 = None
            if ohlc is not None:
                close_series = ohlc['close']
                rsi = self.calculate_rsi(close_series, 14)
                sma_20 = self.calculate_sma(close_series, 20)
                sma_50 = self.calculate_sma(close_series, 50)
                
                # RSI filter (overbought)
                if rsi > 75 or (rsi >= 65 and new_price <= sma_20): 
                     skip_reason = f"SKIP BUY: Technicals weak (RSI {rsi:.1f})"
                     print(f"  ⚠️ Skipping {new_ticker}: {skip_reason}")
                     trade_logger.log_decision({
                         'ticker': new_ticker,
                         'action': 'SKIP',
                         'price': new_price,
                         'sentiment_score': item['sent_score'],
                         'duration_score': item['dur_score'],
                         'rsi_14': rsi,
                         'sma_20': sma_20,
                         'sma_50': sma_50,
                         'decision_reason': skip_reason
                     })
                     continue
                
                # ENTRY FILTER: Whipsaw-Protected Trend Check (synced with risk check)
                # Reject BUY if Price < SMA20 AND SMA20 < SMA50 (confirmed downtrend)
                # OPTIMIZATION: Allow "Contrarian Entry" if Oversold OR High Conviction
                if sma_20 and sma_50 and new_price < sma_20 and sma_20 < sma_50:
                     gap_pct = ((sma_20 - new_price) / sma_20) * 100
                     
                     # Exception 1: Oversold (RSI < 35)
                     is_oversold = (rsi is not None and rsi < 35)
                     # Exception 2: High Conviction (Rank > 0.5)
                     is_high_conviction = (new_rank >= 0.5)
                     
                     if is_oversold or is_high_conviction:
                         why = "Oversold (RSI < 35)" if is_oversold else f"High Conviction (Rank {new_rank:.2f})"
                         print(f"  📉 Contrarian Entry Accepted: {new_ticker} is in confirmed downtrend (gap {gap_pct:.1f}%) but {why}.")
                     else:
                         skip_reason = f"SKIP BUY: {new_ticker} in confirmed downtrend (Price ${new_price:.2f} < SMA20 ${sma_20:.2f} < SMA50 ${sma_50:.2f}, gap {gap_pct:.1f}%)"
                         print(f"  🚫 {skip_reason}")
                         trade_logger.log_decision({
                             'ticker': new_ticker,
                             'action': 'SKIP',
                             'price': new_price,
                             'sentiment_score': item['sent_score'],
                             'duration_score': item['dur_score'],
                             'rsi_14': rsi,
                             'sma_20': sma_20,
                             'sma_50': sma_50,
                             'decision_reason': skip_reason
                         })
                         continue
                elif sma_20 and not sma_50 and new_price < sma_20:
                     # SMA50 unavailable: fall back to single-SMA check (original behavior)
                     gap_pct = ((sma_20 - new_price) / sma_20) * 100
                     is_oversold = (rsi is not None and rsi < 35)
                     is_high_conviction = (new_rank >= 0.5)
                     
                     if is_oversold or is_high_conviction:
                         why = "Oversold (RSI < 35)" if is_oversold else f"High Conviction (Rank {new_rank:.2f})"
                         print(f"  📉 Contrarian Entry Accepted: {new_ticker} below SMA20 (gap {gap_pct:.1f}%, SMA50 N/A) but {why}.")
                     else:
                         skip_reason = f"SKIP BUY: {new_ticker} below SMA20 (Price ${new_price:.2f} < SMA20 ${sma_20:.2f}, gap {gap_pct:.1f}%, SMA50 unavailable)"
                         print(f"  🚫 {skip_reason}")
                         trade_logger.log_decision({
                             'ticker': new_ticker,
                             'action': 'SKIP',
                             'price': new_price,
                             'sentiment_score': item['sent_score'],
                             'duration_score': item['dur_score'],
                             'rsi_14': rsi,
                             'sma_20': sma_20,
                             'decision_reason': skip_reason
                         })
                         continue
            
            target_trade_value = self.budget * self.risk_per_trade_percent
            
            # HYBRID FRACTIONAL/WHOLE SHARE LOGIC
            raw_qty = target_trade_value / new_price
            
            if raw_qty >= 1.0 and remaining_budget >= new_price:
                # PRIMARY: Whole-share limit order
                shares_to_buy = math.floor(raw_qty)
                
                # [OPTIMIZATION] Minimum 1 Share Rule
                if shares_to_buy == 0:
                    max_allowed_shares = math.floor((self.budget * self.max_concentration_percent) / new_price)
                    if max_allowed_shares >= 1:
                        shares_to_buy = 1
                        print(f"  🔹 Upgrading trade to 1 share (Price ${new_price:.2f} > Target ${target_trade_value:.2f} but < Max Concen).")
                
                if shares_to_buy > 0:
                     cost = shares_to_buy * new_price
                     if cost > remaining_budget:
                         shares_to_buy = math.floor(remaining_budget / new_price)
                         cost = shares_to_buy * new_price
                     
                     if shares_to_buy > 0:
                         remaining_budget -= cost
                         buy_order_type = 'limit'
                         reason = f"Holistic Buy (Rank {new_rank:.3f})."
                         
                         decision_id = trade_logger.log_decision({
                            'ticker': new_ticker,
                            'action': 'BUY',
                            'quantity': shares_to_buy,
                            'price': new_price,
                            'sentiment_score': item['sent_score'],
                            'duration_score': item['dur_score'],
                            'rsi_14': rsi,
                            'sma_20': sma_20,
                            'sma_50': sma_50,
                            'decision_reason': reason
                         })
                         
                         orders.append({
                            "ticker": new_ticker,
                            "action": "buy",
                            "quantity": shares_to_buy,
                            "order_type": buy_order_type,
                            "limit_price": new_price,
                            "reason": reason,
                            "decision_id": decision_id
                         })
                         print(f"  ✅ BUY {shares_to_buy} {new_ticker} (Limit ${new_price:.2f}) [DB#{decision_id}]")
                         continue
            
            elif 0 < raw_qty < 1.0 and remaining_budget > 0 and remaining_budget < new_price:
                # GAP-FILLER: Fractional share market order
                frac_qty = round(remaining_budget / new_price, 4)
                if frac_qty > 0:
                    cost = frac_qty * new_price
                    remaining_budget -= cost
                    buy_order_type = 'market'
                    reason = f"Fractional Gap-Fill (Rank {new_rank:.3f}, Qty {frac_qty})."
                    
                    decision_id = trade_logger.log_decision({
                        'ticker': new_ticker,
                        'action': 'BUY',
                        'quantity': frac_qty,
                        'price': new_price,
                        'sentiment_score': item['sent_score'],
                        'duration_score': item['dur_score'],
                        'rsi_14': rsi,
                        'sma_20': sma_20,
                        'sma_50': sma_50,
                        'decision_reason': reason
                    })
                    
                    orders.append({
                        "ticker": new_ticker,
                        "action": "buy",
                        "quantity": frac_qty,
                        "order_type": buy_order_type,
                        "reason": reason,
                        "decision_id": decision_id
                    })
                    print(f"  ✅ BUY {frac_qty} {new_ticker} (Market, Fractional) [DB#{decision_id}]")
                    continue

            # Check 2: Swap Opportunity (50% Rule)
            # Look for a holding that is significantly weaker (New > Old * 1.2)
            # And STRICTLY enforce we only swap 50%
            
            for potential_swap in reversed(rank_list):
                if potential_swap['type'] == 'holding' and potential_swap['ticker'] not in sold_tickers:
                    old_ticker = potential_swap['ticker']
                    old_rank = potential_swap['rank_score']
                    old_qty = potential_swap['qty']
                    
                    # Improvement Threshold: 20%
                    # Handle low scores: if old_rank is 0, new_rank > 0.1 is enough
                    threshold_met = False
                    if old_rank <= 0.01:
                        if new_rank > 0.1: threshold_met = True
                    elif new_rank > (old_rank * 1.2):
                        threshold_met = True
                    
                    if threshold_met:
                        print(f"  🔄 Partial Swap: {new_ticker} ({new_rank:.3f}) >> {old_ticker} ({old_rank:.3f})")
                        
                        # Sell 50% of Old (FLOOR)
                        swap_qty = math.floor(old_qty * 0.5)
                        
                        if swap_qty <= 0:
                             print(f"     [Skip Swap] 50% of {old_ticker} (Qty: {old_qty}) is 0.")
                             continue
                        
                        # LOGIC:
                        # 1. Sell Swap Qty
                        # 2. Use Proceeds to Buy New Ticker
                        
                        proceeds = swap_qty * potential_swap['price']
                        
                        # Sell Order
                        sold_tickers.append(old_ticker)
                        
                        sell_decision_id = trade_logger.log_decision({
                            'ticker': old_ticker,
                            'action': 'SELL',
                            'quantity': swap_qty,
                            'price': potential_swap['price'],
                            'decision_reason': f"Partial Swap for {new_ticker}"
                        })
                        
                        orders.append({
                            "ticker": old_ticker,
                            "action": "sell",
                            "quantity": swap_qty,
                            "order_type": "limit",
                            "limit_price": potential_swap['price'],
                            "reason": f"Partial Swap for {new_ticker}",
                            "decision_id": sell_decision_id
                        })

                        # Buy Order funded by swap proceeds
                        # HYBRID: Swap buy quantity
                        raw_swap_qty = proceeds / new_price
                        
                        if raw_swap_qty >= 1.0:
                            shares_to_buy_swap = math.floor(raw_swap_qty)
                            swap_order_type = 'limit'
                        elif raw_swap_qty > 0:
                            shares_to_buy_swap = round(raw_swap_qty, 4)
                            swap_order_type = 'market'
                        else:
                            shares_to_buy_swap = 0
                            swap_order_type = 'limit'
                        
                        if shares_to_buy_swap > 0:
                            reason = f"Swap Buy via {old_ticker} (proceeds ${proceeds:.2f})."
                            
                            buy_decision_id = trade_logger.log_decision({
                                'ticker': new_ticker,
                                'action': 'BUY',
                                'quantity': shares_to_buy_swap,
                                'price': new_price,
                                'sentiment_score': item['sent_score'],
                                'duration_score': item['dur_score'],
                                'decision_reason': reason
                            })
                            
                            swap_buy_order = {
                                "ticker": new_ticker,
                                "action": "buy",
                                "quantity": shares_to_buy_swap,
                                "order_type": swap_order_type,
                                "reason": reason,
                                "decision_id": buy_decision_id
                            }
                            if swap_order_type == 'limit':
                                swap_buy_order["limit_price"] = new_price
                            orders.append(swap_buy_order)
                            
                            frac_label = f" (Market, Fractional)" if swap_order_type == 'market' else f" (Limit ${new_price:.2f})"
                            print(f"     -> Selling {swap_qty} {old_ticker} to Buy {shares_to_buy_swap} {new_ticker}{frac_label}")
                        
                        # LIQUIDITY RECYCLING: Add leftover proceeds back to budget
                        buy_cost = (shares_to_buy_swap * new_price) if shares_to_buy_swap > 0 else 0
                        leftover = proceeds - buy_cost
                        if leftover > 0:
                            remaining_budget += leftover
                            print(f"     💰 Recycled ${leftover:.2f} back to available budget (now ${remaining_budget:.2f})")
                        
                        break # One swap performed for this signal

        return orders

def main():
    try:
        with open('sentiment_data.json', 'r') as f:
            raw_data = json.load(f)
        with open('current_portfolio.json', 'r') as f:
            portfolio = json.load(f)
        
        # V3 FORMAT: Extract macro envelope
        if isinstance(raw_data, dict) and 'signals' in raw_data:
            env_bias = raw_data.get('global_env_bias', 1.0)
            macro_reason = raw_data.get('macro_reason', '')
            sentiment_data = raw_data['signals']
        else:
            # Backward-compatible: plain array (V2)
            env_bias = 1.0
            macro_reason = 'Legacy format (no macro data)'
            sentiment_data = raw_data
        
    except FileNotFoundError as e:
        print(f"Error loading input files: {e}")
        return

    engine = TradingLogic(
        budget=config.TOTAL_BUDGET,
        risk_per_trade_percent=config.RISK_PER_TRADE_PERCENT,
        stop_loss_percent=config.STOP_LOSS_PERCENT,
        max_concentration_percent=config.MAX_CONCENTRATION_PERCENT
    )

    plan = engine.generate_plan(sentiment_data, portfolio, env_bias=env_bias, macro_reason=macro_reason)

    output_file = 'execution_plan.json'
    with open(output_file, 'w') as f:
        json.dump(plan, f, indent=4)
        
    print(f"\nExecution Plan Saved to {output_file} ({len(plan)} orders)")

if __name__ == "__main__":
    main()
