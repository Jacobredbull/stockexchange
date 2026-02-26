"""
Strategy Reviewer — AI-powered audit of past trading decisions.

Uses DeepSeek to grade each completed trade (A-F) and suggest improvements.
Generates a strategy_report.md with findings.

Usage:
    python strategy_reviewer.py
"""
import json
import os
from datetime import datetime
from openai import OpenAI
import config
import trade_logger


REVIEWER_PROMPT = """You are a Senior Quantitative Auditor reviewing a junior trader's decisions.

You will receive a table of completed trades with:
- The original decision_reason (why the bot decided to buy/sell)
- The actual outcome (14-day return %)

### YOUR TASK:
1. **Grade each trade A-F**:
   - A: Excellent decision, strong positive return (>5%)
   - B: Good decision, modest positive return (2-5%)
   - C: Neutral, return near 0% (-2% to 2%)
   - D: Poor decision, moderate loss (-2% to -5%)
   - F: Bad decision, significant loss (>-5%)

2. **Provide 3 actionable improvements** for the trading logic.
   - Reference specific trades as examples.
   - Suggest concrete rule changes (e.g., "Add momentum filter", "Tighten stop-loss").

### OUTPUT FORMAT (Strict JSON):
{
    "grades": [
        {"decision_id": 123, "grade": "B", "feedback": "Good entry but duration too short."},
        ...
    ],
    "improvements": [
        "1. Improvement suggestion with specific reference.",
        "2. Second suggestion.",
        "3. Third suggestion."
    ],
    "overall_assessment": "Brief summary of the strategy's strengths and weaknesses."
}
"""


def review_strategy():
    print("--- AI Strategy Reviewer ---")
    
    # Initialize DB
    trade_logger.init_db()
    
    # 1. Get completed trades with outcomes
    decisions = trade_logger.get_decisions_for_review()
    
    if not decisions:
        print("ℹ️ No completed trades with 14-day outcomes to review.")
        print("   Run outcome_tracker.py first to fill in price data.")
        return
    
    print(f"Found {len(decisions)} trades to review.\n")
    
    # 2. Format data for LLM
    table_header = "| # | Ticker | Action | Decision Reason | Filled Price | 14d Price | Return % |"
    table_sep =    "|---|--------|--------|-----------------|-------------|-----------|----------|"
    table_rows = []
    
    for d in decisions:
        row = f"| {d['id']} | {d['ticker']} | {d['action']} | {d['decision_reason']} | ${d['filled_price']:.2f} | ${d['price_after_14d']:.2f} | {d['outcome_pnl_pct']:+.2f}% |"
        table_rows.append(row)
    
    trade_table = "\n".join([table_header, table_sep] + table_rows)
    
    user_prompt = f"""Here are my recent completed trades and their actual 14-day outcomes:

{trade_table}

Please grade each trade and provide 3 specific improvements for my trading logic."""

    print("📤 Sending to DeepSeek for analysis...")
    print(f"   Trades being reviewed: {len(decisions)}")
    
    # 3. Call DeepSeek
    if not config.DEEPSEEK_API_KEY:
        print("❌ DEEPSEEK_API_KEY not found.")
        return
    
    client = OpenAI(
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL
    )
    
    try:
        response = client.chat.completions.create(
            model=config.MODEL_NAME,
            messages=[
                {"role": "system", "content": REVIEWER_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,
            stream=False
        )
        
        content = response.choices[0].message.content
        
        # Clean up code blocks
        if content.startswith("```json"):
            content = content.replace("```json", "").replace("```", "")
        elif content.startswith("```"):
            content = content.replace("```", "")
        
        result = json.loads(content.strip())
        
    except json.JSONDecodeError:
        print(f"❌ Failed to parse AI response as JSON.")
        print(f"Raw output:\n{content}")
        return
    except Exception as e:
        print(f"❌ API Error: {e}")
        return
    
    # 4. Save grades to DB
    grades = result.get('grades', [])
    for grade_entry in grades:
        dec_id = grade_entry.get('decision_id')
        grade = grade_entry.get('grade', 'C')
        feedback = grade_entry.get('feedback', '')
        
        trade_logger.save_ai_review(dec_id, grade, feedback)
        print(f"   Trade #{dec_id}: {grade} — {feedback}")
    
    # 5. Generate Strategy Report
    improvements = result.get('improvements', [])
    overall = result.get('overall_assessment', 'No assessment provided.')
    
    report_lines = [
        f"# Strategy Review Report",
        f"",
        f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Trades Reviewed**: {len(decisions)}",
        f"",
        f"## Overall Assessment",
        f"",
        f"{overall}",
        f"",
        f"## Trade Grades",
        f"",
        f"| Decision # | Ticker | Action | Return | Grade | Feedback |",
        f"|---|---|---|---|---|---|",
    ]
    
    # Match grades to decisions
    grade_map = {g['decision_id']: g for g in grades}
    for d in decisions:
        g = grade_map.get(d['id'], {})
        grade = g.get('grade', '?')
        feedback = g.get('feedback', '')
        report_lines.append(
            f"| {d['id']} | {d['ticker']} | {d['action']} | {d['outcome_pnl_pct']:+.2f}% | **{grade}** | {feedback} |"
        )
    
    report_lines.extend([
        f"",
        f"## Recommended Improvements",
        f"",
    ])
    
    for imp in improvements:
        report_lines.append(f"- {imp}")
    
    report_content = "\n".join(report_lines)
    
    report_file = "strategy_report.md"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report_content)
    
    print(f"\n✅ Strategy report saved to {report_file}")
    print(f"\n--- Improvements ---")
    for imp in improvements:
        print(f"  💡 {imp}")


if __name__ == "__main__":
    review_strategy()
