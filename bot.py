import os
import logging
import sys
import asyncio

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
    message,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

load_dotenv()
TOKEN = os.environ["BOT_TOKEN"]

dp = Dispatcher()


# --- FSM States ---


class ConvertFlow(StatesGroup):
    choosing_from = State()  # waiting for source currency
    choosing_to = State()  # waiting for target currency
    entering_amt = State()  # waiting for the amount


# -- Available currencies --

CURRENCIES = ["UAH", "USD", "EUR", "BTC", "GBP", "PLN"]


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

    # placeholder rate — real fetch comes next
    rate = 1.0
    result = amount * rate

    await state.clear()  # reset FSM

    await message.answer(
        f"{amount:,.2f} {src} = <b>{result:,.2f} {dst}</b>\n\n"
        f"(rate: 1 {src} = {rate} {dst})\n\n"
        "Use /start to convert again."
    )


async def main() -> None:
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
