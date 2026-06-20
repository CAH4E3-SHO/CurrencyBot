import os
import logging
import sys
import asyncio

import aiohttp
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, html, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

load_dotenv()
TOKEN = os.environ["BOT_TOKEN"]

dp = Dispatcher()


# -- FSM States --


class ConvertFlow(StatesGroup):
    choosing_from_cat = State()  # picking category for source
    choosing_from = State()  # picking source currency
    choosing_to_cat = State()  # picking category for target
    choosing_to = State()  # picking target currency
    entering_amt = State()  # entering amount


# -- Currency sets --

METALS = {"XAU", "XAG"}

# Frankfurter handles these (no metals, no UAH, no MDL)
FRANKFURTER = {"USD", "EUR", "GBP", "PLN", "CZK", "RON"}

# All fiat + metals
FIAT = FRANKFURTER | {"UAH", "MDL"} | METALS

# Crypto coin IDs for CoinGecko
CRYPTO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "USDT": "tether",
}
CRYPTO = set(CRYPTO_IDS.keys())

# Currencies per category (display order preserved)
CAT_FIAT = ["UAH", "USD", "EUR", "GBP", "PLN", "CZK", "RON", "MDL"]
CAT_METALS = ["XAU", "XAG"]
CAT_CRYPTO = ["BTC", "ETH", "SOL", "USDT"]

CATEGORIES = {
    "fiat": ("💵 Fiat", CAT_FIAT),
    "metals": ("🥇 Metals", CAT_METALS),
    "crypto": ("₿ Crypto", CAT_CRYPTO),
}


# -- Keyboard builders --


def category_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=label, callback_data=f"cat:{key}")
            for key, (label, _) in CATEGORIES.items()
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def currency_keyboard(cat_key: str, exclude: str | None = None) -> InlineKeyboardMarkup:
    _, currencies = CATEGORIES[cat_key]
    buttons = [
        InlineKeyboardButton(text=c, callback_data=f"cur:{c}")
        for c in currencies
        if c != exclude
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    # back button
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="cat:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# -- API helpers --


async def _nbu_rate_to_uah(
    session: aiohttp.ClientSession, foreign: str
) -> float | None:
    """Returns how many UAH equal 1 unit of `foreign` currency."""
    url = f"https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?valcode={foreign}&json"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
        if r.status != 200:
            return None
        data = await r.json(content_type=None)
        if not data:
            return None
        return float(data[0]["rate"])


async def _frankfurter_rate(
    session: aiohttp.ClientSession, src: str, dst: str
) -> float | None:
    """Returns how many DST equal 1 SRC via Frankfurter (pure fiat only)."""
    url = f"https://api.frankfurter.app/latest?from={src}&to={dst}"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
        if r.status != 200:
            return None
        data = await r.json(content_type=None)
        return data["rates"].get(dst)


async def _coingecko_rate(
    session: aiohttp.ClientSession, coin_id: str, vs: str
) -> float | None:
    """Returns the price of `coin_id` in `vs` currency."""
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies={vs}"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
        if r.status != 200:
            return None
        data = await r.json(content_type=None)
        return data.get(coin_id, {}).get(vs)


# -- Rate fetching --


async def fetch_rate(src: str, dst: str) -> float | None:
    async with aiohttp.ClientSession() as session:
        # -- fiat/metal ↔ fiat/metal --
        if src in FIAT and dst in FIAT:
            # UAH involved → NBU directly
            if src == "UAH" or dst == "UAH":
                foreign = dst if src == "UAH" else src
                rate_uah = await _nbu_rate_to_uah(session, foreign)
                if rate_uah is None:
                    return None
                return (1.0 / rate_uah) if src == "UAH" else rate_uah

            # XAU or XAG involved → bridge through UAH via NBU
            if src in METALS or dst in METALS:
                src_uah = await _nbu_rate_to_uah(session, src)
                dst_uah = await _nbu_rate_to_uah(session, dst)
                if src_uah is None or dst_uah is None:
                    return None
                return src_uah / dst_uah

            # MDL involved → bridge through UAH via NBU
            if src == "MDL" or dst == "MDL":
                foreign = dst if src == "MDL" else src
                rate_uah = await _nbu_rate_to_uah(session, foreign)
                mdl_uah = await _nbu_rate_to_uah(session, "MDL")
                if rate_uah is None or mdl_uah is None:
                    return None
                return (rate_uah / mdl_uah) if dst == "MDL" else (mdl_uah / rate_uah)

            # pure fiat, no metals, no UAH/MDL → Frankfurter
            return await _frankfurter_rate(session, src, dst)

        # -- crypto → fiat/metal --
        if src in CRYPTO and dst in FIAT:
            coin_id = CRYPTO_IDS[src]
            if dst in ("UAH", "MDL") or dst in METALS:
                price_usd = await _coingecko_rate(session, coin_id, "usd")
                usd_uah = await _nbu_rate_to_uah(session, "USD")
                if price_usd is None or usd_uah is None:
                    return None
                if dst == "UAH":
                    return price_usd * usd_uah
                dst_uah = await _nbu_rate_to_uah(session, dst)
                if dst_uah is None:
                    return None
                return price_usd * (usd_uah / dst_uah)
            return await _coingecko_rate(session, coin_id, dst.lower())

        # -- fiat/metal → crypto --
        if src in FIAT and dst in CRYPTO:
            coin_id = CRYPTO_IDS[dst]
            price_usd = await _coingecko_rate(session, coin_id, "usd")
            if price_usd is None:
                return None
            if src in ("UAH", "MDL") or src in METALS:
                src_uah = await _nbu_rate_to_uah(session, src)
                usd_uah = await _nbu_rate_to_uah(session, "USD")
                if src_uah is None or usd_uah is None:
                    return None
                return (src_uah / usd_uah) / price_usd
            rate = await _coingecko_rate(session, coin_id, src.lower())
            if rate is None:
                return None
            return 1.0 / rate

        # -- crypto ↔ crypto --
        if src in CRYPTO and dst in CRYPTO:
            src_usd = await _coingecko_rate(session, CRYPTO_IDS[src], "usd")
            dst_usd = await _coingecko_rate(session, CRYPTO_IDS[dst], "usd")
            if src_usd is None or dst_usd is None:
                return None
            return src_usd / dst_usd

    return None


# -- Formatting helper --


def fmt_amount(amount: float, currency: str) -> str:
    if currency in CRYPTO and currency != "USDT":
        return f"{amount:.8f}"
    if currency in METALS:
        return f"{amount:.4f}"
    return f"{amount:,.2f}"


# -- Handlers --


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ConvertFlow.choosing_from_cat)
    await message.answer(
        "From which <b>category</b> do you want to convert?",
        reply_markup=category_keyboard(),
    )


# -- Source: category chosen --
@dp.callback_query(ConvertFlow.choosing_from_cat, F.data.startswith("cat:"))
async def chose_from_cat(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        await callback.answer()
        return
    cat = callback.data.split(":")[1]
    await state.update_data(from_cat=cat)
    await state.set_state(ConvertFlow.choosing_from)
    label, _ = CATEGORIES[cat]
    await callback.message.edit_text(
        f"{label} — which currency to convert <b>from</b>?",
        reply_markup=currency_keyboard(cat),
    )
    await callback.answer()


# -- Source: back to category --
@dp.callback_query(ConvertFlow.choosing_from, F.data == "cat:back")
async def back_from_currency(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await state.set_state(ConvertFlow.choosing_from_cat)
    await callback.message.edit_text(
        "From which <b>category</b> do you want to convert?",
        reply_markup=category_keyboard(),
    )
    await callback.answer()


# -- Source: currency chosen --
@dp.callback_query(ConvertFlow.choosing_from, F.data.startswith("cur:"))
async def chose_from(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        await callback.answer()
        return
    src = callback.data.split(":")[1]
    await state.update_data(src=src)
    await state.set_state(ConvertFlow.choosing_to_cat)
    await callback.message.edit_text(
        f"Converting <b>from {src}</b>.\nTo which <b>category</b>?",
        reply_markup=category_keyboard(),
    )
    await callback.answer()


# -- Target: category chosen --
@dp.callback_query(ConvertFlow.choosing_to_cat, F.data.startswith("cat:"))
async def chose_to_cat(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        await callback.answer()
        return
    cat = callback.data.split(":")[1]
    data = await state.get_data()
    src = data["src"]
    await state.update_data(to_cat=cat)
    await state.set_state(ConvertFlow.choosing_to)
    label, _ = CATEGORIES[cat]
    await callback.message.edit_text(
        f"Converting <b>from {src}</b>.\n{label} — which currency to convert <b>to</b>?",
        reply_markup=currency_keyboard(cat, exclude=src),
    )
    await callback.answer()


# -- Target: back to category --
@dp.callback_query(ConvertFlow.choosing_to, F.data == "cat:back")
async def back_to_category(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    data = await state.get_data()
    src = data["src"]
    await state.set_state(ConvertFlow.choosing_to_cat)
    await callback.message.edit_text(
        f"Converting <b>from {src}</b>.\nTo which <b>category</b>?",
        reply_markup=category_keyboard(),
    )
    await callback.answer()


# -- Target: currency chosen --
@dp.callback_query(ConvertFlow.choosing_to, F.data.startswith("cur:"))
async def chose_to(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        await callback.answer()
        return
    dst = callback.data.split(":")[1]
    data = await state.get_data()
    src = data["src"]
    await state.update_data(dst=dst)
    await state.set_state(ConvertFlow.entering_amt)
    await callback.message.edit_text(
        f"<b>{src} → {dst}</b>\nEnter the amount in {src}:"
    )
    await callback.answer()


# -- Amount entered --
@dp.message(ConvertFlow.entering_amt)
async def entered_amount(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "Please enter a valid positive number, e.g. <code>1500</code>"
        )
        return

    data = await state.get_data()
    src, dst = data["src"], data["dst"]

    await message.answer("⏳ Fetching live rate...")

    rate = await fetch_rate(src, dst)
    if rate is None:
        await message.answer("❌ Could not fetch the exchange rate. Try again later.")
        await state.clear()
        return

    result = amount * rate
    await state.clear()

    await message.answer(
        f"{fmt_amount(amount, src)} {src} = <b>{fmt_amount(result, dst)} {dst}</b>\n"
        f"<i>rate: 1 {src} = {fmt_amount(rate, dst)} {dst}</i>\n\n"
        "Use /start to convert again."
    )


async def main() -> None:
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
