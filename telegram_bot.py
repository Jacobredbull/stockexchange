"""
stockexchange_V0.4 — Telegram Bot (Monitoring & Alerts)

Functions:
  send_summary()   — Post-run session report (Bias, P/L, Gainers/Losers, Shadow Alerts)
  send_heartbeat() — Monday morning system-alive confirmation
  send_backup()    — Friday: send trade_history.db as private file
  send_alert()     — General-purpose alert (errors, defense mode)
"""

import os
import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

import pytz
import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("telegram_bot")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

DB_FILE = Path(__file__).parent / "data" / "trade_history.db"
SENTIMENT_FILE = Path(__file__).parent / "sentiment_data.json"
PLAN_FILE = Path(__file__).parent / "execution_plan.json"

TZ_NY = pytz.timezone("America/New_York")
TZ_LONDON = pytz.timezone("Europe/London")


def _is_configured() -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("Telegram not configured (BOT_TOKEN or CHAT_ID missing). Skipping.")
        return False
    return True


# ---------------------------------------------------------------------------
# Low-level senders
# ---------------------------------------------------------------------------
def _send_message(text: str, parse_mode: str = "Markdown"):
    """Send a text message via Telegram Bot API."""
    if not _is_configured():
        return None
    try:
        payload = {"chat_id": CHAT_ID, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        resp = requests.post(
            f"{BASE_URL}/sendMessage",
            json=payload,
            timeout=15,
        )
        if resp.status_code != 200:
            log.warning(f"Telegram API error: {resp.status_code} — {resp.text[:200]}")
        return resp
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")
        return None


def _send_document(file_path: Path, caption: str = ""):
    """Send a file via Telegram Bot API."""
    if not _is_configured():
        return None
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                f"{BASE_URL}/sendDocument",
                data={"chat_id": CHAT_ID, "caption": caption},
                files={"document": (file_path.name, f)},
                timeout=30,
            )
        if resp.status_code != 200:
            log.warning(f"Telegram file send error: {resp.status_code}")
        return resp
    except Exception as e:
        log.warning(f"Telegram file send failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Portfolio helpers
# ---------------------------------------------------------------------------
def _get_portfolio_summary() -> dict:
    """Query Alpaca for current positions and P/L."""
    try:
        from alpaca.trading.client import TradingClient
        api_key = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        client = TradingClient(api_key, secret_key, paper=True)
        account = client.get_account()
        positions = client.get_all_positions()

        holdings = []
        for p in positions:
            pnl_pct = float(p.unrealized_plpc) * 100
            holdings.append({
                "ticker": p.symbol,
                "qty": float(p.qty),
                "entry": float(p.avg_entry_price),
                "current": float(p.current_price),
                "pnl_pct": round(pnl_pct, 2),
                "market_value": float(p.market_value),
            })

        holdings.sort(key=lambda x: x["pnl_pct"], reverse=True)

        return {
            "equity": float(account.equity),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "total_pnl": sum(h["pnl_pct"] for h in holdings),
            "holdings": holdings,
        }
    except Exception as e:
        log.warning(f"Portfolio fetch failed: {e}")
        return {"equity": 0, "cash": 0, "buying_power": 0, "total_pnl": 0, "holdings": []}


def _get_macro_data() -> dict:
    """Read latest sentiment_data.json for macro context."""
    try:
        with open(SENTIMENT_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict) and "global_env_bias" in data:
            return {
                "env_bias": data.get("global_env_bias", 1.0),
                "macro_reason": data.get("macro_reason", "N/A"),
                "shadows": [
                    s for s in data.get("signals", [])
                    if s.get("source") == "shadow_link"
                ],
                "signal_count": len(data.get("signals", [])),
            }
        return {"env_bias": 1.0, "macro_reason": "Legacy format", "shadows": [], "signal_count": 0}
    except Exception as e:
        log.warning(f"Macro data read failed: {e}")
        return {"env_bias": 1.0, "macro_reason": "Unavailable", "shadows": [], "signal_count": 0}


def _get_execution_summary() -> dict:
    """Read actual filled orders from the database for the last 12 hours."""
    try:
        from datetime import timedelta
        db_path = Path(__file__).parent / "data" / "trade_history.db"
        if not db_path.exists():
            return {"total": 0, "buys": 0, "sells": 0, "orders": []}
            
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        
        # Get filled orders from the last 12 hours
        cutoff = (datetime.now() - timedelta(hours=12)).isoformat()
        
        c.execute('''
            SELECT ticker, action, filled_qty, filled_price 
            FROM history 
            WHERE execution_status IN ('filled', 'partial_fill') AND timestamp > ?
        ''', (cutoff,))
        rows = c.fetchall()
        conn.close()
        
        buys = [r for r in rows if r[1] and r[1].upper() == 'BUY']
        sells = [r for r in rows if r[1] and r[1].upper() == 'SELL']
        return {"total": len(rows), "buys": len(buys), "sells": len(sells), "orders": rows}
    except Exception as e:
        log.warning(f"Failed to fetch execution summary: {e}")
        return {"total": 0, "buys": 0, "sells": 0, "orders": []}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def send_summary(session_name: str = "Session", success: bool = True):
    """Send a formatted Markdown summary after a trading session."""
    now_ny = datetime.now(TZ_NY).strftime("%Y-%m-%d %H:%M ET")
    now_ldn = datetime.now(TZ_LONDON).strftime("%H:%M GMT")

    macro = _get_macro_data()
    portfolio = _get_portfolio_summary()
    execution = _get_execution_summary()

    # Env bias emoji
    bias = macro["env_bias"]
    if bias >= 0.8:
        bias_icon = "🟢"
    elif bias >= 0.5:
        bias_icon = "🟡"
    else:
        bias_icon = "🔴"

    status_icon = "✅" if success else "❌"

    # Build message
    lines = [
        f"{status_icon} *stockexchange\\_V0.4 — {session_name}*",
        f"📅 {now_ny} ({now_ldn})",
        "",
        f"*🌍 Macro Environment*",
        f"  {bias_icon} Bias: `{bias}` — {macro['macro_reason'][:80]}",
        f"  📊 Signals: {macro['signal_count']}",
        "",
        f"*💰 Portfolio*",
        f"  Equity: `${portfolio['equity']:,.2f}`",
        f"  Cash: `${portfolio['cash']:,.2f}`",
    ]

    # Top Gainers / Losers
    holdings = portfolio["holdings"]
    if holdings:
        gainers = [h for h in holdings if h["pnl_pct"] > 0]
        losers = [h for h in holdings if h["pnl_pct"] < 0]

        if gainers:
            top = gainers[:3]
            lines.append("")
            lines.append("*📈 Top Gainers*")
            for h in top:
                lines.append(f"  `{h['ticker']}` +{h['pnl_pct']:.1f}% (${h['current']:.2f})")

        if losers:
            bottom = losers[-3:]
            lines.append("")
            lines.append("*📉 Top Losers*")
            for h in bottom:
                lines.append(f"  `{h['ticker']}` {h['pnl_pct']:.1f}% (${h['current']:.2f})")

    # Shadow tickers
    if macro["shadows"]:
        lines.append("")
        lines.append("*👤 Shadow Ticker Alerts*")
        for s in macro["shadows"][:5]:
            lines.append(
                f"  🔗 `{s.get('ticker', '?')}` "
                f"({s.get('sentiment_score', 0)}) — "
                f"{s.get('reasoning', '')[:60]}"
            )

    # Execution
    lines.append("")
    lines.append(f"*📦 Execution*")
    lines.append(f"  Orders: {execution['total']} (Buy: {execution['buys']}, Sell: {execution['sells']})")

    # Risk Scaling Warning
    if bias == 0.0:
        lines.append("")
        lines.append("🚨 *SAFE HOLD MODE ACTIVE* — All buys frozen, ATR tightened 50%")
    elif bias < 0.3:
        lines.append("")
        lines.append("🔴 *CRITICAL RISK* — Max Slots: 1, Min Score: 0.70, ATR tightened 50%")
    elif bias < 0.5:
        lines.append("")
        lines.append("🟠 *ELEVATED RISK* — Max Slots: 2, Min Score: 0.60, ATR tightened 30%")
    elif bias < 0.8:
        lines.append("")
        lines.append("🟡 *CAUTIOUS RISK* — Max Slots: 3, Min Score: 0.50")

    text = "\n".join(lines)
    _send_message(text)
    log.info(f"Telegram summary sent for {session_name}")


def send_heartbeat():
    """Monday morning heartbeat — confirm system is alive."""
    now_ny = datetime.now(TZ_NY).strftime("%Y-%m-%d %H:%M ET")
    now_ldn = datetime.now(TZ_LONDON).strftime("%H:%M GMT")

    try:
        portfolio = _get_portfolio_summary()
        equity_str = f"${portfolio['equity']:,.2f}"
        holdings_count = len(portfolio["holdings"])
    except Exception:
        equity_str = "unavailable"
        holdings_count = 0

    text = (
        f"💓 *stockexchange\\_V0.4 — Heartbeat*\n"
        f"📅 Monday {now_ny} ({now_ldn})\n"
        f"\n"
        f"🤖 System: `ONLINE`\n"
        f"💰 Equity: `{equity_str}`\n"
        f"📊 Positions: `{holdings_count}`\n"
        f"\n"
        f"_Next session at market open + 15min._"
    )
    _send_message(text)
    log.info("Heartbeat sent.")


def send_backup():
    """
    Send trade_history.db as a file via Telegram.
    Called every Friday after market close for disaster recovery.
    """
    if not DB_FILE.exists():
        log.warning(f"DB file not found: {DB_FILE}")
        send_alert("⚠️ Friday backup skipped — trade_history.db not found")
        return

    now_ny = datetime.now(TZ_NY).strftime("%Y-%m-%d %H:%M ET")
    caption = f"📦 Weekly DB Backup — {now_ny}"
    _send_document(DB_FILE, caption=caption)
    log.info("DB backup sent via Telegram.")


def send_alert(message: str):
    """General purpose alert (errors, defense mode triggers, etc.)."""
    text = f"🚨 *stockexchange\\_V0\.4 Alert*\n\n{message}"
    _send_message(text)

def send_emergency_alert(message: str):
    """High-priority emergency alert — sent as plain text to avoid Markdown parse errors."""
    text = f"\u203c\ufe0f stockexchange_V0.4 \u2014 EMERGENCY\n\n{message}"
    _send_message(text, parse_mode=None)


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("--- Telegram Bot Test ---")
    if not _is_configured():
        print("❌ Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env first.")
        print("   1. Talk to @BotFather on Telegram → /newbot")
        print("   2. Copy the token to .env")
        print("   3. Send a message to your bot, then visit:")
        print(f"      https://api.telegram.org/bot<TOKEN>/getUpdates")
        print("   4. Copy your chat_id to .env")
    else:
        print(f"Token: {BOT_TOKEN[:8]}...{BOT_TOKEN[-4:]}")
        print(f"Chat ID: {CHAT_ID}")
        send_heartbeat()
        print("✅ Heartbeat sent. Check Telegram.")
