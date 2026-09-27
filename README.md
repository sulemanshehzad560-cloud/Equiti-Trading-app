# Equiti Trader

An automated trading bot for **Equiti** accounts, plus an installable app to watch and
control it from your phone or desktop. The app is a PWA, ready to package with
[PWABuilder](https://www.pwabuilder.com).

```
equiti-trader/
├── bot/        Python bot: strategies, news reader, risk manager, MT5 connection, app API
├── public/     The app (PWA): manifest, service worker, icons, screenshots
├── netlify.toml   hosts public/ as a static site (no build step)
└── .github/workflows/tests.yml
```

> **Risk warning.** Leveraged FX/CFD trading can lose money fast, and no strategy here is
> guaranteed to be profitable. Backtest first, run on a **demo** account for weeks, and only
> then consider live money. Use at your own risk.

---

## How it all fits together

```
 Your PC / Windows VPS                                   Your phone / desktop
┌──────────────────────────────────────────┐          ┌────────────────────────┐
│ MetaTrader 5 (logged in to Equiti)       │          │ Equiti Trader app      │
│        ▲ official MetaTrader5 package    │  HTTPS   │ (PWA from PWABuilder)  │
│ bot/  strategies ─ news ─ risk ─ orders  │◄────────►│ status · positions     │
│       app API + app on :8787             │  token   │ news · activity        │
└──────────────────────────────────────────┘          └────────────────────────┘
          trades also appear in the normal Equiti / MT5 apps
```

* **No API key needed.** Equiti has no public trading API, but every Equiti account can use
  MetaTrader 5, and MetaQuotes ships an official Python package that drives the MT5
  terminal. The bot logs in with your own MT5 number, password and server. Its trades show
  in the Equiti app like any other trade.
* **The app can't place trades.** It only shows status, pauses or resumes new entries, and
  closes the bot's positions. Trading decisions stay in the bot.
* The bot only manages its own trades, which carry a magic number. **It never touches trades you open by hand.**

---

## 1. Run the bot

Needs Windows (a PC, or a cheap Windows VPS for 24/5 running), the **MT5 terminal from
Equiti**, and Python 3.10+.

```bash
cd bot
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env                  # put your Equiti MT5 login / password / server in it
copy config.example.json config.json    # symbols, strategies, risk, news settings
```

In MT5, switch on **Algo Trading** and allow it under *Tools → Options → Expert Advisors*.
Use the server name exactly as MT5 shows it under *File → Login to Trade Account*. If your
account's symbols have a suffix (for example `EURUSD.m`), change `symbols` in `config.json`.

```bash
python main.py backtest --synthetic          # offline check that everything works
python main.py backtest --csv EURUSD_M15.csv # real history: MT5 > View > Symbols > Bars > Export
python main.py news --symbol EURUSD          # what the news reader currently thinks
python main.py demo                          # try the app on simulated prices (no MT5 needed)
python main.py run --mode paper              # live Equiti prices, simulated orders
python main.py run --mode demo               # real orders on a DEMO login (refuses a real account)
python main.py run --mode live --confirm-live
```

On first `run` or `demo`, the bot prints an **app access token** and saves it in
`bot/.env` as `DASHBOARD_TOKEN`. On the same PC, open **http://localhost:8787** to use the app
right away.

### What the bot does

* **Strategies.** Three strategies vote on every closed bar. A weighted ensemble trades only
  when the combined signal is strong enough.
  * **Trend follow:** EMA 20/50 plus ADX ≥ 22 plus DI direction.
  * **Mean reversion:** RSI 30/70 outside the Bollinger Bands, only in ranging markets (ADX < 20).
  * **Breakout:** a close through the 20-bar Donchian channel.
* **News, read in the background with no keys.**
  * **Economic calendar blackout:** no new trades from 30 min before to 30 min after
    high-impact events for the pair's currencies. Gold, oil and indices also follow USD events.
  * **Headline sentiment from public RSS feeds:** each clause's subject currency is scored with
    a finance lexicon (hawkish, rate hike, beats … versus dovish, rate cut, misses …), and older
    headlines count for less. News against a trade by ≥ 0.35 **vetoes** it. News that agrees
    **sizes up** the trade to 1.5×.
* **Risk.** Each trade risks 0.5 % of equity, with ATR stop loss and take profit (2:1). Limits:
  3 open trades, 1 per symbol, 3 % daily loss halt, a spread filter and trading hours. Every
  decision is written to `logs/journal.jsonl`.

## 2. Put the app online (Netlify)

1. On Netlify, choose **Add new site → Import from Git → GitHub →** this repo.
2. Leave the build command empty and set the publish directory to `public`. `netlify.toml`
   already sets both, so just click **Deploy**.
3. Open `https://YOUR-SITE.netlify.app`. You should see the app with *Try with sample data*.

Any static HTTPS host works, including GitHub Pages and Cloudflare Pages. Just publish the `public/` folder.

## 3. Package it with PWABuilder

1. Go to **https://www.pwabuilder.com**, paste `https://YOUR-SITE.netlify.app` and click **Start**.
   The report card should show **Manifest**, **Service Worker** and **Security (HTTPS)** as green.
   The manifest already includes the name, description, icons (any and maskable, 192 and 512),
   narrow and wide screenshots, shortcuts, categories, display modes and a launch handler.
2. Click **Package For Stores** and pick a platform:
   * **Android:** set a package ID such as `com.yourname.equititrader` and download the zip.
     It contains an `.apk` to install directly on your phone, an `.aab` for the Google Play
     Console, **a signing key (keep it safe, and never commit it)**, and `assetlinks.json`.
   * **Windows:** gives you an `.msixbundle` for the Microsoft Store or for sideloading.
   * **iOS:** gives you an Xcode project. A Mac is needed to build it.
3. **Android only, to remove the browser address bar:** copy the `assetlinks.json` from the
   zip to `public/.well-known/assetlinks.json`, commit and push. Wait for Netlify to redeploy,
   then check that `https://YOUR-SITE.netlify.app/.well-known/assetlinks.json` shows the JSON.
   Uninstall and reinstall the APK.

## 4. Connect the app to the bot from your phone

Browsers only let an installed HTTPS app talk to **HTTPS** addresses, or to `localhost` on the
same machine. To reach the bot from your phone, give it a free HTTPS address with a Cloudflare
tunnel. On the trading PC, run:

```bash
cloudflared tunnel --url http://localhost:8787
```

It prints an address like `https://something.trycloudflare.com`. In the app, open **Settings**,
paste that address and the access token, click **Test**, then **Save**.

* Quick tunnels get a new address each time they start. For a permanent address, use a named
  Cloudflare tunnel (free Cloudflare account) or Tailscale Funnel.
* Set `DASHBOARD_ORIGIN=https://YOUR-SITE.netlify.app` in `bot/.env` so only your app can call
  the bot from a browser.
* Anyone with the token can pause the bot and close its trades, though not open new ones.
  Treat the token like a password. To change it, delete `DASHBOARD_TOKEN` from `.env` and
  restart the bot, which prints a new one.

## The app

| Tab | Shows |
|---|---|
| Dashboard | Equity, balance, today's P&L, mode (Paper / Demo / **LIVE**), running, paused or halted, **Pause** and **Close all** |
| Positions | Every open bot trade with live P&L, entry, stop and target |
| News | Per-symbol blackout and headline bias meter, upcoming high and medium impact events, currency mood, latest headlines |
| Activity | Every decision: opens with lot size and news multiplier, closes, and optionally skips with the reason |

The app works offline for its shell, since the service worker caches it. Trading data always
comes live from the bot and is never cached. Light and dark themes follow the device.

To regenerate the icons and store screenshots after changing the design, use any headless
browser: render `public/icons/icon.svg` at 192 and 512 px, then take the screenshots at
1080×1920 and 1920×1080 with *Try with sample data* turned on.

## Tests

```bash
cd bot && python -m unittest discover -s tests -v
```

There are 30 tests, covering the indicators, strategies, news parsing, sentiment and blackout,
the engine's news veto and size boost, risk sizing, paper fills, the MT5 adapter (against a fake
`MetaTrader5` module, so they pass on any OS), and the app API (auth, pause and resume,
close-all, static file safety). GitHub Actions runs them on every push.
