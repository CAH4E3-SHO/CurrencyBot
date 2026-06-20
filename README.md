# 💱 Currency Converter Bot

A Telegram bot for converting currencies and crypto built with **aiogram 3** and **Python**.  
Supports fiat currencies (UAH, USD, EUR, GBP, PLN etc.), cryptos, metals — with live exchange rates.

---

## Features

- Sequential UX: pick source → target → enter amount
- Inline keyboard menus, no typing needed
- Live rates via [exchangerate.host](https://exchangerate.host) (fiat) and [CoinGecko](https://coingecko.com) (BTC)
- FSM-based flow — each user has isolated state
- Input validation with friendly error messages

---

## Requirements

- Python 3.11+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

---

## Installation

```bash
git clone https://github.com/CAH4E3-SHO/currency-bot.git
cd currency-bot
pip install -r requirements.txt
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
| `/start` | Opens the bot and shows the source currency menu |
| Pick source | Tap the currency you want to convert **from** |
| Pick target | Tap the currency you want to convert **to** |
| Enter amount | Type a number, e.g. `1500` or `3.14` |
| Result | Bot replies with the converted amount and live rate |

---

## Project Structure

```
currency-bot/
├── bot.py       # Main bot logic (FSM, handlers, keyboards)
├── requirements.txt  # Python dependencies
├── .env              # Bot token (not committed)
└── README.md
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `aiogram` | Telegram Bot API framework |
| `python-dotenv` | Load token from `.env` |
| `aiohttp` | Async HTTP client for rate APIs |

---

## License

MIT
