import glob
import os
import shutil
import sqlite3
import datetime

DB_DIR = 'data'
if not os.path.exists(DB_DIR):
    os.makedirs(DB_DIR)
DB_FILE = os.path.join(DB_DIR, 'trade_history.db')

# --- All new columns to migrate safely ---
_MIGRATIONS = [
    'duration_score REAL',
    'order_id TEXT',
    'execution_status TEXT',
    'filled_price REAL',
    'filled_qty REAL',
    'filled_at TEXT',
    'price_after_7d REAL',
    'price_after_14d REAL',
    'outcome_pnl_pct REAL',
    'decision_grade TEXT',
    'ai_feedback TEXT',
    # v2: ATR / Trailing / Whipsaw columns
    'atr_14 REAL',
    'sma_50 REAL',
    'high_water_mark REAL',
    # v3: Macro-Environmental Observer columns
    'env_bias REAL',
    'macro_reason TEXT',
]

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            ticker TEXT,
            action TEXT,
            quantity INTEGER,
            price REAL,
            sentiment_score REAL,
            duration_score REAL,
            sentiment_reason TEXT,
            rsi_14 REAL,
            sma_20 REAL,
            decision_reason TEXT,
            entry_price REAL,
            exit_price REAL,
            pnl REAL,
            pnl_percent REAL,
            order_id TEXT,
            execution_status TEXT,
            filled_price REAL,
            filled_qty REAL,
            filled_at TEXT,
            price_after_7d REAL,
            price_after_14d REAL,
            outcome_pnl_pct REAL,
            decision_grade TEXT,
            ai_feedback TEXT,
            atr_14 REAL,
            sma_50 REAL,
            high_water_mark REAL,
            env_bias REAL,
            macro_reason TEXT
        )
    ''')
    
    # Safe migration: add columns if old schema lacks them
    for col_def in _MIGRATIONS:
        try:
            c.execute(f'ALTER TABLE history ADD COLUMN {col_def}')
        except sqlite3.OperationalError:
            pass  # Column already exists
    
    conn.commit()
    conn.close()


def backup_db(keep_last=7):
    """
    Creates a timestamped backup of the trade database in data/backups/.
    Automatically removes old backups, keeping only the `keep_last` most recent.
    Call this at the end of each trading session.
    """
    backup_dir = os.path.join(DB_DIR, 'backups')
    os.makedirs(backup_dir, exist_ok=True)

    if not os.path.exists(DB_FILE):
        print("⚠️  DB backup skipped: database file not found.")
        return

    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(backup_dir, f'trade_history_{timestamp}.db')
    shutil.copy2(DB_FILE, backup_path)
    print(f"💾  DB backed up → {backup_path}")

    # Prune old backups, keep only the most recent `keep_last`
    all_backups = sorted(glob.glob(os.path.join(backup_dir, 'trade_history_*.db')))
    to_delete = all_backups[:-keep_last] if len(all_backups) > keep_last else []
    for old in to_delete:
        os.remove(old)
        print(f"   🗑️  Removed old backup: {os.path.basename(old)}")


def log_decision(decision_data):
    """Logs a decision from logic_engine.py. Returns the row ID."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''
        INSERT INTO history (
            timestamp, ticker, action, quantity, price, 
            sentiment_score, duration_score, sentiment_reason, rsi_14, sma_20, decision_reason,
            entry_price, exit_price, pnl, pnl_percent,
            atr_14, sma_50, high_water_mark,
            env_bias, macro_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        datetime.datetime.now().isoformat(),
        decision_data.get('ticker'),
        decision_data.get('action'),
        decision_data.get('quantity', 0),
        decision_data.get('price'),
        decision_data.get('sentiment_score'),
        decision_data.get('duration_score', 0.0),
        decision_data.get('sentiment_reason', ''),
        decision_data.get('rsi_14'),
        decision_data.get('sma_20'),
        decision_data.get('decision_reason', ''),
        decision_data.get('entry_price'),
        decision_data.get('exit_price'),
        decision_data.get('pnl'),
        decision_data.get('pnl_percent'),
        decision_data.get('atr_14'),
        decision_data.get('sma_50'),
        decision_data.get('high_water_mark'),
        decision_data.get('env_bias'),
        decision_data.get('macro_reason')
    ))
    
    row_id = c.lastrowid
    conn.commit()
    conn.close()
    return row_id


def update_execution(decision_id, order_id, status, filled_price=None, filled_qty=None, filled_at=None):
    """Called by trader.py after submitting/polling an order."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''
        UPDATE history SET
            order_id = ?,
            execution_status = ?,
            filled_price = ?,
            filled_qty = ?,
            filled_at = ?
        WHERE id = ?
    ''', (order_id, status, filled_price, filled_qty, filled_at, decision_id))
    
    conn.commit()
    conn.close()


def update_outcome(decision_id, price_7d, price_14d, outcome_pnl_pct):
    """Called by outcome_tracker.py with ground-truth results."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''
        UPDATE history SET
            price_after_7d = ?,
            price_after_14d = ?,
            outcome_pnl_pct = ?
        WHERE id = ?
    ''', (price_7d, price_14d, outcome_pnl_pct, decision_id))
    
    conn.commit()
    conn.close()


def save_ai_review(decision_id, grade, feedback):
    """Called by strategy_reviewer.py to persist AI analysis."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''
        UPDATE history SET
            decision_grade = ?,
            ai_feedback = ?
        WHERE id = ?
    ''', (grade, feedback, decision_id))
    
    conn.commit()
    conn.close()


def get_latest_scores(ticker):
    """Retrieves the most recent (Sentiment, Duration) scores for a ticker."""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        c.execute('''
            SELECT sentiment_score, duration_score FROM history 
            WHERE ticker = ? AND sentiment_score IS NOT NULL 
            ORDER BY timestamp DESC LIMIT 1
        ''', (ticker,))
        
        result = c.fetchone()
        conn.close()
        
        if result:
            return {'sentiment': result[0], 'duration': result[1] if result[1] is not None else 0.5}
        return {'sentiment': 0.0, 'duration': 0.0}
    except Exception as e:
        print(f"Error fetching scores for {ticker}: {e}")
        return {'sentiment': 0.0, 'duration': 0.0}


def get_pending_outcomes(days_threshold=14):
    """Returns BUY decisions older than `days_threshold` days that need outcome tracking."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=days_threshold)).isoformat()
    
    c.execute('''
        SELECT id, ticker, timestamp, filled_price, filled_at FROM history
        WHERE action = 'BUY' 
          AND execution_status = 'filled'
          AND price_after_14d IS NULL
          AND timestamp < ?
        ORDER BY timestamp ASC
    ''', (cutoff,))
    
    rows = c.fetchall()
    conn.close()
    
    return [{'id': r[0], 'ticker': r[1], 'timestamp': r[2], 
             'filled_price': r[3], 'filled_at': r[4]} for r in rows]


def get_decisions_for_review():
    """Returns completed trades with 14-day outcomes for AI analysis."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''
        SELECT id, ticker, action, quantity, price, filled_price,
               sentiment_score, duration_score, decision_reason,
               price_after_7d, price_after_14d, outcome_pnl_pct,
               decision_grade, timestamp
        FROM history
        WHERE action IN ('BUY', 'SELL')
          AND execution_status = 'filled'
          AND price_after_14d IS NOT NULL
          AND (decision_grade IS NULL OR decision_grade = '')
        ORDER BY timestamp ASC
    ''')
    
    rows = c.fetchall()
    conn.close()
    
    return [{
        'id': r[0], 'ticker': r[1], 'action': r[2], 'quantity': r[3],
        'decision_price': r[4], 'filled_price': r[5],
        'sentiment_score': r[6], 'duration_score': r[7], 'decision_reason': r[8],
        'price_after_7d': r[9], 'price_after_14d': r[10], 'outcome_pnl_pct': r[11],
        'decision_grade': r[12], 'timestamp': r[13]
    } for r in rows]


def is_on_cooldown(ticker, hours=4):
    """Returns True if this ticker was traded (BUY/SELL) in the last `hours` hours."""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        cutoff = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
        
        c.execute('''
            SELECT COUNT(*) FROM history
            WHERE ticker = ? 
              AND action = 'BUY'
              AND timestamp > ?
              AND (execution_status IS NULL OR execution_status != 'rejected')
        ''', (ticker, cutoff))
        
        count = c.fetchone()[0]
        conn.close()
        return count > 0
    except Exception as e:
        print(f"Error checking cooldown for {ticker}: {e}")
        return False


def get_last_buy_time(ticker):
    """Returns the datetime of the most recent BUY for this ticker, or None."""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        c.execute('''
            SELECT timestamp FROM history
            WHERE ticker = ? AND action = 'BUY'
            ORDER BY timestamp DESC LIMIT 1
        ''', (ticker,))
        
        result = c.fetchone()
        conn.close()
        
        if result and result[0]:
            ts = result[0]
            if 'T' in str(ts):
                return datetime.datetime.fromisoformat(str(ts))
            else:
                return datetime.datetime.strptime(str(ts)[:19], '%Y-%m-%d %H:%M:%S')
        return None
    except Exception as e:
        print(f"Error fetching last buy time for {ticker}: {e}")
        return None


if __name__ == '__main__':
    init_db()
    print(f"Database initialized: {DB_FILE}")
