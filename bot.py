import os
import logging
import sys
import asyncio

import aiohttp
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
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
    choosing_lang: State = State()
    choosing_from_cat = State()  # picking category for source
    choosing_from = State()  # picking source currency
    choosing_to_cat = State()  # picking category for target
    choosing_to = State()  # picking target currency
    entering_amt = State()  # entering amount


# -- Language storage (in-memory, survives per bot session) --

user_lang: dict[int, str] = {}  # user_id -> "en" | "ua"


def get_lang(user_id: int) -> str:
    return user_lang.get(user_id, "en")


# -- Translations --

STRINGS: dict[str, dict[str, str]] = {
    "choose_lang": {
        "en": "🌐 Choose your language:",
        "ua": "🌐 Оберіть мову:",
    },
    "choose_from_cat": {
        "en": "From which <b>category</b> do you want to convert?",
        "ua": "З якої <b>категорії</b> ви хочете конвертувати?",
    },
    "choose_from_currency": {
        "en": "— which currency to convert <b>from</b>?",
        "ua": "— яку валюту конвертувати <b>з</b>?",
    },
    "converting_from": {
        "en": "Converting <b>from {src}</b>.\nTo which <b>category</b>?",
        "ua": "Конвертую <b>з {src}</b>.\nДо якої <b>категорії</b>?",
    },
    "choose_to_currency": {
        "en": "Converting <b>from {src}</b>.\n{label} — which currency to convert <b>to</b>?",
        "ua": "Конвертую <b>з {src}</b>.\n{label} — яку валюту конвертувати <b>в</b>?",
    },
    "enter_amount": {
        "en": "<b>{src} → {dst}</b>\nEnter the amount in {src}:",
        "ua": "<b>{src} → {dst}</b>\nВведіть суму в {src}:",
    },
    "fetching": {
        "en": "⏳ Fetching live rate...",
        "ua": "⏳ Отримую актуальний курс...",
    },
    "error_fetch": {
        "en": "❌ Could not fetch the exchange rate. Try again later.",
        "ua": "❌ Не вдалося отримати курс обміну. Спробуйте пізніше.",
    },
    "error_number": {
        "en": "Please enter a valid positive number, e.g. <code>1500</code>",
        "ua": "Будь ласка, введіть коректне позитивне число, наприклад <code>1500</code>",
    },
    "result": {
        "en": "{amount} {src} = <b>{result} {dst}</b>\n<i>rate: 1 {src} = {rate} {dst}</i>\n\nUse /start to convert again.",
        "ua": "{amount} {src} = <b>{result} {dst}</b>\n<i>курс: 1 {src} = {rate} {dst}</i>\n\nВикористайте /start для нової конвертації.",
    },
    "back": {
        "en": "⬅️ Back",
        "ua": "⬅️ Назад",
    },
    "lang_updated": {
        "en": "🌐 Language set to English.",
        "ua": "🌐 Мову змінено на українську.",
    },
}


def t(key: str, user_id: int, **kwargs: str) -> str:
    lang = get_lang(user_id)
    text = STRINGS[key].get(lang, STRINGS[key]["en"])
    return text.format(**kwargs) if kwargs else text


# -- Currency sets --

METALS = {"XAU", "XAG"}
FRANKFURTER = {"USD", "EUR", "GBP", "PLN", "CZK", "RON"}
FIAT = FRANKFURTER | {"UAH", "MDL"} | METALS

CRYPTO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "USDT": "tether",
}
CRYPTO = set(CRYPTO_IDS.keys())

CAT_FIAT = ["UAH", "USD", "EUR", "GBP", "PLN", "CZK", "RON", "MDL"]
CAT_METALS = ["XAU", "XAG"]
CAT_CRYPTO = ["BTC", "ETH", "SOL", "USDT"]

CATEGORIES: dict[str, tuple[dict[str, str], list[str]]] = {
    "fiat": ({"en": "💵 Fiat", "ua": "💵 Фіат"}, CAT_FIAT),
    "metals": ({"en": "🥇 Metals", "ua": "🥇 Метали"}, CAT_METALS),
    "crypto": ({"en": "₿ Crypto", "ua": "₿ Крипто"}, CAT_CRYPTO),
}


def cat_label(cat_key: str, user_id: int) -> str:
    lang = get_lang(user_id)
    labels, _ = CATEGORIES[cat_key]
    return labels.get(lang, labels["en"])


# -- Keyboard builders --


def lang_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en"),
                InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang:ua"),
            ]
        ]
    )


def category_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=cat_label(key, user_id), callback_data=f"cat:{key}"
            )
            for key in CATEGORIES
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def currency_keyboard(
    cat_key: str, user_id: int, exclude: str | None = None
) -> InlineKeyboardMarkup:
    _, currencies = CATEGORIES[cat_key]
    buttons = [
        InlineKeyboardButton(text=c, callback_data=f"cur:{c}")
        for c in currencies
        if c != exclude
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append(
        [InlineKeyboardButton(text=t("back", user_id), callback_data="cat:back")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# -- API helpers --


async def _nbu_rate_to_uah(
    session: aiohttp.ClientSession, foreign: str
) -> float | None:
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
    url = f"https://api.frankfurter.app/latest?from={src}&to={dst}"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
        if r.status != 200:
            return None
        data = await r.json(content_type=None)
        return data["rates"].get(dst)


async def _coingecko_rate(
    session: aiohttp.ClientSession, coin_id: str, vs: str
) -> float | None:
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies={vs}"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
        if r.status != 200:
            return None
        data = await r.json(content_type=None)
        return data.get(coin_id, {}).get(vs)


# -- Rate fetching --


async def fetch_rate(src: str, dst: str) -> float | None:
    async with aiohttp.ClientSession() as session:
        if src in FIAT and dst in FIAT:
            if src == "UAH" or dst == "UAH":
                foreign = dst if src == "UAH" else src
                rate_uah = await _nbu_rate_to_uah(session, foreign)
                if rate_uah is None:
                    return None
                return (1.0 / rate_uah) if src == "UAH" else rate_uah

            if src in METALS or dst in METALS:
                src_uah = await _nbu_rate_to_uah(session, src)
                dst_uah = await _nbu_rate_to_uah(session, dst)
                if src_uah is None or dst_uah is None:
                    return None
                return src_uah / dst_uah

            if src == "MDL" or dst == "MDL":
                foreign = dst if src == "MDL" else src
                rate_uah = await _nbu_rate_to_uah(session, foreign)
                mdl_uah = await _nbu_rate_to_uah(session, "MDL")
                if rate_uah is None or mdl_uah is None:
                    return None
                return (rate_uah / mdl_uah) if dst == "MDL" else (mdl_uah / rate_uah)

            return await _frankfurter_rate(session, src, dst)

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
    if not message.from_user:
        return
    await state.clear()
    user_id = message.from_user.id

    # show lang picker only on first launch
    if user_id not in user_lang:
        await state.set_state(ConvertFlow.choosing_lang)
        await message.answer(t("choose_lang", user_id), reply_markup=lang_keyboard())
    else:
        await state.set_state(ConvertFlow.choosing_from_cat)
        await message.answer(
            t("choose_from_cat", user_id), reply_markup=category_keyboard(user_id)
        )


@dp.message(Command("language"))
async def cmd_language(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    await state.clear()
    await state.set_state(ConvertFlow.choosing_lang)
    await message.answer(
        t("choose_lang", message.from_user.id), reply_markup=lang_keyboard()
    )


@dp.callback_query(ConvertFlow.choosing_lang, F.data.startswith("lang:"))
async def chose_lang(callback: CallbackQuery, state: FSMContext) -> None:
    if (
        not callback.data
        or not isinstance(callback.message, Message)
        or not callback.from_user
    ):
        await callback.answer()
        return
    lang = callback.data.split(":")[1]
    user_id = callback.from_user.id
    user_lang[user_id] = lang

    await state.set_state(ConvertFlow.choosing_from_cat)
    await callback.message.edit_text(
        t("lang_updated", user_id) + "\n\n" + t("choose_from_cat", user_id),
        reply_markup=category_keyboard(user_id),
    )
    await callback.answer()


# -- Source: category chosen --
@dp.callback_query(ConvertFlow.choosing_from_cat, F.data.startswith("cat:"))
async def chose_from_cat(callback: CallbackQuery, state: FSMContext) -> None:
    if (
        not callback.data
        or not isinstance(callback.message, Message)
        or not callback.from_user
    ):
        await callback.answer()
        return
    cat = callback.data.split(":")[1]
    user_id = callback.from_user.id
    await state.update_data(from_cat=cat)
    await state.set_state(ConvertFlow.choosing_from)
    await callback.message.edit_text(
        f"{cat_label(cat, user_id)} {t('choose_from_currency', user_id)}",
        reply_markup=currency_keyboard(cat, user_id),
    )
    await callback.answer()


# -- Source: back to category --
@dp.callback_query(ConvertFlow.choosing_from, F.data == "cat:back")
async def back_from_currency(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message) or not callback.from_user:
        await callback.answer()
        return
    user_id = callback.from_user.id
    await state.set_state(ConvertFlow.choosing_from_cat)
    await callback.message.edit_text(
        t("choose_from_cat", user_id), reply_markup=category_keyboard(user_id)
    )
    await callback.answer()


# -- Source: currency chosen --
@dp.callback_query(ConvertFlow.choosing_from, F.data.startswith("cur:"))
async def chose_from(callback: CallbackQuery, state: FSMContext) -> None:
    if (
        not callback.data
        or not isinstance(callback.message, Message)
        or not callback.from_user
    ):
        await callback.answer()
        return
    src = callback.data.split(":")[1]
    user_id = callback.from_user.id
    await state.update_data(src=src)
    await state.set_state(ConvertFlow.choosing_to_cat)
    await callback.message.edit_text(
        t("converting_from", user_id, src=src), reply_markup=category_keyboard(user_id)
    )
    await callback.answer()


# -- Target: category chosen --
@dp.callback_query(ConvertFlow.choosing_to_cat, F.data.startswith("cat:"))
async def chose_to_cat(callback: CallbackQuery, state: FSMContext) -> None:
    if (
        not callback.data
        or not isinstance(callback.message, Message)
        or not callback.from_user
    ):
        await callback.answer()
        return
    cat = callback.data.split(":")[1]
    user_id = callback.from_user.id
    data = await state.get_data()
    src = data["src"]
    await state.update_data(to_cat=cat)
    await state.set_state(ConvertFlow.choosing_to)
    await callback.message.edit_text(
        t("choose_to_currency", user_id, src=src, label=cat_label(cat, user_id)),
        reply_markup=currency_keyboard(cat, user_id, exclude=src),
    )
    await callback.answer()


# -- Target: back to category --
@dp.callback_query(ConvertFlow.choosing_to, F.data == "cat:back")
async def back_to_category(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message) or not callback.from_user:
        await callback.answer()
        return
    user_id = callback.from_user.id
    data = await state.get_data()
    src = data["src"]
    await state.set_state(ConvertFlow.choosing_to_cat)
    await callback.message.edit_text(
        t("converting_from", user_id, src=src), reply_markup=category_keyboard(user_id)
    )
    await callback.answer()


# -- Target: currency chosen --
@dp.callback_query(ConvertFlow.choosing_to, F.data.startswith("cur:"))
async def chose_to(callback: CallbackQuery, state: FSMContext) -> None:
    if (
        not callback.data
        or not isinstance(callback.message, Message)
        or not callback.from_user
    ):
        await callback.answer()
        return
    dst = callback.data.split(":")[1]
    user_id = callback.from_user.id
    data = await state.get_data()
    src = data["src"]
    await state.update_data(dst=dst)
    await state.set_state(ConvertFlow.entering_amt)
    await callback.message.edit_text(t("enter_amount", user_id, src=src, dst=dst))
    await callback.answer()


# -- Amount entered --
@dp.message(ConvertFlow.entering_amt)
async def entered_amount(message: Message, state: FSMContext) -> None:
    if not message.text or not message.from_user:
        return
    user_id = message.from_user.id

    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer(t("error_number", user_id))
        return

    data = await state.get_data()
    src, dst = data["src"], data["dst"]

    await message.answer(t("fetching", user_id))

    rate = await fetch_rate(src, dst)
    if rate is None:
        await message.answer(t("error_fetch", user_id))
        await state.clear()
        return

    result = amount * rate
    await state.clear()

    await message.answer(
        t(
            "result",
            user_id,
            amount=fmt_amount(amount, src),
            src=src,
            result=fmt_amount(result, dst),
            dst=dst,
            rate=fmt_amount(rate, dst),
        )
    )


async def main() -> None:
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
