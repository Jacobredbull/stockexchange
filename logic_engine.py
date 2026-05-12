import json
import os
import math
import pandas as pd
import numpy as np
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
import config
import trade_logger  # [NEW] Import Logger
from datetime import datetime, timedelta

class TradingLogic:
    def __init__(self):
        self.budget = config.TOTAL_BUDGET
        
        # Pillar 1: Slot-Based Execution
        self.max_slots = config.MAX_SLOTS
        
        # Pillar 2: Volatility Moat
        self.risk_per_trade_pct = config.RISK_PER_TRADE_PCT
        self.atr_multiplier = config.ATR_MULTIPLIER
        self.atr_period = config.ATR_PERIOD
        self.max_volatility_pct = config.MAX_VOLATILITY_PCT
        
        # Pillar 3: Stop-Loss & Trailing
        self.breakeven_trigger_pct = config.BREAKEVEN_TRIGGER_PCT
        self.trailing_activation_pct = config.TRAILING_ACTIVATION_PCT
        self.trailing_drop_pct = config.TRAILING_DROP_PCT
        
        # Pillar 4: Cost-Aware
        self.min_order_value = config.MIN_ORDER_VALUE
        
        # Pillar 5: Incremental Swap
        self.scout_replace_threshold = config.SCOUT_REPLACE_THRESHOLD
        self.full_replace_threshold = config.FULL_REPLACE_THRESHOLD
        self.scout_validation_sessions = config.SCOUT_VALIDATION_SESSIONS
        self.scout_mercy_drop_pct = config.SCOUT_MERCY_DROP_PCT
        
        # Scoring
        self.return_cap = config.RETURN_CAP
        
        # Alpaca Setup
        self.api_key = os.getenv("ALPACA_API_KEY", "REPLACE_ME")
        self.secret_key = os.getenv("ALPACA_SECRET_KEY", "REPLACE_ME")
        
        self.client = None
        self.data_client = None
        if "REPLACE" not in self.api_key:
             try:
                 self.client = TradingClient(self.api_key, self.secret_key, paper=True)
                 self.data_client = StockHistoricalDataClient(self.api_key, self.secret_key)
             except Exception as e:
                 print(f"Warning: Alpaca API failed to init: {e}")
        
        # keep self.api as alias so rest of code can check `if self.api`
        self.api = self.client
        
        # Initialize Database
        trade_logger.init_db()
        
        # Ticker validation cache
        self._ticker_cache = {}


    def calculate_weighted_score(self, bias, return_pct, atr, price):
        """
        Five Pillars scoring: 0.4*Bias + 0.3*cappedReturn + 0.3*(1-NormATR)
        R3: Return% capped at RETURN_CAP to prevent moonshot bias.
        """
        capped_return = min(return_pct, self.return_cap) if return_pct > 0 else max(return_pct, -self.return_cap)
        norm_atr = min(atr / price, 1.0) if price > 0 and atr > 0 else 1.0
        return 0.4 * bias + 0.3 * capped_return + 0.3 * (1 - norm_atr)

    def calculate_position_size(self, atr, price, env_bias=1.0):
        """
        Pillar 2: ATR-based position sizing (2% Rule) + Hard Capital Cap.
        Qty A = floor(BUDGET * 2% / (2 * ATR))
        Qty B = floor((BUDGET / MAX_SLOTS * env_bias) / price)
        Returns min(Qty A, Qty B), or 0 if volatility exceeds hard filter.
        """
        if not atr or atr <= 0 or not price or price <= 0:
            return 0
        
        # Hard Filter: too volatile
        if atr / price > self.max_volatility_pct:
            return 0
        
        # Method A: ATR Risk Parity (Limit loss per trade to RISK_PER_TRADE_PCT)
        atr_qty = (self.budget * self.risk_per_trade_pct) / (2 * atr)
        
        # Method B: Hard Capital Slot Cap (Scaled strictly by Gemini's Macro Bias)
        slot_budget = (self.budget / self.max_slots) * env_bias
        capital_qty = slot_budget / price
        
        # The system takes the MOST conservative (smallest) quantity
        final_qty = min(atr_qty, capital_qty)
        return math.floor(final_qty)

    def validate_ticker(self, ticker):
        """
        Validates that a ticker exists and is tradable on Alpaca.
        Returns True if valid, False otherwise. Results are cached.
        """
        if ticker in self._ticker_cache:
            return self._ticker_cache[ticker]
        
        if not self.client:
            self._ticker_cache[ticker] = True  # Can't validate without API, assume OK
            return True
        
        try:
            asset = self.client.get_asset(ticker)
            is_valid = asset.tradable and asset.status.value == 'active'
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
        if self.data_client:
            try:
                req = StockLatestQuoteRequest(symbol_or_symbols=ticker)
                quote = self.data_client.get_stock_latest_quote(req)
                price = float(quote[ticker].ask_price or quote[ticker].bid_price)
                print(f"  [API] Fetched {ticker} price: ${price:.2f}")
            except Exception as e:
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
        if not self.data_client:
            return None
            
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days*2)
            
            req = StockBarsRequest(
                symbol_or_symbols=ticker,
                timeframe=TimeFrame.Day,
                start=start_date,
                end=end_date,
                limit=days,
                feed='iex'
            )
            bars_response = self.data_client.get_stock_bars(req)
            bars = bars_response.df
            
            if bars.empty:
                return None
            
            # Flatten multi-index if present
            if isinstance(bars.index, pd.MultiIndex):
                bars = bars.xs(ticker, level='symbol')
            
            return bars[['high', 'low', 'close']]
            
        except Exception as e:
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
            # PILLAR 3: ATR-Based Stop-Loss + Breakeven + Trailing
            # ============================================================
            if atr_14 and atr_14 > 0:
                stop_price = buy_price - (self.atr_multiplier * atr_14)
            else:
                # Fallback: flat 8% stop if ATR unavailable
                stop_price = buy_price * 0.92
            
            # --- BREAKEVEN RULE (P3) --- Persisted High Water Mark ---
            # Once price hits +3%, breakeven is permanently locked via DB-persisted HWM.
            # This survives across sessions: even if price drops back, stop stays at entry.
            is_breakeven_active = False
            is_trailing_active = False
            peak_price_ever = current_price  # Default; updated below
            if buy_price > 0:
                # Read persisted HWM from DB — survives across sessions
                persisted_hwm = trade_logger.get_high_water_mark(ticker)
                # Only use persisted HWM + current live price.
                # Do NOT use ohlc daily highs — they may include days before entry.
                peak_price_ever = max(current_price, persisted_hwm or 0)
                
                # Use HISTORICAL peak (not just current price) for activation checks
                max_gain_pct = (peak_price_ever - buy_price) / buy_price
                
                if max_gain_pct >= self.trailing_activation_pct:
                    # TRAILING STOP: 1.5% drop from persisted peak
                    trailing_stop = peak_price_ever * (1 - self.trailing_drop_pct)
                    if trailing_stop > stop_price:
                        stop_price = trailing_stop
                        is_trailing_active = True
                elif max_gain_pct >= self.breakeven_trigger_pct:
                    # BREAKEVEN: once triggered by historical peak, NEVER reverts
                    if buy_price > stop_price:
                        stop_price = buy_price
                        is_breakeven_active = True
            
            if current_price < stop_price:
                drop_pct = (1 - current_price / buy_price) * 100
                if is_trailing_active:
                    drop_from_peak = ((peak_price_ever - current_price) / peak_price_ever) * 100
                    sell_reason = (
                        f"SELL: Trailing Stop hit ({drop_from_peak:.1f}% drop from peak ${peak_price_ever:.2f}) | "
                        f"Entry: ${buy_price:.2f} → Current: ${current_price:.2f} | Stop: ${stop_price:.2f}"
                    )
                elif is_breakeven_active:
                    sell_reason = (
                        f"SELL: Breakeven Stop hit | "
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
                        f"Threshold: 8% (ATR unavailable)"
                    )
            
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
                            'atr_14': atr_14, 'high_water_mark': peak_price_ever,
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
            
            # ============================================================
            # PRIORITY 4: Time Stop — force-sell losing positions held too long
            # ============================================================
            if sell_reason is None and buy_price > 0 and current_price < buy_price:
                last_buy = trade_logger.get_last_buy_time(ticker)
                if last_buy:
                    days_held = (datetime.now() - last_buy).days
                    if days_held >= config.TIME_STOP_DAYS:
                        loss_pct = ((buy_price - current_price) / buy_price) * 100
                        sell_reason = (
                            f"SELL: Time Stop ({days_held}d held, -{loss_pct:.1f}% loss) | "
                            f"Entry: ${buy_price:.2f} → Current: ${current_price:.2f}"
                        )
            
            # Calculate P&L for SELL
            pnl = (current_price - buy_price) * shares
            pnl_pct = ((current_price - buy_price) / buy_price) * 100 if buy_price > 0 else 0
            
            if sell_reason:
                estimated_proceeds = current_price * shares
                total_proceeds += estimated_proceeds
                
                print(f"  🚨 SELL ALERT for {ticker}: {sell_reason}")
                print(f"     P&L: ${pnl:.2f} ({pnl_pct:.2f}%) | Est. Proceeds: ${estimated_proceeds:.2f}")
                
                # Use the persisted peak price (already computed above)
                log_hwm = peak_price_ever
                
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
                if is_trailing_active:
                    status_msg += " 📈 Trailing Active"
                elif is_breakeven_active:
                    status_msg += " 🛡️ Breakeven Active"
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
                    'high_water_mark': peak_price_ever,
                    'decision_reason': f"Safe from ATR Stop & Trailing TP & Whipsaw Breakdown{ta_status}"
                })
                
        return sell_orders, total_proceeds

    def check_budget_overflow(self, holdings_data, env_bias):
        """
        Calculates if any existing position severely exceeds the Double Hard Capital Cap.
        Automatically trims excess shares to return capital to the budget.
        """
        trim_orders = []
        max_capital_per_slot = (self.budget / self.max_slots) * env_bias
        
        # Add a 15% tolerance buffer so natural profit growth doesn't trigger micro-selling
        trim_threshold = max_capital_per_slot * 1.15
        
        print(f"\n--- Checking Budget Overflow (Cap: ${max_capital_per_slot:.2f}, Threshold: ${trim_threshold:.2f}) ---")
        
        for ticker, data in holdings_data.items():
            shares = int(data.get('qty', 0))
            if shares <= 0:
                continue
                
            current_price = data.get('current_price') or self.fetch_price(ticker)
            if not current_price:
                continue
                
            market_value = shares * current_price
            
            # If the market value severely exceeds the allowed capital
            if market_value > trim_threshold:
                # Calculate how many shares to sell to get back to exactly max_capital_per_slot
                excess_value = market_value - max_capital_per_slot
                shares_to_sell = math.floor(excess_value / current_price)
                
                if shares_to_sell > 0:
                    sell_reason = f"Budget Trim: Position Value ${market_value:.2f} exceeds cap ${max_capital_per_slot:.2f}. Trimming {shares_to_sell} shares."
                    print(f"  ✂️ TRIM ALERT for {ticker}: {sell_reason}")
                    
                    sell_id = trade_logger.log_decision({
                        'ticker': ticker,
                        'action': 'SELL',
                        'quantity': shares_to_sell,
                        'price': current_price,
                        'decision_reason': sell_reason
                    })
                    
                    trim_orders.append({
                        "ticker": ticker,
                        "action": "sell",
                        "quantity": shares_to_sell,
                        "order_type": "market",  # Use market order for immediate trimming
                        "reason": "Budget Trim (Overflow)",
                        "decision_id": sell_id
                    })
                    
                    # Prevent future logic in generate_plan from acting like we still hold these
                    data['qty'] -= shares_to_sell
                    
        return trim_orders

    def generate_plan(self, sentiment_data, portfolio, env_bias=1.0, macro_reason=''):
        """Five Pillars Execution Plan Generator (v2.1 — Graduated Risk Scaling)."""
        print("\n--- Generating Execution Plan (Five Pillars v2.1) ---")
        orders = []
        self._env_bias = env_bias
        self._macro_reason = macro_reason
        
        safe_hold_mode = (env_bias == 0.0)
        self._panic_mode = env_bias < 0.3
        
        # ── Graduated Risk Scaling (replaces binary Defense Mode) ──
        risk_scaled_slots = self.max_slots
        min_entry_score = 0.45  # Default minimum
        risk_label = "🟢 NORMAL"
        
        if safe_hold_mode:
            print(f"  🚨 SAFE HOLD MODE ACTIVE — Reason: {macro_reason}")
            self.atr_multiplier = config.ATR_MULTIPLIER * config.ATR_MULTIPLIER_CRITICAL
        else:
            for threshold, max_slots, min_score in config.RISK_TIERS:
                if env_bias < threshold:
                    risk_scaled_slots = max_slots
                    min_entry_score = min_score
                    break
            
            # P4: ELEVATED Decay — if stuck in ELEVATED too long, relax min_score gradually
            # Only applies to ELEVATED (0.3-0.5), NEVER relaxes CRITICAL (<0.3)
            if 0.3 <= env_bias < 0.5:
                elevated_days = trade_logger.get_consecutive_elevated_days()
                if elevated_days > config.ELEVATED_DECAY_DAYS:
                    decay = (elevated_days - config.ELEVATED_DECAY_DAYS) * config.ELEVATED_DECAY_RATE
                    original_min = min_entry_score
                    min_entry_score = max(min_entry_score - decay, config.ELEVATED_MIN_FLOOR)
                    if min_entry_score < original_min:
                        print(f"  📉 ELEVATED Decay: {elevated_days}d consecutive → min_score {original_min:.2f} → {min_entry_score:.2f}")
            
            if env_bias < 0.3:
                risk_label = "🔴 CRITICAL"
                self.atr_multiplier = config.ATR_MULTIPLIER * config.ATR_MULTIPLIER_CRITICAL
            elif env_bias < 0.5:
                risk_label = "🟠 ELEVATED"
                self.atr_multiplier = config.ATR_MULTIPLIER * config.ATR_MULTIPLIER_ELEVATED
            elif env_bias < 0.8:
                risk_label = "🟡 CAUTIOUS"
        
        print(f"  {risk_label} (env_bias={env_bias:.2f}) | Max Slots: {risk_scaled_slots} | Min Score: {min_entry_score:.2f}")
        if macro_reason:
            print(f"  📰 Macro: {macro_reason[:120]}")

        # ── 1. Fetch Positions ──
        current_holdings_data = {}
        if self.client:
            try:
                positions = self.client.get_all_positions()
                for p in positions:
                    current_holdings_data[p.symbol] = {
                        'qty': float(p.qty), 'avg_entry': float(p.avg_entry_price),
                        'market_value': float(p.market_value), 'current_price': float(p.current_price)
                    }
            except Exception as e:
                print(f"  ⚠️ Error fetching positions: {e}")
                for ticker, data in portfolio.get('positions', {}).items():
                    current_holdings_data[ticker] = {'qty': data['shares'], 'avg_entry': data['buy_price']}
        
        num_positions = len([t for t, d in current_holdings_data.items() if d.get('qty', 0) > 0])
        open_slots = max(0, min(risk_scaled_slots, self.max_slots) - num_positions)
        print(f"  📊 Slots: {num_positions}/{self.max_slots} used | {open_slots} open (risk-adjusted max: {risk_scaled_slots})")

        # ── 3. P3: Risk Checks (+ Time Stop) ──
        risk_sells, risk_proceeds = self.check_portfolio_risks(current_holdings_data)
        orders.extend(risk_sells)
        
        # ── 3.1 FIX: Initialize sold_tickers early — all downstream logic reads this ──
        sold_tickers = [o['ticker'] for o in risk_sells]
        
        # Immediately remove sold tickers from holdings so they don't block slots or appear in further logic
        for ticker in sold_tickers:
            if ticker in current_holdings_data:
                del current_holdings_data[ticker]
                
        # Recalculate slots cleanly
        num_positions = len([t for t, d in current_holdings_data.items() if d.get('qty', 0) > 0])
        open_slots = max(0, risk_scaled_slots - num_positions)

        # ── 3.5 Automated Budget Overflow Trim ──
        trim_orders = self.check_budget_overflow(current_holdings_data, env_bias)
        orders.extend(trim_orders)

        # ── 4. Safe Hold: freeze all buys (only for env_bias == 0.0) ──
        if safe_hold_mode:
            print(f"\n  🚨 SAFE HOLD: All buys frozen.")
            trade_logger.log_decision({
                'ticker': 'SYSTEM', 'action': 'DEFENSE_MODE', 'price': 0,
                'decision_reason': f'SAFE HOLD: env_bias=0.00. Reason: {macro_reason}',
                'env_bias': env_bias, 'macro_reason': macro_reason
            })
            return orders
        
        # Log risk tier for non-normal modes (for tracking)
        if env_bias < 0.8:
            trade_logger.log_decision({
                'ticker': 'SYSTEM', 'action': 'RISK_SCALED', 'price': 0,
                'decision_reason': f'{risk_label}: env_bias={env_bias:.2f}, slots={risk_scaled_slots}, min_score={min_entry_score:.2f}. Reason: {macro_reason}',
                'env_bias': env_bias, 'macro_reason': macro_reason
            })

        # ── 4.5 Sentiment-Driven Sell: Act on audited negative signals for held tickers ──
        SELL_SENTIMENT_THRESHOLD = -0.4  # Only act on strong negative signals
        print("\n--- Checking Sell Signals Against Holdings ---")
        for signal in sentiment_data:
            if signal.get('action') != 'Sell':
                continue
            sig_ticker = signal.get('ticker')
            if not sig_ticker or sig_ticker not in current_holdings_data:
                continue
            # Only act on audited signals (High or Medium consensus)
            consensus = signal.get('consensus_level')
            if consensus not in ('High', 'Medium'):
                continue
            sent_score = signal.get('sentiment_score', 0)
            if sent_score > SELL_SENTIMENT_THRESHOLD:
                continue
            
            data = current_holdings_data[sig_ticker]
            shares = int(data.get('qty', 0))
            if shares <= 0:
                continue
            
            cp = data.get('current_price') or self.fetch_price(sig_ticker)
            if not cp:
                continue
            
            sell_reason = (
                f"Sentiment Sell: Audited {consensus} consensus, "
                f"score={sent_score:.2f} ≤ {SELL_SENTIMENT_THRESHOLD}. "
                f"Reason: {signal.get('reasoning', 'N/A')[:100]}"
            )
            print(f"  🔻 SENTIMENT SELL: {sig_ticker} ({sell_reason})")
            
            sid = trade_logger.log_decision({
                'ticker': sig_ticker, 'action': 'SELL', 'quantity': shares,
                'price': cp, 'sentiment_score': sent_score,
                'duration_score': signal.get('duration_score'),
                'weighted_score': sent_score,
                'decision_reason': sell_reason
            })
            orders.append({
                "ticker": sig_ticker, "action": "sell", "quantity": shares,
                "order_type": "limit", "limit_price": cp,
                "reason": sell_reason, "decision_id": sid
            })
            sold_tickers.append(sig_ticker)
            del current_holdings_data[sig_ticker]
            
            # Recalculate open slots
            num_positions = len([t for t, d in current_holdings_data.items() if d.get('qty', 0) > 0])
            open_slots = max(0, risk_scaled_slots - num_positions)
        

        candidates = []
        for signal in sentiment_data:
            if signal.get('action') != 'Buy':
                continue
            ticker = signal.get('ticker')
            if not ticker or not self.validate_ticker(ticker):
                trade_logger.log_decision({'ticker': ticker or 'UNKNOWN', 'action': 'SKIP', 'price': 0,
                    'decision_reason': 'SKIP: Not tradable on Alpaca'})
                continue
            
            bias = signal.get('sentiment_score', 0)
            if trade_logger.is_blacklisted(ticker, current_bias=bias):
                print(f"  🚫 {ticker}: 30-day blacklisted")
                trade_logger.log_decision({'ticker': ticker, 'action': 'SKIP', 'price': 0,
                    'sentiment_score': bias, 'decision_reason': 'SKIP: 30-day blacklist'})
                continue
            
            price = self.fetch_price(ticker)
            if not price:
                continue
            
            ohlc = self.fetch_history(ticker)
            atr = self.calculate_atr(ohlc, self.atr_period) if ohlc is not None else None
            rsi, sma_20, sma_50 = None, None, None
            if ohlc is not None:
                close_series = ohlc['close']
                rsi = self.calculate_rsi(close_series, 14)
                sma_20 = self.calculate_sma(close_series, 20)
                sma_50 = self.calculate_sma(close_series, 50)
            
            # P2: Volatility hard filter
            if atr and price and atr / price > self.max_volatility_pct:
                print(f"  🚫 {ticker}: Too volatile (ATR/Price={atr/price*100:.1f}%)")
                trade_logger.log_decision({'ticker': ticker, 'action': 'SKIP', 'price': price,
                    'sentiment_score': bias, 'atr_14': atr,
                    'decision_reason': f'SKIP: Volatility {atr/price*100:.1f}% > {self.max_volatility_pct*100:.0f}%'})
                continue
            
            # RSI filter
            if rsi is not None and (rsi > 75 or (rsi >= 65 and sma_20 and price <= sma_20)):
                print(f"  ⚠️ {ticker}: RSI overbought ({rsi:.1f})")
                trade_logger.log_decision({'ticker': ticker, 'action': 'SKIP', 'price': price,
                    'sentiment_score': bias, 'rsi_14': rsi,
                    'decision_reason': f'SKIP: RSI {rsi:.1f}'})
                continue
            
            # Downtrend filter (strong): price < SMA20 AND SMA20 < SMA50
            if sma_20 and sma_50 and price < sma_20 and sma_20 < sma_50:
                is_oversold = rsi is not None and rsi < 35
                if not is_oversold:
                    gap_pct = ((sma_20 - price) / sma_20) * 100
                    print(f"  🚫 {ticker}: Downtrend (gap {gap_pct:.1f}%)")
                    trade_logger.log_decision({'ticker': ticker, 'action': 'SKIP', 'price': price,
                        'sentiment_score': bias, 'rsi_14': rsi, 'sma_20': sma_20, 'sma_50': sma_50,
                        'decision_reason': f'SKIP: Downtrend gap {gap_pct:.1f}%'})
                    continue
            
            # [P1] Momentum filter: block entry if price significantly below SMA20
            # Even if SMA20 > SMA50 (not a full downtrend), being >2% below SMA20
            # indicates weak short-term momentum — most such entries lost money.
            if sma_20 and price < sma_20:
                gap_pct = ((sma_20 - price) / sma_20) * 100
                if gap_pct > config.MOMENTUM_GAP_TOLERANCE * 100:
                    print(f"  🚫 {ticker}: Below SMA20 by {gap_pct:.1f}% (tolerance: {config.MOMENTUM_GAP_TOLERANCE*100:.0f}%)")
                    trade_logger.log_decision({'ticker': ticker, 'action': 'SKIP', 'price': price,
                        'sentiment_score': bias, 'rsi_14': rsi, 'sma_20': sma_20,
                        'decision_reason': f'SKIP: Momentum filter — price {gap_pct:.1f}% below SMA20'})
                    continue
            
            score = self.calculate_weighted_score(bias, 0, atr or 0, price)
            
            # Min entry score filter (graduated by risk tier)
            if score < min_entry_score:
                print(f"  🚫 {ticker}: Score {score:.3f} < min {min_entry_score:.2f}")
                trade_logger.log_decision({'ticker': ticker, 'action': 'SKIP', 'price': price,
                    'sentiment_score': bias, 'weighted_score': score,
                    'decision_reason': f'SKIP: Score {score:.3f} < risk-tier min {min_entry_score:.2f}'})
                continue
            
            qty = self.calculate_position_size(atr or 0, price, self._env_bias)
            
            # Compute 5-day SMA for momentum filter (used in swap/replace decisions)
            sma_5 = self.calculate_sma(ohlc['close'], config.MOMENTUM_SMA_PERIOD) if ohlc is not None and len(ohlc) >= config.MOMENTUM_SMA_PERIOD else None
            
            candidates.append({
                'ticker': ticker, 'score': score, 'price': price, 'qty': qty,
                'bias': bias, 'atr': atr, 'rsi': rsi, 'sma_20': sma_20, 'sma_50': sma_50,
                'sma_5': sma_5, 'consensus': signal.get('consensus_level', 'Low')
            })
        
        # Deduplicate candidates by ticker BEFORE sorting — keep the strongest per ticker
        # Prevents duplicate BUY/SKIP decisions and distorted ranking pressure
        seen_tickers = {}
        consensus_weight = {'High': 3, 'Medium': 2, 'Low': 1, 'None': 0}
        for cand in candidates:
            t = cand['ticker']
            if t not in seen_tickers:
                seen_tickers[t] = cand
            else:
                existing = seen_tickers[t]
                if cand['score'] > existing['score']:
                    seen_tickers[t] = cand
                elif cand['score'] == existing['score']:
                    c_weight = consensus_weight.get(cand.get('consensus', 'Low'), 1)
                    e_weight = consensus_weight.get(existing.get('consensus', 'Low'), 1)
                    if c_weight > e_weight:
                        seen_tickers[t] = cand
                        
        if len(seen_tickers) < len(candidates):
            print(f"  🔀 Deduplicated candidates: {len(candidates)} → {len(seen_tickers)} (removed {len(candidates) - len(seen_tickers)} duplicates)")
        
        candidates = sorted(seen_tickers.values(), key=lambda x: x['score'], reverse=True)
        
        # ── 6. Score existing holdings ──
        holdings_scored = []
        for ticker, data in current_holdings_data.items():
            if data.get('qty', 0) <= 0 or ticker in sold_tickers:
                continue
            cp = data.get('current_price') or self.fetch_price(ticker)
            ae = data.get('avg_entry', cp)
            ret = (cp - ae) / ae if ae > 0 else 0
            ohlc = self.fetch_history(ticker)
            atr = self.calculate_atr(ohlc, self.atr_period) if ohlc is not None else None
            scores = trade_logger.get_latest_scores(ticker)
            sc = self.calculate_weighted_score(scores['sentiment'], ret, atr or 0, cp or 1)
            holdings_scored.append({'ticker': ticker, 'score': sc, 'qty': data['qty'], 'price': cp, 'avg_entry': ae})
        
        holdings_scored.sort(key=lambda x: x['score'])
        
        print(f"\n--- Candidates: {len(candidates)} | Holdings: {len(holdings_scored)} ---")
        for c in candidates[:5]:
            print(f"  📈 {c['ticker']}: Score={c['score']:.3f} Qty={c['qty']} ${c['price']:.2f}")
        for h in holdings_scored:
            ret_pct = ((h['price']-h['avg_entry'])/h['avg_entry']*100) if h['avg_entry'] > 0 else 0
            print(f"  📦 {h['ticker']}: Score={h['score']:.3f} Qty={int(h['qty'])} Ret={ret_pct:.1f}%")

        # ── 6.5 Strict Slot Enforcement (Purge Excess) ──
        # If we have more than risk_scaled_slots, sell the weakest ones until we are at risk_scaled_slots
        excess_slots = len(holdings_scored) - risk_scaled_slots
        if excess_slots > 0:
            print(f"\n  🧹 Slot Purge: {len(holdings_scored)} active > {risk_scaled_slots} risk-adjusted limit. Selling {excess_slots} weakest.")
            for i in range(excess_slots):
                weakest = holdings_scored.pop(0)  # Remove and get the lowest score
                sq = int(weakest['qty'])
                sid = trade_logger.log_decision({
                    'ticker': weakest['ticker'], 'action': 'SELL', 'quantity': sq,
                    'price': weakest['price'], 'weighted_score': weakest['score'],
                    'decision_reason': f'Slot Purge: Enforcing risk-scaled max {risk_scaled_slots} slots'})
                orders.append({"ticker": weakest['ticker'], "action": "sell", "quantity": sq,
                    "order_type": "limit", "limit_price": weakest['price'],
                    "reason": f"Slot limit enforced ({risk_scaled_slots})", "decision_id": sid})
                # Prevent this ticker from blocking buys later
                sold_tickers.append(weakest['ticker'])
                if weakest['ticker'] in current_holdings_data:
                    del current_holdings_data[weakest['ticker']]
                print(f"    ❌ Purged {weakest['ticker']} (Score: {weakest['score']:.3f})")
            open_slots = 0  # We are exactly at max slots now
        else:
            open_slots = risk_scaled_slots - len(holdings_scored)
        
        # sold_tickers is already maintained from risk sells + purge sells above
        bought_this_session = set()  # [BUG FIX] Prevent same ticker bought twice in one session
        
        # ── 7. Execute: Fill slots → Replacements ──
        for cand in candidates:
            ticker, price, qty, score = cand['ticker'], cand['price'], cand['qty'], cand['score']
            
            if ticker in sold_tickers:
                continue
            if ticker in bought_this_session:
                continue
            if ticker in current_holdings_data and current_holdings_data[ticker].get('qty', 0) > 0:
                continue
            
            # [BUG FIX] Pre-cap qty to slot budget BEFORE ordering
            slot_cap = (self.budget / self.max_slots) * self._env_bias
            max_affordable_qty = math.floor(slot_cap / price) if price > 0 else 0
            qty = min(qty, max_affordable_qty)
            
            # P4: Min order value (Scaled by env_bias to avoid blocking trades during low-bias periods)
            order_value = qty * price if qty > 0 else 0
            scaled_min_order_value = self.min_order_value * self._env_bias
            if qty <= 0 or order_value < scaled_min_order_value:
                print(f"  🚫 {ticker}: Order £{order_value:.0f} < min £{scaled_min_order_value:.0f}")
                trade_logger.log_decision({'ticker': ticker, 'action': 'SKIP', 'price': price,
                    'sentiment_score': cand['bias'], 'weighted_score': score,
                    'decision_reason': f'SKIP: Order £{order_value:.0f} < min £{scaled_min_order_value:.0f} (P4)'})
                continue
            
            # OPEN SLOT
            if open_slots > 0:
                reason = f"Slot Fill (Score {score:.3f}, {qty} shares)"
                did = trade_logger.log_decision({
                    'ticker': ticker, 'action': 'BUY', 'quantity': qty, 'price': price,
                    'sentiment_score': cand['bias'], 'rsi_14': cand.get('rsi'),
                    'sma_20': cand.get('sma_20'), 'sma_50': cand.get('sma_50'),
                    'atr_14': cand.get('atr'), 'decision_reason': reason, 'weighted_score': score
                })
                orders.append({"ticker": ticker, "action": "buy", "quantity": qty,
                    "order_type": "limit", "limit_price": price, "reason": reason, "decision_id": did})
                open_slots -= 1
                bought_this_session.add(ticker)
                current_holdings_data[ticker] = {'qty': qty, 'avg_entry': price, 'current_price': price}
                print(f"  ✅ BUY {qty} {ticker} @ ${price:.2f} [DB#{did}]")
                continue
            
            # ALL SLOTS FULL
            if not holdings_scored:
                break
            weakest = holdings_scored[0]
            ws = weakest['score']
            
            # Momentum filter: block swap/replace if target has negative 5-day momentum
            sma_5 = cand.get('sma_5')
            if sma_5 and price < sma_5:
                print(f"  🚫 {ticker}: Negative 5d momentum (${price:.2f} < SMA5 ${sma_5:.2f}) — swap/replace blocked")
                trade_logger.log_decision({'ticker': ticker, 'action': 'SKIP', 'price': price,
                    'sentiment_score': cand['bias'], 'weighted_score': score,
                    'decision_reason': f'SKIP: Negative 5d momentum ${price:.2f} < SMA5 ${sma_5:.2f}'})
                continue
            
            # [P3] Trend confirmation for swap/replace: target must be above SMA20
            # DeepSeek data: 5/5 Full Replace trades lost money — all entered weak trends
            cand_sma20 = cand.get('sma_20')
            if cand_sma20 and price < cand_sma20:
                gap_pct = ((cand_sma20 - price) / cand_sma20) * 100
                print(f"  🚫 {ticker}: Replace blocked — below SMA20 by {gap_pct:.1f}% (no uptrend confirmation)")
                trade_logger.log_decision({'ticker': ticker, 'action': 'SKIP', 'price': price,
                    'sentiment_score': cand['bias'], 'weighted_score': score,
                    'decision_reason': f'SKIP: Replace blocked — price {gap_pct:.1f}% below SMA20'})
                continue
            
            # P1: Full replacement (≥20%)
            if ws <= 0.01 or score >= ws * self.full_replace_threshold:
                print(f"  🔄 FULL REPLACE: {ticker}({score:.3f}) >> {weakest['ticker']}({ws:.3f})")
                sq = int(weakest['qty'])
                sid = trade_logger.log_decision({
                    'ticker': weakest['ticker'], 'action': 'SELL', 'quantity': sq,
                    'price': weakest['price'], 'weighted_score': ws,
                    'decision_reason': f'Full Replace by {ticker} ({ws:.3f}→{score:.3f})'})
                orders.append({"ticker": weakest['ticker'], "action": "sell", "quantity": sq,
                    "order_type": "limit", "limit_price": weakest['price'],
                    "reason": f"Full Replace by {ticker}", "decision_id": sid})
                sold_tickers.append(weakest['ticker'])
                
                bid = trade_logger.log_decision({
                    'ticker': ticker, 'action': 'BUY', 'quantity': qty, 'price': price,
                    'sentiment_score': cand['bias'], 'atr_14': cand.get('atr'),
                    'decision_reason': f'Full Replace of {weakest["ticker"]}', 'weighted_score': score})
                orders.append({"ticker": ticker, "action": "buy", "quantity": qty,
                    "order_type": "limit", "limit_price": price,
                    "reason": f"Full Replace of {weakest['ticker']}", "decision_id": bid,
                    "paired_sell_ticker": weakest['ticker']})
                holdings_scored.pop(0)
                bought_this_session.add(ticker)
                current_holdings_data[ticker] = {'qty': qty, 'avg_entry': price, 'current_price': price}
                print(f"  ✅ Sell {sq} {weakest['ticker']} → Buy {qty} {ticker}")
                continue
            
            print(f"  ⏭️ {ticker}: Score {score:.3f} < threshold for {weakest['ticker']} ({ws:.3f})")
            trade_logger.log_decision({'ticker': ticker, 'action': 'SKIP', 'price': price,
                'sentiment_score': cand['bias'], 'weighted_score': score,
                'decision_reason': f'SKIP: Below replacement threshold'})

        return orders

def main():
    try:
        with open('sentiment_data.json', 'r') as f:
            raw_data = json.load(f)
    except FileNotFoundError as e:
        print(f"Error loading sentiment_data.json: {e}")
        # Write sentinel record so every session has at least one DB entry
        trade_logger.init_db()
        trade_logger.log_decision({
            'ticker': 'SYSTEM', 'action': 'NO_DATA', 'price': 0,
            'decision_reason': f'Pipeline skipped: {e}'
        })
        return
    
    # Staleness check: reject data older than SENTIMENT_MAX_AGE_DAYS
    try:
        file_mtime = datetime.fromtimestamp(os.path.getmtime('sentiment_data.json'))
        data_age_days = (datetime.now() - file_mtime).days
        if data_age_days > config.SENTIMENT_MAX_AGE_DAYS:
            print(f"  ⚠️ sentiment_data.json is {data_age_days} days old (max: {config.SENTIMENT_MAX_AGE_DAYS})")
            print(f"  🔄 Stale data — run market_brain.py first for fresh signals.")
            trade_logger.init_db()
            trade_logger.log_decision({
                'ticker': 'SYSTEM', 'action': 'STALE_DATA', 'price': 0,
                'decision_reason': f'Sentiment data {data_age_days}d old > max {config.SENTIMENT_MAX_AGE_DAYS}d. Skipping.'
            })
            return
        print(f"  ✅ Sentiment data age: {data_age_days}d (max: {config.SENTIMENT_MAX_AGE_DAYS}d)")
    except Exception as e:
        print(f"  ⚠️ Could not check data age: {e}")

    # current_portfolio.json is OPTIONAL — Alpaca API is the primary source
    # for live positions. This file is only a fallback for offline/mock mode.
    try:
        with open('current_portfolio.json', 'r') as f:
            portfolio = json.load(f)
    except FileNotFoundError:
        print("  ℹ️ current_portfolio.json not found — using Alpaca API for positions (normal).")
        portfolio = {"positions": {}}
    
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

    engine = TradingLogic()

    plan = engine.generate_plan(sentiment_data, portfolio, env_bias=env_bias, macro_reason=macro_reason)

    output_file = 'execution_plan.json'
    with open(output_file, 'w') as f:
        json.dump(plan, f, indent=4)
        
    print(f"\nExecution Plan Saved to {output_file} ({len(plan)} orders)")

if __name__ == "__main__":
    main()
