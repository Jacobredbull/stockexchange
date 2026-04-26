# Strategy Review Report

**Generated**: 2026-04-26 12:21
**Trades Reviewed**: 16

## Overall Assessment

The strategy shows one excellent winner (BMBL +22.80%) and one solid winner (MRK +3.96%), but is heavily skewed toward losses with 12 of 16 trades negative. The core issues are: (1) low conviction scores/ranks being acted upon, (2) poor timing on swap and replace decisions that buy into declining stocks, and (3) no risk management to cut losses. The strategy appears to be overtrading with too many marginal decisions. Strengths include the ability to identify one strong winner and a defensive pick, but the lack of filters and stop-losses is causing significant capital erosion. Recommend pausing trading, backtesting the proposed improvements, and only re-entering with stricter criteria.

## Trade Grades

| Decision # | Ticker | Action | Return | Grade | Feedback |
|---|---|---|---|---|---|
| 10 | BMBL | BUY | +22.80% | **A** | Excellent swap decision. Strong positive return of +22.80% from BMBL. Proceeds from MP were well deployed. |
| 55 | RIVN | BUY | -8.20% | **F** | Poor holistic buy decision. Rank 0.240 is low conviction and resulted in -8.20% loss. Avoid low-ranked entries. |
| 70 | NVDA | BUY | -8.56% | **F** | Bad swap decision. Swapping out of RIVN into NVDA at $183.10 was poorly timed, losing -8.56%. The swap logic needs a momentum filter. |
| 73 | UBER | BUY | -7.51% | **F** | Poor holistic buy. Rank 0.420 is moderate but resulted in -7.51% loss. Consider requiring higher rank thresholds for entry. |
| 77 | NFLX | BUY | -1.47% | **C** | Neutral outcome. -1.47% loss is within noise range. Low rank (0.240) suggests this was a marginal decision. |
| 78 | RIVN | BUY | -9.60% | **F** | Bad repeat buy. Second RIVN buy at $16.36 with same low rank (0.240) resulted in -9.60% loss. No averaging down into losers. |
| 93 | CRM | BUY | -9.78% | **F** | Poor gap-fill trade. Rank 0.560 is decent but -9.78% loss indicates the gap-fill strategy needs a stop-loss or better timing filter. |
| 96 | UBER | BUY | -5.25% | **D** | Poor swap decision. Swapping RIVN for UBER again resulted in -5.25% loss. The swap logic is consistently underperforming. |
| 119 | PLTR | BUY | -10.37% | **F** | Bad full replace decision. Replacing FCX with PLTR at $153.43 led to -10.37% loss. No evidence of mean reversion or catalyst. |
| 121 | BMBL | BUY | -16.49% | **F** | Very bad full replace. Replacing MP with BMBL at $3.88 resulted in -16.49% loss. Buying into a falling knife. |
| 123 | F | BUY | -5.88% | **D** | Poor full replace. Replacing IT with F at $11.90 lost -5.88%. F is a low-beta value stock not suited for momentum strategies. |
| 160 | META | BUY | -9.58% | **F** | Bad slot fill. Score 0.608 is high but META at $632.74 lost -9.58%. The scoring system may be overfitting or ignoring market context. |
| 162 | MRK | BUY | +3.96% | **B** | Good slot fill. Score 0.572 with +3.96% return. Defensive pharma stock performed well. Consider more defensive sector allocations. |
| 190 | NVDA | BUY | -4.03% | **D** | Poor slot fill. Score 0.629 is high but NVDA at $183.20 lost -4.03%. High scores don't guarantee short-term performance. |
| 192 | NVDA | BUY | -3.99% | **D** | Poor full replace. Replacing META with NVDA at $183.11 lost -3.99%. Similar to trade 190, the NVDA entry timing was poor. |
| 216 | XLE | BUY | +0.75% | **C** | Neutral slot fill. Score 0.574 with +0.75% return. Energy sector (XLE) was a safe choice but generated minimal alpha. |

## Recommended Improvements

- 1. Add a minimum rank/score threshold for all entry types: Require Rank >= 0.600 for holistic buys and Score >= 0.650 for slot fills. Trades 55, 77, 78 (Rank 0.240) and 190, 192 (Score 0.629) all underperformed with sub-threshold scores. This would have eliminated 5 of the 12 losing trades.
- 2. Implement a momentum filter for swap and replace decisions: Before swapping into a new position, require the target ticker to have positive 5-day momentum (e.g., price above 5-day SMA). Trades 70, 96, 119, 121 all swapped into stocks that continued declining. A momentum check would have prevented entering NVDA at $183.10 (trade 70) and BMBL at $3.88 (trade 121).
- 3. Add a stop-loss or time-stop for all positions: Implement a -7% trailing stop-loss or a 7-day maximum hold for underperforming positions. Trades 55, 78, 93, 119, 121 all exceeded -8% losses. A -7% stop would have limited losses on 5 trades and preserved capital for better opportunities.