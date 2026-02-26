import sqlite3
from trade_logger import DB_FILE

def view_logs(limit=5):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    try:
        c.execute('''
            SELECT id, timestamp, ticker, action, quantity, price, decision_reason 
            FROM history 
            ORDER BY id DESC 
            LIMIT ?
        ''', (limit,))
        
        rows = c.fetchall()
        
        if not rows:
            print("No logs found.")
            return

        print(f"\n--- Last {len(rows)} Decisions ---")
        for row in rows:
            print(f"[{row[1]}] {row[3]} {row[2]} (Qty: {row[4]} @ ${row[5]})", flush=True)
            print(f"   Reason: {row[6]}", flush=True)
            print("-" * 40, flush=True)
            
    except sqlite3.Error as e:
        print(f"Error accessing database: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    view_logs()
