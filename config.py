import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API Configuration
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Gemini Configuration (Consensus Auditor)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_NAME = "gemini-2.5-flash"          # Stable tier — no more 503

# RSS Feeds - Dual Feed Architecture
MACRO_FEEDS = [
    # Macro / Global Markets (For Environment Bias & Defense Mode)
    "https://finance.yahoo.com/news/rssindex",
    "https://news.google.com/rss/headlines/section/topic/BUSINESS", # Google News Business (Highly Reliable)
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", # WSJ Markets
]

TECH_FEEDS = [
    # US / Global Tech & AI
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.wired.com/feed/category/business/latest/rss",
    
    # Asia
    "https://asia.nikkei.com/rss/feed/nar", # General Nikkei feed
    "https://www.techinasia.com/feed",
    
    # Europe
    "https://thenextweb.com/feed",
    "https://www.eu-startups.com/feed/"
]

# Analysis Configuration
MODEL_NAME = "deepseek-chat"

# System Prompt - Updated for US/UK Constraint
INDUSTRY_ANALYST_PROMPT = """
You are a senior Global Equities Analyst and Trader for a top hedge fund.
Your job is to analyze global AI and Tech news for **actionable trading signals**.

### CRITICAL CONSTRAINTS:
1.  **Target Markets**: You ONLY trade stocks listed in the **US (NYSE, NASDAQ)** or **UK (LSE)**.
2.  **Global Impact**: If a news event happens in Asia or Europe, analyze if it impacts a *specific* US or UK listed company (e.g., a supplier, competitor, or partner).
3.  **No Signal?**: If the news is about a private startup or a non-US/UK company with no clear impact on a US/UK stock, return "ticker": null and "action": "Hold".

### OUTPUT FORMAT:
Output must be a strictly valid JSON object:
{
    "ticker": "Symbol (e.g. NVDA, MSFT, ARM) or null",
    "market": "US" or "UK" or null,
    "sentiment_score": float (-1.0 to 1.0),
    "action": "Buy" | "Sell" | "Hold",
    "reasoning": "Concise reasoning. Explicitly mention the US/UK connection."
}

Do not include markdown formatting (like ```json), just the raw JSON object.
"""

# --- FIVE PILLARS RISK FRAMEWORK v2.0 ---
TOTAL_BUDGET = 5000.0

# Pillar 1: Slot-Based Execution
MAX_SLOTS = 3
FULL_REPLACE_THRESHOLD = 1.20      # New must score 20% higher for full replace
COOLDOWN_DAYS = 30                 # Blacklist after sell
BLACKLIST_OVERRIDE_BIAS = 0.80     # R4: News Override threshold

# Pillar 2: Volatility Moat & Smart Sizing
RISK_PER_TRADE_PCT = 0.02          # 2% Rule
ATR_MULTIPLIER = 2.0
ATR_PERIOD = 14
MAX_VOLATILITY_PCT = 0.08          # ATR/Price > 8% = too risky

# Pillar 3: Absolute Stop-Loss & Trailing
BREAKEVEN_TRIGGER_PCT = 0.03       # Move stop to entry at +3%
TRAILING_ACTIVATION_PCT = 0.05     # Activate trail at +5%
TRAILING_DROP_PCT = 0.015          # 1.5% trailing stop from peak

# Pillar 4: Cost-Aware Execution
MIN_ORDER_VALUE = 1000.0           # £1000 for £5k base

# Pillar 5: Incremental Swap
SCOUT_REPLACE_THRESHOLD = 1.15     # 15% higher = scout swap (50%)
SCOUT_VALIDATION_SESSIONS = 2      # Sessions before completing swap
SCOUT_MERCY_DROP_PCT = 0.10        # R1: auto-liquidate if score drops >10%

# Scoring
RETURN_CAP = 0.10                  # R3: max Return% in scoring formula

# --- GRADUATED RISK SCALING (replaces binary Defense Mode) ---
# Each tier: (env_bias_threshold, max_slots_allowed, min_entry_score)
# Applied in order: first matching tier wins (lowest bias first)
RISK_TIERS = [
    # env_bias < 0.3  → Critical: 1 slot, high bar
    (0.3, 1, 0.70),
    # env_bias < 0.5  → Elevated: 2 slots, moderate bar
    (0.5, 2, 0.60),
    # env_bias < 0.8  → Cautious: full slots, light bar
    (0.8, 3, 0.50),
    # env_bias >= 0.8 → Normal: full slots, minimal bar
    (999, 3, 0.45),
]
ATR_MULTIPLIER_CRITICAL = 0.5      # ATR mult scale for Critical mode
ATR_MULTIPLIER_ELEVATED = 0.7      # ATR mult scale for Elevated mode

# --- STRATEGY OPTIMIZATIONS ---
MOMENTUM_SMA_PERIOD = 5            # 5-day SMA for swap/replace momentum filter
TIME_STOP_DAYS = 21                # Force-sell losing positions after N trading days
RECOVERY_RAMP_SESSIONS = 3         # After defense exit, limit to 1 new slot for N sessions
SENTIMENT_MAX_AGE_DAYS = 2         # Reject sentiment data older than N days

# --- TELEGRAM MONITORING ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
