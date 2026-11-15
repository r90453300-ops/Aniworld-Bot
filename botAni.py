
import asyncio
import logging
import os
import re
from html import escape

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# =====================================
# ANIJON BOT SOZLAMALARI
# =====================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

PUBLIC_CHANNEL_ID = int(
    os.getenv("PUBLIC_CHANNEL_ID", "-1004349473919")
)
PUBLIC_CHANNEL_USERNAME = os.getenv(
    "PUBLIC_CHANNEL_USERNAME", "@AniJon_Anime"
).strip()

STORAGE_CHANNEL_ID = int(
    os.getenv("STORAGE_CHANNEL_ID", "-1004368926932")
)

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "8975803698").split(",")
    if x.strip().isdigit()
}

DB_PATH = os.getenv("DB_PATH", "anijon.db")
SITE_URL = os.getenv(
    "SITE_URL", "https://anijonbot.netlify.app"
).strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("AniJon")

dp = Dispatcher(storage=MemoryStorage())


# =====================================
# HOLATLAR
# =====================================

class AddAnime(StatesGroup):
    waiting_data = State()


class AddEpisode(StatesGroup):
    waiting_data = State()
    waiting_video = State()


class PublishAnime(StatesGroup):
    waiting_code = State()


class SearchAnime(StatesGroup):
    waiting_code = State()


# =====================================
# YORDAMCHI FUNKSIYALAR
# =====================================

def is_admin(user_id):
    return user_id in ADMIN_IDS


def valid_code(code):
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,32}", code))


def normalize_code(code):
    return code.strip().upper()


# =====================================
# DATABASE
# =====================================

async def init_db():
    folder = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(folder, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS anime (
                code TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT ''
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                anime_code TEXT NOT NULL,
                episode_no INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                file_type TEXT NOT NULL,
                caption TEXT DEFAULT '',
                PRIMARY KEY (anime_code, episode_no),
                FOREIGN KEY (anime_code)
                    REFERENCES anime(code)
                    ON DELETE CASCADE
            )
        """)

        await db.commit()

    log.info("Database tayyor: %s", DB_PATH)


async def get_anime(code):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT code, title, description
            FROM anime
            WHERE UPPER(code) = UPPER(?)
            """,
            (code,),
        )
        return await cur.fetchone()


async def get_episode(code, number):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT file_id, file_type, caption
            FROM episodes
            WHERE UPPER(anime_code) = UPPER(?)
            AND episode_no = ?
            """,
            (code, number),
        )
        return await cur.fetchone()


async def get_episode_numbers(code):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT episode_no
            FROM episodes
            WHERE UPPER(anime_code) = UPPER(?)
            ORDER BY episode_no
            """,
            (code,),
        )
        rows = await cur.fetchall()
        return [row[0] for row in rows]


async def get_statistics():
    async with aiosqlite.connect(DB_PATH) as db:
        anime = await (
            await db.execute("SELECT COUNT(*) FROM anime")
        ).fetchone()

        episodes = await (
            await db.execute("SELECT COUNT(*) FROM episodes")
        ).fetchone()

    return anime[0], episodes[0]


# =====================================
# OBUNA TEKSHIRISH
# =====================================

async def subscribed(bot, user_id):
    try:
        member = await bot.get_chat_member(
            PUBLIC_CHANNEL_ID, user_id
        )

        return (
            member.status in ("member", "administrator", "creator")
            or getattr(member, "is_member", False)
        )

    except TelegramAPIError as exc:
        log.warning("Obuna tekshirish xatosi: %s", exc)
        return False


def subscription_keyboard(payload=""):
    check_data = "subcheck"

    if payload and len(payload) <= 55:
        check_data = f"subcheck:{payload}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📢 Kanalga obuna bo‘lish",
                url=(
                    "https://t.me/"
                    + PUBLIC_CHANNEL_USERNAME.lstrip("@")
                ),
            )
        ],
        [
            InlineKeyboardButton(
                text="✅ Obunani tekshirish",
                callback_data=check_data,
            )
        ],
    ])


# =====================================
# KLAVIATURALAR
# =====================================

def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🔎 Anime kodini kiritish",
                callback_data="search:code",
            )
        ],
        [
            InlineKeyboardButton(
                text="🌐 Anime saytiga kirish",
                url=SITE_URL,
            )
        ],
    ])


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="➕ Anime qo‘shish",
                callback_data="admin:addanime",
            )
        ],
        [
            InlineKeyboardButton(
                text="🎬 Qism qo‘shish",
                callback_data="admin:addepisode",
            )
        ],
        [
            InlineKeyboardButton(
                text="📚 Anime ro‘yxati",
                callback_data="admin:list",
            )
        ],
        [
            InlineKeyboardButton(
                text="📣 Kanalga chiqarish",
                callback_data="admin:publish",
            )
        ],
        [
            InlineKeyboardButton(
                text="📊 Statistika",
                callback_data="admin:stats",
            )
        ],
    ])


def episode_keyboard(code, numbers):
    buttons = []
    row = []

    for number in numbers:
        row.append(
            InlineKeyboardButton(
                text=f"{number}-qism",
                callback_data=f"get:{code}:{number}",
            )
        )

        if len(row) == 3:
            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# =====================================
# ANIME QIDIRISH
# =====================================

async def show_anime(message, code):
    code = normalize_code(code)

    if not valid_code(code):
        await message.answer(
            "❌ Anime kodi noto‘g‘ri.\n"
            "Faqat harf, raqam, _ va - ishlating."
        )
        return

    anime = await get_anime(code)

    if not anime:
        await message.answer(
            "❌ Bunday anime kodi topilmadi.\n"
            "Kodni qayta tekshirib ko‘ring."
        )
        return

    numbers = await get_episode_numbers(code)

    text = (
        f"🎬 <b>{escape(anime[1])}</b>\n\n"
        f"🔑 Kodi: <code>{escape(anime[0])}</code>\n"
    )

    if anime[2]:
        text += f"\n📝 {escape(anime[2])}\n"

    if not numbers:
        text += "\nHozircha qismlar mavjud emas."
        await message.answer(text)
        return

    text += "\n📺 Kerakli qismni tanlang:"

    await message.answer(
        text,
        reply_markup=episode_keyboard(code, numbers),
    )


# =====================================
# QISM YUBORISH
# =====================================

async def deliver_episode(target, bot, code, number):
    anime = await get_anime(code)
    episode = await get_episode(code, number)

    if not anime:
        error = "❌ Anime kodi topilmadi."
        if isinstance(target, CallbackQuery):
            await target.answer(error, show_alert=True)
        else:
            await target.answer(error)
        return

    if not episode:
        error = "❌ Bu qism hali Omborga qo‘shilmagan."
        if isinstance(target, CallbackQuery):
            await target.answer(error, show_alert=True)
        else:
            await target.answer(error)
        return

    file_id, file_type, caption = episode

    text = caption or (
        f"<b>{escape(anime[1])}</b>\n"
        f"🎬 {number}-qism"
    )

    if isinstance(target, CallbackQuery):
        chat_id = target.message.chat.id
        await target.answer("📤 Qism yuborilmoqda...")
    else:
        chat_id = target.chat.id

    try:
        if file_type == "video":
            await bot.send_video(
                chat_id=chat_id,
                video=file_id,
                caption=text,
            )
        else:
            await bot.send_document(
                chat_id=chat_id,
                document=file_id,
                caption=text,
            )

    except TelegramAPIError as exc:
        log.exception("Qism yuborishda xato: %s", exc)

        await bot.send_message(
            chat_id,
            "❌ Qismni yuborib bo‘lmadi. "
            "Administratorga murojaat qiling.",
        )


# =====================================
# START
# =====================================

@dp.message(CommandStart())
async def start_handler(message: Message, bot: Bot):
    parts = (message.text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""

    if not await subscribed(bot, message.from_user.id):
        await message.answer(
            "👋 <b>AniJon botiga xush kelibsiz!</b>\n\n"
            "Botdan foydalanish uchun avval kanalimizga obuna bo‘ling.",
            reply_markup=subscription_keyboard(payload),
        )
        return

    if payload:
        match = re.fullmatch(
            r"ep_([A-Za-z0-9_-]{1,32})_(\d+)", payload
        )

        anime_match = re.fullmatch(
            r"anime_([A-Za-z0-9_-]{1,32})", payload
        )

        if match:
            await deliver_episode(
                message, bot, match.group(1), int(match.group(2))
            )
            return

        if anime_match:
            await show_anime(message, anime_match.group(1))
            return

    await message.answer(
        "👋 <b>AniJon botiga xush kelibsiz!</b>\n\n"
        "Anime kodini kiriting yoki saytimizga o'ting.",
        reply_markup=main_keyboard(),
    )


# =====================================
# OBUNANI QAYTA TEKSHIRISH
# =====================================

@dp.callback_query(F.data.startswith("subcheck"))
async def subscription_check(callback: CallbackQuery, bot: Bot):
    payload = ""

    if ":" in (callback.data or ""):
        payload = callback.data.split(":", 1)[1]

    if not await subscribed(bot, callback.from_user.id):
        await callback.answer(
            "Avval kanalga obuna bo‘ling.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        "✅ Obuna tasdiqlandi!\n"
        "Endi AniJon botidan foydalanishingiz mumkin."
    )

    if payload:
        match = re.fullmatch(
            r"ep_([A-Za-z0-9_-]{1,32})_(\d+)", payload
        )
        anime_match = re.fullmatch(
            r"anime_([A-Za-z0-9_-]{1,32})", payload
        )

        if match:
            await deliver_episode(
                callback, bot, match.group(1), int(match.group(2))
            )
        elif anime_match:
            await show_anime(
                callback.message, anime_match.group(1)
            )
        else:
            await callback.answer()
    else:
        await callback.message.answer(
            "🔎 Anime kodini qidirishingiz mumkin.",
            reply_markup=main_keyboard(),
        )
        await callback.answer("Tayyor!")


# =====================================
# ANIME KODI ORQALI QIDIRISH
# =====================================

@dp.callback_query(F.data == "search:code")
async def search_button(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SearchAnime.waiting_code)

    await callback.message.answer(
        "🔎 Anime kodini yuboring.\n\n"
        "Masalan: <code>AN001</code>"
    )

    await callback.answer()


@dp.message(Command("search"))
async def search_command(message: Message, state: FSMContext):
    parts = (message.text or "").split(maxsplit=1)

    if len(parts) > 1:
        await show_anime(message, parts[1])
        return

    await state.set_state(SearchAnime.waiting_code)
    await message.answer(
        "🔎 Anime kodini yuboring.\n"
        "Masalan: <code>AN001</code>"
    )


@dp.message(SearchAnime.waiting_code)
async def search_by_code(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Iltimos, anime kodini matn ko‘rinishida yuboring.")
        return

    await state.clear()
    await show_anime(message, message.text)


# =====================================
# QISM TUGMASI
# =====================================

@dp.callback_query(F.data.startswith("get:"))
async def get_episode_callback(callback: CallbackQuery, bot: Bot):
    if not await subscribed(bot, callback.from_user.id):
        await callback.message.answer(
            "Avval kanalga obuna bo‘ling.",
            reply_markup=subscription_keyboard(),
        )
        await callback.answer()
        return

    try:
        _, code, number = callback.data.split(":")
        await deliver_episode(
            callback, bot, code, int(number)
        )
    except (ValueError, TypeError):
        await callback.answer(
            "Noto‘g‘ri qism.", show_alert=True
        )


# =====================================
# ADMIN PANEL
# =====================================

@dp.message(Command("admin"))
async def admin_handler(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Sizda admin huquqi yo‘q.")
        return

    await message.answer(
        "🛠 <b>AniJon admin paneli</b>",
        reply_markup=admin_keyboard(),
    )


# =====================================
# ANIME QO‘SHISH
# =====================================

@dp.callback_query(F.data == "admin:addanime")
async def admin_add_anime(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    await state.set_state(AddAnime.waiting_data)

    await callback.message.answer(
        "Anime ma’lumotini yuboring:\n\n"
        "<code>KOD | Anime nomi | Tavsif</code>\n\n"
        "Masalan:\n"
        "<code>AN001 | Naruto | Ninja haqidagi anime</code>"
    )

    await callback.answer()


@dp.message(AddAnime.waiting_data)
async def save_anime(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    if not message.text or "|" not in message.text:
        await message.answer(
            "Format xato. KOD | Anime nomi | Tavsif"
        )
        return

    parts = [x.strip() for x in message.text.split("|", 2)]

    code = normalize_code(parts[0])
    title = parts[1] if len(parts) > 1 else ""
    description = parts[2] if len(parts) > 2 else ""

    if not valid_code(code) or not title:
        await message.answer("Kod yoki anime nomi noto‘g‘ri.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO anime(code, title, description)
            VALUES(?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                title=excluded.title,
                description=excluded.description
            """,
            (code, title, description),
        )
        await db.commit()

    await state.clear()

    await message.answer(
        f"✅ <b>{escape(title)}</b> bazaga qo‘shildi.\n"
        f"Anime kodi: <code>{code}</code>"
    )


# =====================================
# QISM QO‘SHISH
# =====================================

@dp.callback_query(F.data == "admin:addepisode")
async def admin_add_episode(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    await state.set_state(AddEpisode.waiting_data)

    await callback.message.answer(
        "Anime kodi va qism raqamini yuboring:\n\n"
        "<code>KOD | QISM</code>\n"
        "Masalan: <code>AN001 | 1</code>"
    )

    await callback.answer()


@dp.message(AddEpisode.waiting_data)
async def episode_metadata(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    if not message.text or "|" not in message.text:
        await message.answer("Format: <code>AN001 | 1</code>")
        return

    code, number_text = [
        x.strip() for x in message.text.split("|", 1)
    ]

    code = normalize_code(code)

    if not valid_code(code) or not number_text.isdigit():
        await message.answer("Anime kodi yoki qism raqami noto‘g‘ri.")
        return

    number = int(number_text)

    if number < 1:
        await message.answer("Qism raqami 1 yoki undan katta bo‘lishi kerak.")
        return

    anime = await get_anime(code)

    if not anime:
        await message.answer("Anime topilmadi. Avval anime qo‘shing.")
        return

    await state.update_data(
        code=code,
        episode_no=number,
    )

    await state.set_state(AddEpisode.waiting_video)

    await message.answer(
        f"🎬 <b>{escape(anime[1])}</b> — {number}-qism.\n\n"
        "Endi video yoki video faylni yuboring.\n"
        "Bot uni Ombor kanaliga saqlaydi."
    )


@dp.message(AddEpisode.waiting_video, F.video | F.document)
async def save_episode_file(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()

    code = data["code"]
    number = data["episode_no"]
    anime = await get_anime(code)

    if not anime:
        await message.answer("Anime topilmadi. Jarayonni qaytadan boshlang.")
        await state.clear()
        return

    try:
        storage_caption = (
            f"{anime[1]} — {number}-qism | {code}"
        )

        if message.video:
            stored = await bot.send_video(
                STORAGE_CHANNEL_ID,
                message.video.file_id,
                caption=storage_caption,
            )

            file_id = stored.video.file_id
            file_type = "video"

        else:
            stored = await bot.send_document(
                STORAGE_CHANNEL_ID,
                message.document.file_id,
                caption=storage_caption,
            )

            file_id = stored.document.file_id
            file_type = "document"

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO episodes(
                    anime_code, episode_no, file_id, file_type, caption
                )
                VALUES(?, ?, ?, ?, ?)
