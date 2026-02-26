import streamlit as st
import sqlite3
import pandas as pd
from trade_logger import DB_FILE

# Page Config
st.set_page_config(page_title="AI Trading Brain - Dashboard", layout="wide")

st.title("🧠 AI Trading Brain - Decision Log")

# Setup Database Connection
@st.cache_data(ttl=5) # Refresh every 5 seconds
def load_data():
    conn = sqlite3.connect(DB_FILE)
    query = "SELECT * FROM history ORDER BY id DESC"
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

# Load Data
try:
    df = load_data()
except Exception as e:
    st.error(f"Error loading database: {e}")
    st.stop()

if df.empty:
    st.warning("No trade history found yet. Run the logic engine to generate logs.")
    st.stop()

# Sidebar Filters
st.sidebar.header("Filters")

# Ticker Filter
tickers = ["All"] + list(df['ticker'].unique())
selected_ticker = st.sidebar.selectbox("Select Ticker", tickers)

# Action Filter
actions = ["All"] + list(df['action'].unique())
selected_action = st.sidebar.selectbox("Select Action", actions)

# Apply Filters
filtered_df = df.copy()
if selected_ticker != "All":
    filtered_df = filtered_df[filtered_df['ticker'] == selected_ticker]
if selected_action != "All":
    filtered_df = filtered_df[filtered_df['action'] == selected_action]

# Metrics
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Decisions", len(filtered_df))
col2.metric("Buys", len(filtered_df[filtered_df['action'] == 'BUY']))
col3.metric("Sells", len(filtered_df[filtered_df['action'] == 'SELL']))

# Calculate Realized P&L
realized_pnl = filtered_df['pnl'].sum()
col4.metric("Realized P&L", f"${realized_pnl:,.2f}", delta_color="normal")

# Main Table
st.subheader("Recent Decisions")

def style_pnl(val):
    color = 'red' if val < 0 else 'green'
    return f'color: {color}'

st.dataframe(
    filtered_df,
    width='stretch', # Replaces use_container_width=True
    column_config={
        "timestamp": st.column_config.DatetimeColumn("Time", format="D MMM, HH:mm:ss"),
        "sentiment_score": st.column_config.NumberColumn("Sent. Score", format="%.2f"),
        "rsi_14": st.column_config.NumberColumn("RSI (14)", format="%.1f"),
        "sma_20": st.column_config.NumberColumn("SMA (20)", format="$%.2f"),
        "price": st.column_config.NumberColumn("Price", format="$%.2f"),
        "pnl": st.column_config.NumberColumn("P&L ($)", format="$%.2f"),
        "pnl_percent": st.column_config.NumberColumn("P&L (%)", format="%.2f%%"),
    }
)

# Detailed View (Expander)
st.subheader("Deep Dive")
if not filtered_df.empty:
    selected_id = st.selectbox("Select Decision ID to Inspect", filtered_df['id'])
    row = filtered_df[filtered_df['id'] == selected_id].iloc[0]
    
    with st.expander("See Full Details", expanded=True):
        c1, c2 = st.columns(2)
        c1.write(f"**Ticker:** {row['ticker']}")
        c1.write(f"**Action:** {row['action']}")
        c1.write(f"**Price:** ${row['price']}")
        
        c2.write(f"**RSI:** {row['rsi_14']}")
        c2.write(f"**SMA:** {row['sma_20']}")
        c2.write(f"**Sentiment:** {row['sentiment_score']}")
        
        st.markdown("---")
        st.write("**AI Reasoning:**")
        st.info(row['sentiment_reason'])
        
        st.write("**Final Decision Logic:**")
        st.success(row['decision_reason'])
