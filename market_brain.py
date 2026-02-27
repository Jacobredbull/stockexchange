import feedparser
import json
import os
import time
from datetime import datetime
from openai import OpenAI
import config

# Gemini SDK (Consensus Auditor)
try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("⚠️ google-genai not installed. Gemini audit disabled.")

class BrainPowerLossError(Exception):
    pass

def with_exponential_backoff(retries=3):
    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    wait_time = 2 ** (attempt + 1)
                    if attempt < retries - 1:
                        print(f"  ⚠️ API error: {e}. Retrying in {wait_time}s (Attempt {attempt+1}/{retries})...")
                        time.sleep(wait_time)
                    else:
                        print(f"  🚨 API failed after {retries} retries: {e}")
                        raise e
        return wrapper
    return decorator


def fetch_rss_news(feed_urls):
    """
    Fetches news from a list of RSS feed URLs.
    """
    articles = []
    print(f"Fetching news from {len(feed_urls)} feeds...")
    
    for url in feed_urls:
        try:
            feed = feedparser.parse(url)
            print(f" - Parsed {len(feed.entries)} entries from {url}")
            for entry in feed.entries[:20]: # Limit to 20 latest articles per feed
                articles.append({
                    "title": entry.title,
                    "link": entry.link,
                    "summary": entry.summary if 'summary' in entry else entry.title,
                    "published": entry.published if 'published' in entry else str(datetime.now())
                })
        except Exception as e:
            print(f"Error parsing {url}: {e}")
            
    return articles


def analyze_article(client, article):
    """
    Analyzes a single article using the DeepSeek API.
    """
    content_to_analyze = f"Title: {article['title']}\nSummary: {article['summary']}\nLink: {article['link']}"
    
    # Define Holistic Prompt locally to avoid modifying config.py
    HOLISTIC_ANALYST_PROMPT = """
    You are a senior Quantitative Portfolio Manager.
    Analyze the news for **actionable trading signals** with a focus on **1-2 week catalyst potential**.

    ### CRITICAL CONSTRAINTS:
    1.  **Target Markets**: ONLY US (NYSE, NASDAQ) or UK (LSE).
    2.  **Global Impact**: Analyze impact on US/UK listed companies.
    3.  **No Signal?**: Return "ticker": null and "action": "Hold".

    ### OUTPUT FORMAT (Strict JSON):
    {
        "ticker": "Symbol or null",
        "market": "US" or "UK" or null,
        "sentiment_score": float (-1.0 to 1.0),
        "duration_score": float (0.0 to 1.0) - Represents confidence in a 1-2 week price appreciation.
        "action": "Buy" | "Sell" | "Hold",
        "reasoning": "Concise reasoning."
    }
    """

    try:
        response = client.chat.completions.create(
            model=config.MODEL_NAME,
            messages=[
                {"role": "system", "content": HOLISTIC_ANALYST_PROMPT},
                {"role": "user", "content": content_to_analyze}
            ],
            temperature=0.0,
            stream=False
        )
        
        content = response.choices[0].message.content
        
        # Clean up code blocks if the model includes them despite instructions
        if content.startswith("```json"):
            content = content.replace("```json", "").replace("```", "")
        elif content.startswith("```"):
            content = content.replace("```", "")
            
        result = json.loads(content)
        
        # Sanitize ticker: strip whitespace + uppercase to prevent "asset not found"
        if result.get('ticker') and result['ticker'] != 'null':
            result['ticker'] = result['ticker'].strip().upper()
        
        # Add metadata back to the result
        result['source_title'] = article['title']
        result['source_link'] = article['link']
        result['published_at'] = article['published']
        
        return result

    except json.JSONDecodeError:
        print(f"Failed to parse JSON for article: {article['title']}")
        print(f"Raw output: {content}")
        return None
    except Exception as e:
        print(f"API Error analyzing {article['title']}: {e}")
        return None


def assess_macro_environment(ds_client, articles, top_n=10):
    """
    Stage 0: Macro-Environmental Sentinel.
    Analyzes the top N headlines for systemic risk.
    Supports 3-Step Fail-over: Gemini (Retry) -> DeepSeek -> BrainPowerLossError.
    Returns (global_env_bias, macro_reason, shadow_tickers, source).
    """
    
    # Take top N most recent headlines
    headlines = [a['title'] for a in articles[:top_n]]
    headlines_block = "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines))
    
    MACRO_PROMPT = f"""You are a Global Macro-Environmental Risk Sentinel for a quantitative trading fund.

Analyze the following {len(headlines)} recent news headlines as an AGGREGATE to assess the global trading environment:

{headlines_block}

### YOUR TASK:
1. Assess the overall macro environment for equity markets.
2. Output a `global_env_bias` score:
   - 1.0 = Stable: No systemic threats, favorable conditions
   - 0.8 = Mild Concern: Minor geopolitical or policy noise
   - 0.6 = Disturbed: High macro noise, political scandals, shifting monetary policy
   - 0.4 = Elevated Risk: Significant geopolitical tensions, market stress signals
   - 0.2 = Critical: Black Swan events, war, systemic financial collapse
3. Provide a brief `macro_reason` explaining your assessment.
4. Perform "Shadow Linking": If any headline suggests a major scandal, geopolitical event, or systemic risk, identify US/UK publicly traded companies ("Shadow Tickers") that could be indirectly affected — even if not explicitly mentioned. These are companies with known exposure (banks, partners, suppliers, competitors). Apply a negative sentiment penalty.

### OUTPUT FORMAT (Strict JSON):
{{
    "global_env_bias": float (0.2 to 1.0),
    "macro_reason": "Brief explanation",
    "shadow_tickers": [
        {{
            "ticker": "SYMBOL",
            "market": "US",
            "action": "Sell",
            "sentiment_score": float (-1.0 to 0.0),
            "duration_score": float (0.0 to 1.0),
            "reasoning": "Shadow link explanation"
        }}
    ]
}}

If no shadow tickers are identified, return an empty array for shadow_tickers.
Return ONLY the raw JSON object. No markdown, no code blocks."""

    @with_exponential_backoff(retries=3)
    def call_gemini():
        if not GEMINI_AVAILABLE or not config.GEMINI_API_KEY:
            raise Exception("Gemini API key not configured")
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        response = client.models.generate_content(
            model=config.GEMINI_MODEL_NAME,
            contents=MACRO_PROMPT,
            config=genai.types.GenerateContentConfig(temperature=0.0)
        )
        return response.text.strip(), "Gemini"

    @with_exponential_backoff(retries=3)
    def call_deepseek():
        response = ds_client.chat.completions.create(
            model=config.MODEL_NAME,
            messages=[{"role": "user", "content": MACRO_PROMPT}],
            temperature=0.0,
            stream=False
        )
        return response.choices[0].message.content, "DeepSeek-V3"

    try:
        print(f"\n--- Macro Sentinel: Analyzing Top {len(headlines)} headlines ---")
        try:
            content, source = call_gemini()
        except Exception as e:
            print(f"  ⚠️ Gemini Macro Sentinel failed: {e}. Switching to DeepSeek-V3 Failover...")
            try:
                content, source = call_deepseek()
            except Exception as ds_e:
                raise BrainPowerLossError(f"Both Gemini and DeepSeek failed for Macro Sentinel: {ds_e}")
        
        # Clean code blocks
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        
        result = json.loads(content)
        
        env_bias = float(result.get('global_env_bias', 1.0))
        env_bias = max(0.2, min(1.0, env_bias))  # Clamp to valid range
        macro_reason = result.get('macro_reason', 'No reason provided')
        shadow_tickers = result.get('shadow_tickers', [])
        
        # Display results
        if env_bias >= 0.8:
            icon = "🟢"
        elif env_bias >= 0.5:
            icon = "🟡"
        else:
            icon = "🔴"
        
        print(f"  {icon} Global Environment Bias: {env_bias} [Source: {source}]")
        print(f"     Reason: {macro_reason}")
        
        if shadow_tickers:
            print(f"  👤 Shadow Tickers Identified: {len(shadow_tickers)}")
            for st in shadow_tickers:
                ticker = st.get('ticker', '').strip().upper()
                st['ticker'] = ticker
                st['source_title'] = 'Shadow Link (Macro Sentinel)'
                st['source_link'] = ''
                st['published_at'] = str(datetime.now())
                st['source'] = 'shadow_link'
                print(f"     🔗 {ticker}: {st.get('reasoning', '')[:80]}")
        
        return env_bias, macro_reason, shadow_tickers, source
    
    except json.JSONDecodeError as e:
        print(f"  ⚠️ Macro returned invalid JSON: {e}")
        return 1.0, f"JSON parse error: {e}", [], "Error"
    except BrainPowerLossError:
        raise
    except Exception as e:
        print(f"  ⚠️ Macro error: {e}")
        return 1.0, f"API error: {e}", [], "Error"


def audit_signals(ds_client, candidate_signals, top_n=5):
    """
    Stage 2: Consensus Audit.
    Sends top N candidates in a SINGLE batched request for re-evaluation.
    Supports 3-Step Fail-over: Gemini (Retry) -> DeepSeek -> BrainPowerLossError.
    Returns a dict mapping ticker -> {sentiment_score, duration_score, reasoning}.
    """
    
    # Sort by absolute sentiment score (strongest signals first)
    sorted_signals = sorted(candidate_signals, key=lambda x: abs(x.get('sentiment_score', 0)), reverse=True)
    top_signals = sorted_signals[:top_n]
    
    if not top_signals:
        return None
    
    # Build batched audit prompt
    signal_descriptions = []
    for i, sig in enumerate(top_signals):
        signal_descriptions.append(
            f"{i+1}. Ticker: {sig['ticker']} | Action: {sig['action']} | "
            f"Sentiment: {sig['sentiment_score']} | Duration: {sig.get('duration_score', 'N/A')} | "
            f"Headline: \"{sig.get('source_title', 'N/A')}\" | "
            f"Reasoning: \"{sig.get('reasoning', 'N/A')}\""
        )
    
    signals_block = "\n".join(signal_descriptions)
    
    AUDITOR_PROMPT = f"""You are a skeptical Quantitative Auditor reviewing trading signals generated by another AI model (DeepSeek).

DeepSeek has identified the following trading signals from today's news:

{signals_block}

### YOUR TASK:
Re-evaluate EACH signal independently. For each ticker:
1. Is the ticker correctly identified? (e.g., is it the right company?)
2. Is the sentiment direction (Buy/Sell) justified by the headline?
3. Provide your own sentiment_score (-1.0 to 1.0) and duration_score (0.0 to 1.0).
4. If the logic is flawed or ticker misidentified, flag it.

### OUTPUT FORMAT (Strict JSON Array):
Return a JSON array with one object per signal, in the SAME ORDER:
[
    {{
        "ticker": "SYMBOL",
        "sentiment_score": float,
        "duration_score": float,
        "reasoning": "Brief auditor note",
        "flagged": false
    }}
]

Return ONLY the raw JSON array. No markdown, no code blocks."""

    @with_exponential_backoff(retries=3)
    def call_gemini():
        if not GEMINI_AVAILABLE or not config.GEMINI_API_KEY:
            raise Exception("Gemini API key not configured")
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        response = client.models.generate_content(
            model=config.GEMINI_MODEL_NAME,
            contents=AUDITOR_PROMPT,
            config=genai.types.GenerateContentConfig(temperature=0.0)
        )
        return response.text.strip(), "Gemini"

    @with_exponential_backoff(retries=3)
    def call_deepseek():
        response = ds_client.chat.completions.create(
            model=config.MODEL_NAME,
            messages=[{"role": "user", "content": AUDITOR_PROMPT}],
            temperature=0.0,
            stream=False
        )
        return response.choices[0].message.content, "DeepSeek-V3"

    try:
        print(f"\n--- Consensus Audit: Reviewing Top {len(top_signals)} signals ---")
        try:
            content, source = call_gemini()
        except Exception as e:
            print(f"  ⚠️ Gemini Audit failed: {e}. Switching to DeepSeek-V3 Failover...")
            try:
                content, source = call_deepseek()
            except Exception as ds_e:
                raise BrainPowerLossError(f"Both Gemini and DeepSeek failed for Auditor: {ds_e}")
        
        # Clean up code blocks
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        
        audit_results = json.loads(content)
        
        # Build lookup by ticker
        audit_map = {}
        for result in audit_results:
            ticker = result.get('ticker', '').strip().upper()
            if ticker:
                audit_map[ticker] = {
                    'sentiment_score': result.get('sentiment_score', 0),
                    'duration_score': result.get('duration_score', 0.5),
                    'reasoning': result.get('reasoning', ''),
                    'flagged': result.get('flagged', False)
                }
                status = "🚩 FLAGGED" if result.get('flagged') else "✅"
                print(f"  {status} {ticker}: {source} Sent={result.get('sentiment_score')}, Dur={result.get('duration_score')} — {result.get('reasoning', '')[:80]}")
        
        return audit_map, source
    
    except json.JSONDecodeError as e:
        print(f"  ⚠️ Auditor returned invalid JSON: {e}")
        print(f"  Raw output: {content[:200]}")
        return None, "Error"
    except BrainPowerLossError:
        raise
    except Exception as e:
        print(f"  ⚠️ API error: {e}")
        return None, "Error"


def apply_consensus(signals, audit_map, kill_switch_threshold=0.35):
    """
    Merge DeepSeek and Gemini scores using consensus logic.
    - Final score = average of both models
    - Kill-Switch: discard if |delta| > threshold
    - Consensus Level: High (≤0.15), Medium (≤0.35), discarded (>0.35)
    """
    if audit_map is None:
        # Gemini unavailable — mark all as unverified
        for sig in signals:
            sig['consensus_level'] = 'unverified'
            sig['model_delta'] = None
            sig['gemini_scores'] = None
        return signals
    
    consensus_signals = []
    
    for sig in signals:
        ticker = sig.get('ticker', '')
        
        if ticker in audit_map:
            gemini = audit_map[ticker]
            ds_sent = sig['sentiment_score']
            ds_dur = sig.get('duration_score', 0.5)
            gm_sent = gemini['sentiment_score']
            gm_dur = gemini['duration_score']
            
            delta = abs(ds_sent - gm_sent)
            
            # Kill-Switch: High disagreement → discard
            if delta > kill_switch_threshold:
                print(f"  🔴 KILL-SWITCH: {ticker} discarded (DeepSeek: {ds_sent}, Gemini: {gm_sent}, Delta: {delta:.2f} > {kill_switch_threshold})")
                continue
            
            # Flagged by Gemini → discard
            if gemini.get('flagged', False):
                print(f"  🚩 FLAGGED: {ticker} discarded by Gemini auditor — {gemini.get('reasoning', '')[:80]}")
                continue
            
            # Consensus scoring
            sig['sentiment_score'] = round((ds_sent + gm_sent) / 2, 3)
            sig['duration_score'] = round((ds_dur + gm_dur) / 2, 3)
            sig['model_delta'] = round(delta, 3)
            sig['gemini_scores'] = {
                'sentiment_score': gm_sent,
                'duration_score': gm_dur
            }
            
            # Consensus level
            if delta <= 0.15:
                sig['consensus_level'] = 'High'
            else:
                sig['consensus_level'] = 'Medium'
            
            print(f"  ✅ {ticker}: Consensus={sig['consensus_level']} | Final Sent={sig['sentiment_score']}, Dur={sig['duration_score']} | Delta={delta:.2f}")
            consensus_signals.append(sig)
        else:
            # Not in top N, unaudited — keep DeepSeek score
            sig['consensus_level'] = 'unverified'
            sig['model_delta'] = None
            sig['gemini_scores'] = None
            consensus_signals.append(sig)
    
    return consensus_signals


def main():
    # Initialize OpenAI client with DeepSeek base URL
    if not config.DEEPSEEK_API_KEY:
        print("Error: DEEPSEEK_API_KEY not found in environment or config.py")
        return

    client = OpenAI(
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL
    )

    # ==========================================
    # STAGE 0: Macro-Environmental Sentinel
    # ==========================================
    
    # 1. Fetch News
    articles = fetch_rss_news(config.RSS_FEEDS)
    if not articles:
        print("No articles found to analyze.")
        return

    # 2. Macro Assessment
    try:
        global_env_bias, macro_reason, shadow_tickers, macro_source = assess_macro_environment(client, articles)
    except BrainPowerLossError as e:
        print(f"\n🚨 {e}")
        # SAFE_HOLD_MODE
        global_env_bias = 0.0
        macro_reason = f"BRAIN_OFFLINE_PROTECTION: {e}"
        macro_source = "SYSTEM_FAIL"
        
        output_data = {
            "global_env_bias": global_env_bias,
            "macro_reason": macro_reason,
            "signals": []
        }
        with open("sentiment_data.json", "w") as f:
            json.dump(output_data, f, indent=4)
            
        import telegram_bot
        telegram_bot.send_emergency_alert(f"🚨 BRAIN OFFLINE: {e}\nSystem entering Safe-Hold mode. No new trades will be made.")
        return

    # ==========================================
    # STAGE 1: DeepSeek RSS Scan (Candidate Signals)
    # ==========================================
    
    print(f"\nFound {len(articles)} articles. Starting Stage 1 (DeepSeek) analysis...")
    
    candidate_signals = []

    for i, article in enumerate(articles):
        print(f"Analyzing {i+1}/{len(articles)}: {article['title']}")
        
        analysis = analyze_article(client, article)
        
        if analysis:
            if analysis.get('ticker') and analysis.get('ticker') != "null":
                candidate_signals.append(analysis)
                print(f"  -> Signal Found: {analysis['ticker']} ({analysis.get('market', 'N/A')}) | {analysis['action']} | Score: {analysis['sentiment_score']}")
            else:
                print("  -> No specific ticker identified.")
        
        time.sleep(1)

    # Inject Shadow Tickers from Macro Sentinel
    if shadow_tickers:
        print(f"\n--- Injecting {len(shadow_tickers)} Shadow-Linked tickers ---")
        for st in shadow_tickers:
            if st.get('ticker'):
                candidate_signals.append(st)
                print(f"  🔗 Added shadow ticker: {st['ticker']} (Sent: {st.get('sentiment_score')})")

    print(f"\n--- Stage 1 Complete: {len(candidate_signals)} candidate signals ---")
    
    if not candidate_signals:
        print("No candidate signals found. Exiting.")
        with open("sentiment_data.json", "w") as f:
            json.dump({"global_env_bias": global_env_bias, "macro_reason": macro_reason, "signals": []}, f, indent=4)
        return

    # ==========================================
    # STAGE 2: Consensus Audit (Top 5)
    # ==========================================
    
    try:
        audit_map, audit_source = audit_signals(client, candidate_signals, top_n=5)
    except BrainPowerLossError as e:
        print(f"\n🚨 {e}")
        # SAFE_HOLD_MODE
        global_env_bias = 0.0
        macro_reason = f"BRAIN_OFFLINE_PROTECTION: {e}"
        
        output_data = {
            "global_env_bias": global_env_bias,
            "macro_reason": macro_reason,
            "signals": []
        }
        with open("sentiment_data.json", "w") as f:
            json.dump(output_data, f, indent=4)
            
        import telegram_bot
        telegram_bot.send_emergency_alert(f"🚨 BRAIN OFFLINE DURNIG AUDIT: {e}\nSystem entering Safe-Hold mode.")
        return
    
    # ==========================================
    # STAGE 3: Apply Consensus & Save
    # ==========================================
    
    print("\n--- Applying Consensus Logic ---")
    final_signals = apply_consensus(candidate_signals, audit_map)
    
    # Save Output — NEW V3 FORMAT with macro envelope
    output_data = {
        "global_env_bias": global_env_bias,
        "macro_reason": macro_reason,
        "macro_source": macro_source,
        "audit_source": audit_source if 'audit_source' in locals() else "N/A",
        "signals": final_signals
    }
    
    output_file = "sentiment_data.json"
    with open(output_file, "w") as f:
        json.dump(output_data, f, indent=4)
    
    # Summary stats
    verified = sum(1 for s in final_signals if s.get('consensus_level') in ('High', 'Medium'))
    unverified = sum(1 for s in final_signals if s.get('consensus_level') == 'unverified')
    shadow_count = sum(1 for s in final_signals if s.get('source') == 'shadow_link')
    discarded = len(candidate_signals) - len(final_signals)
    
    # Env bias display
    if global_env_bias >= 0.8:
        env_icon = "🟢"
    elif global_env_bias >= 0.5:
        env_icon = "🟡"
    else:
        env_icon = "🔴"
    
    print(f"\n--- Analysis Complete ---")
    print(f"  {env_icon} Macro Bias: {global_env_bias} — {macro_reason}")
    print(f"  Candidates: {len(candidate_signals)} | Final: {len(final_signals)} (Verified: {verified}, Unverified: {unverified}, Shadow: {shadow_count}, Discarded: {discarded})")
    print(f"  Saved to {output_file}")


if __name__ == "__main__":
    main()
