"""
build_check.py — Verify all packages loaded correctly after Docker build.

Run after docker compose up:
    docker compose exec stockexchange_v01 python build_check.py

Or directly:
    docker run --rm stockexchange_v01 python build_check.py
"""
import sys

errors = []

def check(name, import_stmt, version_attr=None):
    try:
        mod = __import__(import_stmt)
        ver = getattr(mod, version_attr or '__version__', 'unknown')
        print(f"  ✅  {name:30s} {ver}")
    except ImportError as e:
        print(f"  ❌  {name:30s} MISSING — {e}")
        errors.append(name)

print(f"\n{'='*50}")
print(f"  Antigravity — Package Check ({sys.platform})")
print(f"  Python {sys.version}")
print(f"{'='*50}")

check("numpy",                   "numpy")
check("pandas",                  "pandas")
check("feedparser",              "feedparser")
check("openai",                  "openai")
check("alpaca-trade-api",        "alpaca_trade_api")
check("requests",                "requests")
check("python-dotenv",           "dotenv", "__version__")
check("httpx",                   "httpx")
check("pandas_market_calendars", "pandas_market_calendars")
check("pytz",                    "pytz")

# google-genai optional
try:
    from google import genai
    print(f"  ✅  {'google-genai':30s} {getattr(genai, '__version__', 'installed')}")
except ImportError as e:
    print(f"  ⚠️   {'google-genai':30s} Not available (Gemini fallback will use DeepSeek)")

print(f"{'='*50}")
if errors:
    print(f"  ❌  {len(errors)} package(s) FAILED: {', '.join(errors)}")
    sys.exit(1)
else:
    print(f"  ✅  All core packages OK — ready to trade!\n")
