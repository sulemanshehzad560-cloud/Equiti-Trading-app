# Strategy research: what's in the playbook and why

**Bottom line.** No publicly known rule set reliably prints money. What published research
*does* support is a handful of effects that have held across decades and markets, usually
with modest returns and long flat spells. Each one below is implemented in
`bot/trader/strategies_research.py` exactly as published, and was then re-tested here on real
data with CFD costs. Run it yourself:

```bash
cd bot
python main.py research --fetch     # downloads the sample data (public Yahoo-derived CSVs on GitHub)
python main.py research             # prints the table and writes research_results.json (shown in the app)
```

Equiti's share and index CFDs trade on MT5 like FX, so the bot handles them directly.
Symbols such as `AAPL`, `AAPL.US`, `#AAPL`, `US500` and `NAS100` are recognised as stocks or
indices. They get the New York cash session (DST-aware) and company-name headline parsing.

---

## The strategies

| Key | Strategy | Style | Where it comes from | Rules in one line |
|---|---|---|---|---|
| `rsi2` | Connors RSI(2) pullback | swing, daily | Connors & Alvarez, *Short Term Trading Strategies That Work* (2008) | Buy when RSI(2) < 10 and close > SMA200; sell when close > SMA5 |
| `tsmom` | Time-series momentum | trend, daily/monthly | Moskowitz, Ooi & Pedersen, *JFE* 2012 | Hold the sign of the 12-month return, vol-scaled; exit on flip |
| `xsmom` | Cross-sectional 12-1 momentum | rotation, daily | Jegadeesh & Titman, *JF* 1993; absolute filter from Antonacci 2014 | Own the top-N of the basket by 12-1m return (if > 0); drop below top-2N |
| `tom` | Turn-of-the-month | calendar | Lakonishok & Smidt 1988; McConnell & Xu, *FAJ* 2008 | Hold from the last trading day to the 3rd trading day of the month |
| `orb` | 5-min opening range breakout | intraday | Zarattini, Barbon & Aziz, SSRN 4729284 (2024) | Follow the first 5-min candle's direction on a break of its high/low; stop 10% ATR; flat at close; high relative volume only |
| `imom` | Market intraday momentum | intraday | Gao, Han, Li & Zhou, *JFE* 2018 | Sign of close-to-10:00 return → trade the last 30 min, flat at the bell |

The original FX strategies (`trend`, `meanrev`, `breakout`) stay available.

### What the sources claim, in brief

* **Connors RSI(2).** Published S&P tests show roughly 0.9% average gain per trade, a high win rate,
  and 18–28% time in market. With the 200-day filter, drawdown falls and CAGR drops.
  ([QuantifiedStrategies](https://www.quantifiedstrategies.com/rsi-2-strategy/),
  [StockCharts](https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/rsi-2))
* **Time-series momentum.** 58 futures markets, 1965–2009, significant alpha that does well in
  crises. Later work argues much of the edge comes from *volatility scaling*, not the momentum
  signal itself.
  ([MOP paper](https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf),
  [critique](https://www.sciencedirect.com/science/article/abs/pii/S1386418116301379))
  The bot sizes by ATR, which is a form of volatility scaling.
* **12-1 momentum.** About 1% a month for winners-minus-losers in the original study. It persisted
  after publication but has sharp "momentum crashes", and reverses after 1–5 years.
  ([30-year review](https://link.springer.com/article/10.1007/s11408-022-00417-8))
* **Turn-of-month.** Over 1926–2005, essentially all of the US equity premium came in the 4-day
  turn-of-month window. It was found in 31 of 35 countries.
  ([McConnell & Xu](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=917884))
* **5-min ORB.** The paper reports a 1,637% total return and a Sharpe of 2.81 from 2016–2023,
  **but only on the top-20 "stocks in play"** by opening relative volume, out of 7,000+ US names.
  An independent replication on five index markets reproduced the gross result, but after costs
  the net was about zero.
  ([paper](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284),
  [replication](https://www.mql5.com/en/blogs/post/776235))
* **Intraday momentum.** The first half-hour return predicts the last half-hour, and the effect is
  stronger on volatile, high-volume and macro-news days. The study used SPY 1993–2013.
  ([JFE paper](https://www.sciencedirect.com/science/article/abs/pii/S0304405X18301351))

---

## Our re-test on real data (with CFD costs)

Assumed costs: **0.05% spread** per round trip and **3%/year overnight financing** on open
positions, which is typical for share CFDs. Check your own account's figures.
Each universe gets about 13 months of warm-up and is then scored over the same window as buy and hold.

"risk 1%" = the bot's default sizing, which risks 1% of equity per trade against the stop.
"alloc X%" = a fixed share of capital per position, comparable to the published tests. A
playbook splits the same capital between its strategies.

### S&P 500, monthly, 1872–2026 (154 years, price only)

| Test | CAGR | Sharpe | Max drawdown | Trades | Time in market |
|---|---:|---:|---:|---:|---:|
| Buy & hold | 4.86% | 0.41 | **84.8%** | – | 100% |
| tsmom 12m long-only, no costs | 4.81% | **0.64** | **34.6%** | 65 | 64% |
| tsmom 12m long-only, with 3% financing | 3.24% | 0.44 | 37.6% | 65 | 64% |
| tsmom 12m long/short, no costs | 4.91% | 0.59 | 46.2% | 128 | 100% |

**Takeaway.** This is the most robust result here. Being long only while the 12-month return is
positive matched the market's return with **less than half the worst drawdown**. On a CFD,
though, financing eats roughly a third of the return. For long holds, a real-share account beats a CFD.

### SPY, daily, 2009-02 → 2017-12 (a strong bull market)

| Test | CAGR | Sharpe | Max DD | Trades | Win % | PF | In market |
|---|---:|---:|---:|---:|---:|---:|---:|
| Buy & hold | **17.1%** | **1.07** | 18.6% | – | – | – | 100% |
| rsi2 (alloc 100%) | 3.7% | 0.80 | **7.4%** | 73 | **75%** | 2.72 | **11%** |
| tom (alloc 100%) | 1.7% | 0.31 | 11.8% | 102 | 56% | 1.23 | 18% |
| tsmom long-only (alloc 100%) | 3.8% | 0.41 | 24.8% | 7 | 43% | 4.33 | 82% |
| trend EMA/ADX (the FX strategy) | −2.5% | −0.18 | 39.9% | 27 | 26% | 0.67 | 88% |

### 5 large-cap stocks (AAPL, GOOG, NVDA, ORCL, YHOO), daily, 2005-10 → 2014-12

| Test | CAGR | Sharpe | Max DD | Trades | Win % | PF |
|---|---:|---:|---:|---:|---:|---:|
| Buy & hold, equal weight | **19.2%** | **0.78** | 63.5% | – | – | – |
| tsmom long-only (alloc 25%) | 12.4% | 0.64 | 43.1% | 54 | 41% | 3.70 |
| xsmom top-2 (alloc 25%) | 8.3% | 0.47 | 50.5% | 69 | 51% | 2.22 |
| tom (alloc 25%) | 4.7% | 0.46 | 23.7% | 530 | 55% | 1.29 |
| rsi2 (alloc 25%) | 1.6% | 0.25 | 19.5% | 355 | 65% | 1.19 |

### What this tells you, honestly

1. **In a bull market nothing beat simply holding.** Every strategy spent time out of the market
   while it rose. Their value is **risk control**. RSI(2) made about 3.7% a year while in the
   market only 11% of the time, with a 7% worst drawdown. The trend filter halved the worst
   crash over 154 years.
2. **RSI(2) is the most reliable short-term edge:** a 75% win rate and PF 2.7 on SPY, in line with
   the published figures. It worked on the index but was weak on single stocks. Stocks gap and
   trend harder than an index, so pullbacks keep going more often.
3. **Momentum (`tsmom`, `xsmom`) is the stock-picking edge**, but expect 40–50% drawdowns on
   single names, and remember that the 5-stock basket is survivorship-biased (we know these
   companies survived).
4. **Turn-of-month is real but thin** after spreads. Treat it as a small booster, not a core strategy.
5. **The original FX trend strategy lost money on SPY.** Keep FX logic for FX.
6. **Costs matter.** Financing turns 4.8% into 3.2% on long holds, and spread decides whether
   short-hold strategies (ORB, TOM) have any edge at all.
7. **ORB and intraday momentum were *not* validated here.** Only 4 days of free intraday data were
   available offline. Export M5 history from Equiti MT5 (1–2 years of `US500` / your shares) and run:
   ```bash
   python main.py backtest --csv US500_M5.csv --symbol US500 --strategies orb,imom --spread-pct 0.02 --tz Etc/GMT-3
   ```
   `--tz` is the timezone of your MT5 export: Equiti's server time is usually GMT+2 or GMT+3.
   Note that `Etc/GMT-3` means UTC+3.

## Recommended starting setup (`config.stocks.example.json`)

* **Daily** timeframe on US index CFDs plus a few liquid large caps, in **independent** mode, so
  each strategy owns its own trades.
* `rsi2` on indices; `tsmom` long-only as the trend and crash filter; `xsmom` across your stock
  list (needs 4 or more symbols); `tom` optional.
* ORB and intraday momentum are off until you've backtested them on your own M5 data.
* Daily rules run **10 minutes before the NY close**, using the live price as the close. MT5's
  daily bar rolls when the stock market is shut, so orders can't be placed then.
* Leverage cap (`max_notional_pct`) on; the news filter stays on. Stock headlines are matched
  by ticker and company name (`stock_aliases`).

## Researched but not implemented

* **Post-earnings drift (PEAD).** Well documented, but needs an earnings calendar and surprise data
  that the free feeds don't provide.
* **Pairs / stat-arb.** Needs a large universe and careful cointegration testing, and is prone to overfitting.
* **"Sell in May", Santa rally and other calendar lore.** Weak or inconsistent out of sample.
* **ML / "AI" signal generators sold online.** No verifiable out-of-sample record.

*Data: SPY/AAPL/GOOG from the mplfinance repository; NVDA/ORCL/YHOO from the backtrader
repository (both Yahoo-derived, split and dividend adjusted via Adj Close); S&P 500 monthly from
datasets/s-and-p-500 (Shiller). Past results don't guarantee future returns.*
