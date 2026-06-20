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
    choosing_from = State()  # waiting for source currency
    choosing_to = State()  # waiting for target currency
    entering_amt = State()  # waiting for the amount


# -- Currency sets --

# Frankfurter handles these (including XAU, XAG, CZK, RON)
FRANKFURTER = {"USD", "EUR", "GBP", "PLN", "CZK", "RON", "XAU", "XAG"}

# NBU handles UAH pairs + MDL (NBU publishes MDL rate)
NBU_SUPPORTED = {"USD", "EUR", "GBP", "PLN", "CZK", "RON", "MDL", "XAU", "XAG"}

# Al::l fiat + metals (no crypto)
FIAT = FRANKFURTER | {"UAH", "MDL"}

# Crypto coin IDs for CoinGecko
CRYPTO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "USDT": "tether",
}
CRYPTO = set(CRYPTO_IDS.keys())

# Display order for keyboard
CURRENCIES = [
    "UAH",
    "USD",
    "EUR",
    "GBP",
    "PLN",
    "CZK",
    "RON",
    "MDL",
    "XAU",
    "XAG",
    "BTC",
    "ETH",
    "SOL",
    "USDT",
]


# -- Rate fetching --


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
    """Returns how many DST equal 1 SRC via Frankfurter."""
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


async def fetch_rate(src: str, dst: str) -> float | None:
    async with aiohttp.ClientSession() as session:
        # -- fiat/metal ↔ fiat/metal --
        if src in FIAT and dst in FIAT:
            # UAH involved → use NBU
            if src == "UAH" or dst == "UAH":
                foreign = dst if src == "UAH" else src
                rate_uah = await _nbu_rate_to_uah(session, foreign)
                if rate_uah is None:
                    return None
                # 1 UAH = 1/rate_uah FOREIGN  |  1 FOREIGN = rate_uah UAH
                return (1.0 / rate_uah) if src == "UAH" else rate_uah

            # MDL involved but not UAH → convert both through UAH as bridge
            # MDL is not on Frankfurter, so: SRC→UAH→MDL
            if src == "MDL" or dst == "MDL":
                foreign = dst if src == "MDL" else src
                rate_uah = await _nbu_rate_to_uah(session, foreign)
                mdl_uah = await _nbu_rate_to_uah(session, "MDL")
                if rate_uah is None or mdl_uah is None:
                    return None
                # 1 SRC = rate_uah UAH = rate_uah/mdl_uah MDL
                return (rate_uah / mdl_uah) if dst == "MDL" else (mdl_uah / rate_uah)

            # everything else → Frankfurter
            return await _frankfurter_rate(session, src, dst)

        # -- crypto → fiat/metal --
        if src in CRYPTO and dst in FIAT:
            coin_id = CRYPTO_IDS[src]
            vs = dst.lower()
            # CoinGecko doesn't know UAH/MDL — bridge through USD
            if dst in ("UAH", "MDL"):
                price_usd = await _coingecko_rate(session, coin_id, "usd")
                usd_local = (
                    await _nbu_rate_to_uah(session, "USD") if dst == "UAH" else None
                )
                if dst == "MDL":
                    usd_uah = await _nbu_rate_to_uah(session, "USD")
                    mdl_uah = await _nbu_rate_to_uah(session, "MDL")
                    if price_usd is None or usd_uah is None or mdl_uah is None:
                        return None
                    return price_usd * (usd_uah / mdl_uah)
                if price_usd is None or usd_local is None:
                    return None
                return price_usd * usd_local
            return await _coingecko_rate(session, coin_id, vs)

        # -- fiat/metal → crypto --
        if src in FIAT and dst in CRYPTO:
            coin_id = CRYPTO_IDS[dst]
            vs = src.lower()
            if src in ("UAH", "MDL"):
                usd_rate = await _nbu_rate_to_uah(session, "USD")  # 1 USD = X UAH
                price_usd = await _coingecko_rate(session, coin_id, "usd")
                if usd_rate is None or price_usd is None:
                    return None
                if src == "UAH":
                    # 1 UAH = 1/usd_rate USD; 1 USD buys 1/price_usd coins
                    return (1.0 / usd_rate) / price_usd
                # MDL bridge
                mdl_uah = await _nbu_rate_to_uah(session, "MDL")
                if mdl_uah is None:
                    return None
                mdl_in_usd = mdl_uah / usd_rate
                return mdl_in_usd / price_usd
            price = await _coingecko_rate(session, coin_id, vs)
            if price is None:
                return None
            return 1.0 / price

        # -- crypto ↔ crypto --
        if src in CRYPTO and dst in CRYPTO:
            src_usd = await _coingecko_rate(session, CRYPTO_IDS[src], "usd")
            dst_usd = await _coingecko_rate(session, CRYPTO_IDS[dst], "usd")
            if src_usd is None or dst_usd is None:
                return None
            return src_usd / dst_usd

    return None


# -- Keyboard builder --


def currency_keyboard(exclude: str | None = None) -> InlineKeyboardMarkup:
    """Build a grid of currency buttons, optionally excluding one."""
    buttons = [
        InlineKeyboardButton(text=c, callback_data=f"cur:{c}")
        for c in CURRENCIES
        if c != exclude
    ]
    # arrange into rows of 3
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# -- Formatting helper --


def fmt_amount(amount: float, currency: str) -> str:
    if currency in CRYPTO and currency != "USDT":
        return f"{amount:.8f}"
    if currency in ("XAU", "XAG"):
        return f"{amount:.4f}"
    return f"{amount:,.2f}"


# -- Handlers
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ConvertFlow.choosing_from)
    await message.answer(
        "From which currency do you want to convert?", reply_markup=currency_keyboard()
    )


@dp.callback_query(ConvertFlow.choosing_from, F.data.startswith("cur:"))
async def chose_from(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        await callback.answer()
        return
    src = callback.data.split(":")[1]
    await state.update_data(src=src)
    await state.set_state(ConvertFlow.choosing_to)
    await callback.message.edit_text(
        f"Converting <b>from {src}</b>.\nTo which currency?",
        reply_markup=currency_keyboard(exclude=src),
    )
    await callback.answer()


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
