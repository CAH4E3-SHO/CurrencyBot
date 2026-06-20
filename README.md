# 💱 Currency Converter Bot

A Telegram bot for converting currencies and crypto built with **aiogram 3** and **Python**.
Supports fiat currencies, precious metals, and crypto — with live exchange rates and in-memory caching.

---

## Features

- Sequential UX: pick category → pick currency → repeat for target → enter amount
- Three currency categories: 💵 Fiat, 🥇 Metals, ₿ Crypto
- 🌐 Bilingual interface: English and Ukrainian
- Arithmetic expressions as input — type `500*3` or `1000/4+200` instead of a plain number
- Live rates via NBU (fiat + metals), Frankfurter (fiat), and CoinGecko (crypto)
- In-memory TTL cache — 1 hour for fiat/metals, 2 minutes for crypto
- FSM-based flow — each user has isolated state
- ⬅️ Back navigation at every step
- Input validation with friendly error messages

---

## Supported Currencies

| Category | Currencies |
|----------|-----------|
| 💵 Fiat  | UAH, USD, EUR, GBP, PLN, CZK, RON, MDL |
| 🥇 Metals | XAU (Gold), XAG (Silver) |
| ₿ Crypto | BTC, ETH, SOL, USDT |

---

## Rate Sources

| Source | Used for |
|--------|---------|
| [NBU](https://bank.gov.ua) | UAH, MDL, XAU, XAG pairs |
| [Frankfurter](https://frankfurter.app) | Pure fiat pairs (no UAH/MDL/metals) |
| [CoinGecko](https://coingecko.com) | Crypto prices |

---

## Requirements

- Python 3.11+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

---

## Installation

```bash
git clone https://github.com/yourname/currency-bot.git
cd currency-bot
pip install aiogram python-dotenv aiohttp
```

Create a `.env` file in the project root:

```env
BOT_TOKEN=your_telegram_bot_token_here
```

---

## Running

```bash
python bot.py
```

---

## Usage

| Step | Action |
|------|--------|
| `/start` | Opens the bot; shows language picker on first launch |
| Pick category | 💵 Fiat, 🥇 Metals, or ₿ Crypto |
| Pick source | Tap the currency to convert **from** |
| Pick category | Choose target category |
| Pick target | Tap the currency to convert **to** |
| Enter amount | Type a number or expression: `1500`, `500*3`, `1000/4+200` |
| Result | Bot replies with the converted amount and live rate |
| `/language` | Switch between English and Ukrainian at any time |

---

## Project Structure

```
currency-bot/
├── bot.py        # Main bot logic
├── .env          # Bot token (not committed)
└── README.md
```

---

## License

MIT
