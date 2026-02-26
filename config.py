import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API Configuration
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Gemini Configuration (Consensus Auditor)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_NAME = "gemini-3-flash-preview"

# RSS Feeds - Expanded for Global Coverage
RSS_FEEDS = [
    # US / Global
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.wired.com/feed/category/business/latest/rss",
    
    # Asia
    "https://asia.nikkei.com/rss/feed/nar", # General Nikkei feed (Tech specific not always available via RSS, filtering by content)
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

# --- RISK & MONEY MANAGEMENT ---
TOTAL_BUDGET = 1000.0                # Total account size (Scalable base)
RISK_PER_TRADE_PERCENT = 0.10       # 10% of budget per trade
MAX_CONCENTRATION_PERCENT = 0.20    # Max 20% of budget in one stock
STOP_LOSS_PERCENT = 0.08            # 8% drop triggers sell

# --- TELEGRAM MONITORING ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
