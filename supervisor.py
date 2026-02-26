"""
stockexchange_V0.1 — Supervisor (NYSE-Aware Scheduler)

Handles two trading sessions per day:
  Session 1 (Morning Guard):  market_open  + 15 min
  Session 2 (Closing Sprint): market_close - 30 min

Extras:
  - Monday heartbeat (09:00 ET)
  - Friday DB backup after close
  - File-based heartbeat for Docker healthcheck
  - Full DST-safe timezone handling via pytz
"""

import os
import sys
import time
import json
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import pytz
import pandas_market_calendars as mcal
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "supervisor.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("supervisor")

# ---------------------------------------------------------------------------
# Timezones
# ---------------------------------------------------------------------------
TZ_NY = pytz.timezone("America/New_York")
TZ_LONDON = pytz.timezone("Europe/London")
TZ_UTC = pytz.utc

# ---------------------------------------------------------------------------
# Heartbeat (file-based, for Docker healthcheck)
# ---------------------------------------------------------------------------
HEARTBEAT_FILE = Path(__file__).parent / "logs" / ".heartbeat"


def write_heartbeat():
    """Touch the heartbeat file so Docker healthcheck can verify liveness."""
    try:
        HEARTBEAT_FILE.write_text(datetime.now(TZ_UTC).isoformat())
        os.chmod(HEARTBEAT_FILE, 0o666)  # Ensure readable by Docker engine
    except Exception as e:
        log.warning(f"Heartbeat write failed: {e}")


def check_heartbeat(max_age_seconds: int = 300) -> bool:
    """Return True if heartbeat was updated within max_age_seconds."""
    try:
        if not HEARTBEAT_FILE.exists():
            return False
        last = datetime.fromisoformat(HEARTBEAT_FILE.read_text().strip())
        if last.tzinfo is None:
            last = TZ_UTC.localize(last)
        age = (datetime.now(TZ_UTC) - last).total_seconds()
        return age < max_age_seconds
    except Exception:
        return False


# ---------------------------------------------------------------------------
# NYSE Calendar helpers
# ---------------------------------------------------------------------------
NYSE = mcal.get_calendar("NYSE")


def get_today_schedule():
    """
    Return (market_open, market_close) as tz-aware datetime in ET,
    or (None, None) if today is not a trading day.
    """
    now_et = datetime.now(TZ_NY)
    today_str = now_et.strftime("%Y-%m-%d")
    schedule = NYSE.schedule(start_date=today_str, end_date=today_str)

    if schedule.empty:
        return None, None

    market_open_utc = schedule.iloc[0]["market_open"].to_pydatetime()
    market_close_utc = schedule.iloc[0]["market_close"].to_pydatetime()

    # Convert to ET for human-readable logging
    market_open_et = market_open_utc.astimezone(TZ_NY)
    market_close_et = market_close_utc.astimezone(TZ_NY)

    return market_open_et, market_close_et


def next_trading_day():
    """Return the next trading day's date as a string YYYY-MM-DD."""
    now_et = datetime.now(TZ_NY)
    future = now_et + timedelta(days=1)
    # Look up to 7 days ahead to skip weekends + holidays
    for _ in range(7):
        check = future.strftime("%Y-%m-%d")
        sched = NYSE.schedule(start_date=check, end_date=check)
        if not sched.empty:
            return check
        future += timedelta(days=1)
    return (now_et + timedelta(days=1)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------
def run_pipeline(session_name: str):
    """Run the full trading pipeline: brain → engine → trader."""
    log.info(f"{'='*50}")
    log.info(f"PIPELINE START — {session_name}")
    log.info(f"{'='*50}")

    try:
        # Stage 1: Market Brain (sentiment + macro sentinel)
        log.info("Stage 1: market_brain.py ...")
        import market_brain
        market_brain.main()

        # Stage 2: Logic Engine (gravity-adjusted plan)
        log.info("Stage 2: logic_engine.py ...")
        import logic_engine
        logic_engine.main()

        # Stage 3: Trader (Alpaca execution)
        log.info("Stage 3: trader.py ...")
        import trader
        trader.execute_trades()

        log.info(f"PIPELINE COMPLETE — {session_name}")
        return True

    except Exception as e:
        log.error(f"PIPELINE FAILED — {session_name}: {e}", exc_info=True)
        return False


def post_session_telegram(session_name: str, success: bool):
    """Send Telegram summary after a session run."""
    try:
        import telegram_bot
        telegram_bot.send_summary(session_name, success)
    except Exception as e:
        log.warning(f"Telegram summary failed: {e}")


def friday_backup():
    """Trigger DB backup on Fridays after market close."""
    try:
        import telegram_bot
        telegram_bot.send_backup()
        log.info("Friday DB backup sent via Telegram.")
    except Exception as e:
        log.warning(f"Friday backup failed: {e}")


def monday_heartbeat():
    """Send Monday morning heartbeat via Telegram."""
    try:
        import telegram_bot
        telegram_bot.send_heartbeat()
        log.info("Monday heartbeat sent.")
    except Exception as e:
        log.warning(f"Monday heartbeat failed: {e}")


# ---------------------------------------------------------------------------
# Sleep helper
# ---------------------------------------------------------------------------
def sleep_until(target: datetime, label: str = ""):
    """
    Sleep until the target datetime, writing heartbeat every 60s.
    Handles DST transitions via pytz-aware datetimes.
    """
    while True:
        now = datetime.now(TZ_NY)
        delta = (target - now).total_seconds()
        if delta <= 0:
            break
        # Log every 15 minutes
        if int(delta) % 900 == 0:
            log.info(f"Sleeping {delta/60:.0f}min until {label} ({target.strftime('%H:%M ET')})")
        write_heartbeat()
        time.sleep(min(delta, 60))  # Wake every 60s for heartbeat


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def check_db_integrity():
    """Verify trade_history.db exists and is accessible."""
    db_path = Path(__file__).parent / "data" / "trade_history.db"
    try:
        import sqlite3
        if not db_path.exists():
            log.warning(f"DB not found at {db_path} - will be created on first run.")
            return True
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("PRAGMA integrity_check;")
        result = c.fetchone()[0]
        conn.close()
        if result == "ok":
            log.info("✅ Database integrity check passed.")
            return True
        else:
            log.error(f"❌ Database integrity check failed: {result}")
            return False
    except Exception as e:
        log.error(f"❌ DB Check Error: {e}")
        return False


def main(dry_run: bool = False):
    log.info("=" * 60)
    log.info("stockexchange_V0.1 Supervisor — Starting")
    check_db_integrity()
    log.info(f"  Timezone: {TZ_NY} | DST active: {bool(datetime.now(TZ_NY).dst())}")
    log.info("=" * 60)

    while True:
        write_heartbeat()
        now_et = datetime.now(TZ_NY)

        # --- Monday Heartbeat (09:00 ET) ---
        if now_et.weekday() == 0 and now_et.hour == 9 and now_et.minute < 5:
            if not dry_run:
                monday_heartbeat()

        # --- Get today's schedule ---
        market_open, market_close = get_today_schedule()

        if market_open is None:
            next_day = next_trading_day()
            log.info(f"Market closed today ({now_et.strftime('%A %Y-%m-%d')}). "
                     f"Next trading day: {next_day}")
            # Sleep until 09:00 ET next trading day
            next_open = TZ_NY.localize(
                datetime.strptime(next_day, "%Y-%m-%d").replace(hour=9, minute=0)
            )
            sleep_until(next_open, f"next open ({next_day})")
            continue

        # Session times
        morning_guard = market_open + timedelta(minutes=15)
        closing_sprint = market_close - timedelta(minutes=30)

        log.info(f"Today: {now_et.strftime('%A %Y-%m-%d')}")
        log.info(f"  Market Open:     {market_open.strftime('%H:%M ET')}")
        log.info(f"  Morning Guard:   {morning_guard.strftime('%H:%M ET')}")
        log.info(f"  Closing Sprint:  {closing_sprint.strftime('%H:%M ET')}")
        log.info(f"  Market Close:    {market_close.strftime('%H:%M ET')}")

        # --- Session 1: Morning Guard ---
        if now_et < morning_guard:
            sleep_until(morning_guard, "Morning Guard")
            if not dry_run:
                log.info("🌅 SESSION 1: Morning Guard")
                success = run_pipeline("Morning Guard")
                post_session_telegram("Morning Guard", success)
            else:
                log.info("[DRY RUN] Would run Morning Guard pipeline")

        # --- Session 2: Closing Sprint ---
        if datetime.now(TZ_NY) < closing_sprint:
            sleep_until(closing_sprint, "Closing Sprint")
            if not dry_run:
                log.info("🌆 SESSION 2: Closing Sprint")
                success = run_pipeline("Closing Sprint")
                post_session_telegram("Closing Sprint", success)
            else:
                log.info("[DRY RUN] Would run Closing Sprint pipeline")

        # --- Friday DB Backup (after close) ---
        if datetime.now(TZ_NY).weekday() == 4:  # Friday
            if not dry_run:
                friday_backup()
            else:
                log.info("[DRY RUN] Would send Friday DB backup")

        # --- Sleep until next trading day ---
        next_day = next_trading_day()
        next_open = TZ_NY.localize(
            datetime.strptime(next_day, "%Y-%m-%d").replace(hour=9, minute=0)
        )
        log.info(f"Day complete. Next session: {next_day} 09:00 ET")
        sleep_until(next_open, f"next open ({next_day})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="stockexchange_V0.1 Supervisor")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print schedule without executing pipeline")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
