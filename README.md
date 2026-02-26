# 🧠 stockexchange_V0.1 — AI Trading Brain

> Autonomous AI trading system with macro-environmental awareness, shadow linking, and 24/7 Raspberry Pi deployment.

---

## 🗂️ 项目文件一览

| 文件 | 作用 | 何时运行 |
|------|------|----------|
| `config.py` | 所有配置参数（预算、止损%、API密钥、Telegram） | 不需要运行，被其他文件引用 |
| `market_brain.py` | 宏观哨兵 + RSS抓取 → DeepSeek分析 → Gemini审核 → 输出 `sentiment_data.json` | **第1步** |
| `logic_engine.py` | 重力调整引擎：读取情绪 + 宏观偏差 + Alpaca持仓 → 防御模式 + 恐慌覆盖 → `execution_plan.json` | **第2步** |
| `trader.py` | 读取执行计划 → 提交Alpaca订单（支持分数股Market单） → 5秒轮询成交 → 回写DB | **第3步** |
| `supervisor.py` | **24/7 调度器**：NYSE日历自动执行晨间/收盘会话 + 心跳 + 周五备份 | **Pi 5 自动运行** |
| `telegram_bot.py` | 交易摘要推送、周一心跳、周五DB备份、防御/恐慌模式报警 | 由 supervisor 调用 |
| `outcome_tracker.py` | 查询14天前的BUY → 获取7天/14天后价格 → 计算实际收益率 | 两周后运行 |
| `strategy_reviewer.py` | 读取已完成交易 → DeepSeek评分(A-F) + 改进建议 → `strategy_report.md` | 两周后运行 |
| `trade_logger.py` | 数据库模块，所有脚本共用，含宏观字段 `env_bias` / `macro_reason` | 不需要单独运行 |
| `dashboard.py` | Streamlit仪表板 | `run_dashboard.bat` |
| `test_alpaca.py` | 测试Alpaca连接 | 调试时运行 |
| `close_shorts.py` | 紧急关闭空头仓位 | 应急时运行 |

---

## 🏗️ 架构总览

```
                     ┌─────────────────────────┐
                     │   supervisor.py (24/7)   │
                     │  NYSE Calendar + pytz    │
                     └────────┬────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        09:45 ET         15:30 ET        Mon 09:00
     Morning Guard    Closing Sprint    Heartbeat
              │               │
              ▼               ▼
     ┌────────────────────────────┐
     │     market_brain.py        │
     │  Stage 0: Macro Sentinel   │ ← Gemini (env_bias + shadow links)
     │  Stage 1: DeepSeek Scan    │
     │  Stage 2: Gemini Audit     │
     │  Stage 3: Consensus        │
     └────────────┬───────────────┘
                  ▼
     ┌────────────────────────────┐
     │     logic_engine.py        │
     │  Gravity Budget (×bias)    │
     │  Defense Mode (bias<0.5)   │ → freeze buys + ATR×0.7
     │  Panic Mode (bias<0.3)    │ → override grace period
     │  Hybrid Fractional/Whole   │
     └────────────┬───────────────┘
                  ▼
     ┌────────────────────────────┐
     │       trader.py            │ → Alpaca Orders
     └────────────┬───────────────┘
                  ▼
     ┌────────────────────────────┐
     │     telegram_bot.py        │ → Summary / Alerts
     └────────────────────────────┘
```

---

## 🚀 运行方式

### 手动运行（开发/调试）

```bash
python market_brain.py      # 宏观分析 + 新闻扫描 → sentiment_data.json
python logic_engine.py      # 重力调整执行计划   → execution_plan.json
python trader.py            # 提交订单到Alpaca
```

### 自动运行（Pi 5 部署）

```bash
# 1. 设置 .env（API密钥 + Telegram token）
# 2. Docker 部署
docker compose up -d --build

# 3. 监控
docker compose logs -f
docker compose ps           # 查看 healthcheck 状态
```

### Supervisor 模式

```bash
python supervisor.py             # 24/7 运行（Morning Guard + Closing Sprint）
python supervisor.py --dry-run   # 仅打印日程，不执行交易
```

---

## 🌍 V3.0 宏观雷达

### 宏观环境偏差 (`global_env_bias`)

| 分数 | 状态 | 系统行为 |
|------|------|----------|
| 1.0 | 🟢 稳定 | 正常交易 |
| 0.8 | 🟢 轻微关注 | 正常交易，预算微缩 |
| 0.6 | 🟡 扰动 | 预算按比例缩减 |
| 0.4 | 🟡 风险上升 | 预算缩减至40% |
| < 0.5 | 🚨 **防御模式** | 冻结所有买入 + ATR止损收紧30% |
| < 0.3 | 💥 **恐慌模式** | 防御模式 + 覆盖24小时宽限期 |

### 影子关联 (Shadow Linking)

Gemini 分析头条新闻后，自动识别被间接影响的公司：
- 银行丑闻 → JPM, GS, DB（间接曝险）
- 石油危机 → DAL, FDX（成本冲击）
- 网络攻击 → ICE（交易所曝险）

---

## 📊 两周复盘流程

```bash
python outcome_tracker.py    # 回填7天/14天后价格
python strategy_reviewer.py  # AI评分(A-F) → strategy_report.md
```

---

## 🔄 数据闭环

```
决策(logic_engine) → 执行(trader) → 结果(outcome_tracker) → AI评审(strategy_reviewer)
     ↓                    ↓                ↓                        ↓
  decision_id         order_id        price_14d              grade + feedback
  decision_reason     filled_price    outcome_pnl%           3条改进建议
  env_bias            filled_qty      price_7d               ai_feedback
     └────────────────── trade_history.db ──────────────────────┘
```

---

## ⚙️ 配置说明 (`config.py`)

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `TOTAL_BUDGET` | 1000.0 | 总本金预算（不含浮盈） |
| `RISK_PER_TRADE_PERCENT` | 0.10 | 每笔交易最大占比10% |
| `MAX_CONCENTRATION_PERCENT` | 0.20 | 单只股票最大占比20% |
| `STOP_LOSS_PERCENT` | 0.08 | 跌8%触发止损 |

## 🔑 环境变量 (`.env`)

```
ALPACA_API_KEY=你的key
ALPACA_SECRET_KEY=你的secret
DEEPSEEK_API_KEY=你的deepseek_key
GEMINI_API_KEY=你的gemini_key
TELEGRAM_BOT_TOKEN=你的telegram_bot_token
TELEGRAM_CHAT_ID=你的telegram_chat_id
```

**获取 Telegram Token:**
1. Telegram 搜索 `@BotFather` → `/newbot`
2. 复制 token 到 `.env`
3. 向你的 bot 发一条消息，然后访问 `https://api.telegram.org/bot<TOKEN>/getUpdates` 获取 `chat_id`

## 📦 安装依赖

```bash
pip install -r requirements.txt
```

## 🐳 Docker 部署 (Pi 5)

```bash
docker compose up -d --build    # 构建并启动
docker compose logs -f          # 查看日志
docker compose restart          # 重启
docker compose down             # 停止
```

**持久化卷：**
- `./data/` — `trade_history.db`
- `./logs/` — `supervisor.log` + `.heartbeat`
