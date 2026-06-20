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


# -- Available currencies --

CURRENCIES = ["UAH", "USD", "EUR", "BTC", "GBP", "PLN"]
FIAT = {"UAH", "USD", "EUR", "GBP", "PLN"}
CRYPTO = {"BTC"}

# -- Rate fetching --


async def fetch_rate(src: str, dst: str) -> float | None:
    """
    Returns how many DST units equal 1 SRC unit.
    Handles fiat↔fiat, fiat→BTC, BTC→fiat.
    """
    async with aiohttp.ClientSession() as session:
        # fiat ↔ fiat
        if src in FIAT and dst in FIAT:
            url = f"https://api.frankfurter.app/latest?from={src}&to={dst}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                return data["rates"].get(dst)

        # fiat → BTC : get BTC price in src currency, then invert
        if src in FIAT and dst == "BTC":
            coin_id = "bitcoin"
            currency = src.lower()
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies={currency}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                btc_in_src = data.get(coin_id, {}).get(currency)
                if not btc_in_src:
                    return None
                return 1.0 / btc_in_src  # 1 SRC = X BTC

        # BTC → fiat : get BTC price in dst currency
        if src == "BTC" and dst in FIAT:
            coin_id = "bitcoin"
            currency = dst.lower()
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies={currency}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                return data.get(coin_id, {}).get(currency)

    return None  # unsupported pair


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
        reply_markup=currency_keyboard(exclude=src),  # hide the already-chosen one
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
    # validate input
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

    # BTC gets more decimal places
    result_fmt = f"{result:.8f}" if dst == "BTC" else f"{result:,.2f}"
    rate_fmt = f"{rate:.8f}" if dst == "BTC" else f"{rate:,.4f}"

    await message.answer(
        f"{amount:,.2f} {src} = <b>{result_fmt} {dst}</b>\n"
        f"<i>rate: 1 {src} = {rate_fmt} {dst}</i>\n\n"
        "Use /start to convert again."
    )


async def main() -> None:
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
