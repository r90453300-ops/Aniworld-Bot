import asyncio
import logging
import os
import re
from io import BytesIO

import aiosqlite
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)

# ==============================
# SOZLAMALAR
# ==============================

BOT_TOKEN = os.getenv("BOT_TOKEN")

PUBLIC_CHANNEL_ID = int(
    os.getenv("PUBLIC_CHANNEL_ID", "-1004349473919")
)

PUBLIC_CHANNEL_USERNAME = os.getenv(
    "PUBLIC_CHANNEL_USERNAME", "@AniJon_Anime"
)

STORAGE_CHANNEL_ID = int(
    os.getenv("STORAGE_CHANNEL_ID", "-1004368926932")
)

OWNER_ID = int(os.getenv("OWNER_ID", "8975803698"))

DB_PATH = os.getenv("DB_PATH", "anijon.db")

SITE_URL = os.getenv(
    "SITE_URL", "https://aniworlddub.netlify.app"
)

PORT = int(os.getenv("PORT", "8080"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN Railway Variables ichida yo'q!")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher(storage=MemoryStorage())


# ==============================
# FSM HOLATLARI
# ==============================

class AnimeAdd(StatesGroup):
    code = State()
    title = State()
    photo = State()
    description = State()


class EpisodeAdd(StatesGroup):
    code = State()
    videos = State()


class AdminAdd(StatesGroup):
    user_id = State()


class AdminRemove(StatesGroup):
    user_id = State()


# ==============================
# DATABASE
# ==============================

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute("""
        CREATE TABLE IF NOT EXISTS anime (
            code TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            photo_id TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anime_code TEXT NOT NULL,
            episode_no INTEGER NOT NULL,
            file_id TEXT NOT NULL,
            file_type TEXT,
            caption TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            user_id INTEGER PRIMARY KEY
        )
        """)

        # Eski bazada photo_id bo'lmasa, qo'shadi
        try:
            await db.execute(
                "ALTER TABLE anime ADD COLUMN photo_id TEXT"
            )
        except Exception:
            pass

        # Egani administratorlar ro'yxatiga qo'shish
        await db.execute(
            "INSERT OR IGNORE INTO admin_users (user_id) VALUES (?)",
            (OWNER_ID,)
        )

        await db.commit()


async def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id FROM admin_users WHERE user_id=?",
            (user_id,)
        )
        result = await cursor.fetchone()

    return result is not None


async def get_admins():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id FROM admin_users ORDER BY user_id"
        )
        return await cursor.fetchall()


async def get_anime(code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM anime WHERE code=?",
            (code.upper(),)
        )
        return await cursor.fetchone()


async def get_episode_numbers(code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT episode_no FROM episodes
            WHERE anime_code=?
            ORDER BY episode_no
            """,
            (code.upper(),)
        )
        rows = await cursor.fetchall()

    return [row[0] for row in rows]


async def get_episode(code: str, number: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT file_id, file_type, caption
            FROM episodes
            WHERE anime_code=? AND episode_no=?
            """,
            (code.upper(), number)
        )
        return await cursor.fetchone()
    # ==============================
# TUGMALAR
# ==============================

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔎 Anime qidirish")],
            [KeyboardButton(text="🌐 Saytga kirish")]
        ],
        resize_keyboard=True
    )


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="➕ Anime qo'shish",
                callback_data="add_anime"
            )
        ],
        [
            InlineKeyboardButton(
                text="🎬 Qism qo'shish",
                callback_data="add_episode"
            )
        ],
        [
            InlineKeyboardButton(
                text="📚 Anime ro'yxati",
                callback_data="anime_list"
            )
        ],
        [
            InlineKeyboardButton(
                text="👥 Admin qo'shish",
                callback_data="add_admin"
            )
        ],
        [
            InlineKeyboardButton(
                text="➖ Adminni o'chirish",
                callback_data="remove_admin"
            )
        ],
        [
            InlineKeyboardButton(
                text="📊 Statistika",
                callback_data="statistics"
            )
        ]
    ])


def subscription_keyboard(payload=""):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📢 Kanalga obuna bo'lish",
                url=f"https://t.me/{PUBLIC_CHANNEL_USERNAME.replace('@', '')}"
            )
        ],
        [
            InlineKeyboardButton(
                text="✅ Obunani tekshirish",
                callback_data=f"subcheck|{payload}"
            )
        ]
    ])


async def check_subscription(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(
            PUBLIC_CHANNEL_ID, user_id
        )
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False


async def send_episode(chat_id: int, code: str, number: int):
    anime = await get_anime(code)
    episode = await get_episode(code, number)

    if not anime or not episode:
        await bot.send_message(
            chat_id,
            "❌ Anime yoki qism topilmadi."
        )
        return

    file_id, file_type, saved_caption = episode

    caption = (
        f"<b>{anime['title']}</b>\n"
        f"🎬 {number}-qism\n"
        f"🔖 Kod: {code}"
    )

    if file_type == "document":
        await bot.send_document(
            chat_id,
            file_id,
            caption=caption
        )
    else:
        await bot.send_video(
            chat_id,
            file_id,
            caption=caption
        )


# ==============================
# SAYT UCHUN API
# ==============================

async def api_anime_list(request):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT code, title, description, photo_id FROM anime ORDER BY title"
        )
        rows = await cursor.fetchall()

    base = f"{request.scheme}://{request.host}"

    result = []
    for row in rows:
        result.append({
            "code": row["code"],
            "title": row["title"],
            "description": row["description"] or "",
            "poster_url": (
                f"{base}/poster/{row['code']}"
                if row["photo_id"] else None
            )
        })

    return web.json_response(
        {"anime": result},
        headers={"Access-Control-Allow-Origin": "*"}
    )


async def api_anime_detail(request):
    code = request.match_info["code"].upper()
    anime = await get_anime(code)

    if not anime:
        return web.json_response(
            {"error": "Anime topilmadi"},
            status=404
        )

    numbers = await get_episode_numbers(code)

    base = f"{request.scheme}://{request.host}"

    return web.json_response({
        "code": code,
        "title": anime["title"],
        "description": anime["description"] or "",
        "poster_url": (
            f"{base}/poster/{code}"
            if anime["photo_id"] else None
        ),
        "episodes": [
            {
                "number": number,
                "telegram_url": (
                    f"https://t.me/AniWorldDub_Bot"
                    f"?start=ep_{code}_{number}"
                )
            }
            for number in numbers
        ]
    }, headers={"Access-Control-Allow-Origin": "*"})


async def api_poster(request):
    code = request.match_info["code"].upper()
    anime = await get_anime(code)

    if not anime or not anime["photo_id"]:
        raise web.HTTPNotFound()

    try:
        file = await bot.get_file(anime["photo_id"])
        image_buffer = BytesIO()

        await bot.download_file(
            file.file_path,
            destination=image_buffer
        )

        return web.Response(
            body=image_buffer.getvalue(),
            content_type="image/jpeg",
            headers={
                "Cache-Control": "public, max-age=3600",
                "Access-Control-Allow-Origin": "*"
            }
        )

    except Exception as error:
        logger.exception("Poster yuklashda xato: %s", error)
        raise web.HTTPNotFound()


async def api_options(request):
    return web.Response(
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*"
        }
    )


async def start_api():
    app = web.Application()

    app.router.add_get("/api/anime", api_anime_list)
    app.router.add_get("/api/anime/{code}", api_anime_detail)
    app.router.add_get("/poster/{code}", api_poster)

    app.router.add_route("OPTIONS", "/{tail:.*}", api_options)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )

    await site.start()
    logger.info("API server ishga tushdi: port %s", PORT)

    return runner

# ==============================
# START
# ==============================

@dp.message(CommandStart())
async def start_handler(message: Message):
    user_id = message.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            (user_id,)
        )
        await db.commit()

    payload = message.text.split(maxsplit=1)
    argument = payload[1] if len(payload) > 1 else ""

    # Saytdan qism bosib kelgan bo'lsa
    if argument.startswith("ep_"):
        try:
            episode_data = argument[3:]
            code, number = episode_data.rsplit("_", 1)
            number = int(number)
        except Exception:
            await message.answer(
                "❌ Qism havolasi noto'g'ri."
            )
            return

        if not await check_subscription(user_id):
            await message.answer(
                "Avval kanalimizga obuna bo'ling:",
                reply_markup=subscription_keyboard(argument)
            )
            return

        await send_episode(message.chat.id, code, number)
        return

    await message.answer(
        "🎌 <b>AniWorldDub botiga xush kelibsiz!</b>\n\n"
        "Anime kodini yuboring yoki saytdan foydalaning.",
        reply_markup=main_keyboard()
    )


# ==============================
# OBUNANI TEKSHIRISH
# ==============================

@dp.callback_query(F.data.startswith("subcheck|"))
async def subscription_check_callback(callback: CallbackQuery):
    payload = callback.data.split("|", 1)[1]

    if not await check_subscription(callback.from_user.id):
        await callback.answer(
            "Hali kanalga obuna bo'lmagansiz!",
            show_alert=True
        )
        return

    await callback.message.delete()

    if payload.startswith("ep_"):
        try:
            data = payload[3:]
            code, number = data.rsplit("_", 1)
            await send_episode(
                callback.message.chat.id,
                code,
                int(number)
            )
        except Exception:
            await callback.message.answer(
                "Qismni ochishda xatolik yuz berdi."
            )
    else:
        await callback.message.answer(
            "✅ Obuna tasdiqlandi!",
            reply_markup=main_keyboard()
        )

    await callback.answer()


# ==============================
# ANIME QIDIRISH
# ==============================

@dp.message(F.text == "🔎 Anime qidirish")
async def search_button(message: Message):
    await message.answer(
        "🔎 Anime kodini yuboring:"
    )


@dp.message(F.text == "🌐 Saytga kirish")
async def site_button(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🌐 AniWorldDub saytini ochish",
                url=SITE_URL
            )
        ]
    ])

    await message.answer(
        "Saytimizga o'tish uchun tugmani bosing:",
        reply_markup=keyboard
    )


@dp.message(Command("search"))
async def search_command(message: Message):
    await message.answer(
        "Anime kodini yuboring:"
    )


@dp.message(F.text, StateFilter(None))
async def anime_code_handler(message: Message):
    code = message.text.strip().upper()

    # Buyruqlar va admin menyusi matnlarini o'tkazib yuborish
    if code.startswith("/") or code in (
        "🔎 ANIME QIDIRISH",
        "🌐 SAYTGA KIRISH"
    ):
        return

    anime = await get_anime(code)

    if not anime:
        return

    if not await check_subscription(message.from_user.id):
        await message.answer(
            "Avval kanalimizga obuna bo'ling:",
            reply_markup=subscription_keyboard()
        )
        return

    numbers = await get_episode_numbers(code)

    if not numbers:
        await message.answer(
            f"<b>{anime['title']}</b>\n\n"
            "Hozircha bu anime uchun qismlar qo'shilmagan."
        )
        return

    buttons = []

    for number in numbers:
        buttons.append([
            InlineKeyboardButton(
                text=f"{number}-qism",
                callback_data=f"get:{code}:{number}"
            )
        ])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

    text = (
        f"🎌 <b>{anime['title']}</b>\n\n"
        f"{anime['description'] or ''}\n\n"
        "Kerakli qismni tanlang:"
    )

    if anime["photo_id"]:
        await message.answer_photo(
            anime["photo_id"],
            caption=text,
            reply_markup=keyboard
        )
    else:
        await message.answer(
            text,
            reply_markup=keyboard
        )


# ==============================
# QISMNI BOT ORQALI YUBORISH
# ==============================

@dp.callback_query(F.data.startswith("get:"))
async def get_episode_callback(callback: CallbackQuery):
    try:
        _, code, number = callback.data.split(":")
        number = int(number)
    except Exception:
        await callback.answer(
            "Noto'g'ri qism.",
            show_alert=True
        )
        return

    if not await check_subscription(callback.from_user.id):
        await callback.message.answer(
            "Avval kanalga obuna bo'ling:",
            reply_markup=subscription_keyboard(
                f"ep_{code}_{number}"
            )
        )
        await callback.answer()
        return

    await send_episode(
        callback.message.chat.id,
        code,
        number
    )

    await callback.answer("Video yuborildi!")

# ==============================
# ADMIN PANEL
# ==============================

@dp.message(Command("admin"))
async def admin_command(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("⛔ Siz admin emassiz.")
        return

    await message.answer(
        "🛠 <b>ADMIN PANEL</b>\nKerakli bo'limni tanlang:",
        reply_markup=admin_keyboard()
    )


@dp.message(Command("myid"))
async def myid_command(message: Message):
    await message.answer(
        f"🆔 Sizning Telegram ID raqamingiz:\n"
        f"<code>{message.from_user.id}</code>"
    )


# ==============================
# ADMIN: ANIME QO'SHISH
# ==============================

@dp.callback_query(F.data == "add_anime")
async def add_anime_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    await state.set_state(AnimeAdd.code)
    await callback.message.answer(
        "Anime uchun kod kiriting:\nMasalan: AN001"
    )
    await callback.answer()


@dp.message(AnimeAdd.code)
async def add_anime_code(message: Message, state: FSMContext):
    code = message.text.strip().upper()

    if not re.fullmatch(r"[A-Z0-9_-]{2,30}", code):
        await message.answer(
            "Kod noto'g'ri. Harf va raqamlardan foydalaning."
        )
        return

    await state.update_data(code=code)
    await state.set_state(AnimeAdd.title)

    await message.answer("Anime nomini kiriting:")


@dp.message(AnimeAdd.title)
async def add_anime_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await state.set_state(AnimeAdd.photo)

    await message.answer(
        "Anime posterini rasm shaklida yuboring:"
    )


@dp.message(AnimeAdd.photo, F.photo)
async def add_anime_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id

    await state.update_data(photo_id=photo_id)
    await state.set_state(AnimeAdd.description)

    await message.answer(
        "Endi anime haqida qisqacha tavsif yozing:"
    )


@dp.message(AnimeAdd.photo)
async def add_anime_photo_error(message: Message):
    await message.answer(
        "Iltimos, posterini rasm sifatida yuboring."
    )


@dp.message(AnimeAdd.description)
async def add_anime_description(message: Message, state: FSMContext):
    data = await state.get_data()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO anime
            (code, title, description, photo_id)
            VALUES (?, ?, ?, ?)
            """,
            (
                data["code"],
                data["title"],
                message.text.strip(),
                data["photo_id"]
            )
        )
        await db.commit()

    await state.clear()

    await message.answer(
        f"✅ <b>{data['title']}</b> qo'shildi!\n"
        f"🔖 Kod: <code>{data['code']}</code>\n\n"
        "Anime sayt API'sida ham ko'rinadi."
    )


# ==============================
# ADMIN: QISM QO'SHISH
# ==============================

@dp.callback_query(F.data == "add_episode")
async def add_episode_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    await state.set_state(EpisodeAdd.code)

    await callback.message.answer(
        "Qism qo'shiladigan anime kodini yuboring:"
    )
    await callback.answer()


@dp.message(EpisodeAdd.code)
async def add_episode_code(message: Message, state: FSMContext):
    code = message.text.strip().upper()
    anime = await get_anime(code)

    if not anime:
        await message.answer(
            "❌ Bunday kodli anime topilmadi."
        )
        return

    numbers = await get_episode_numbers(code)
    next_number = max(numbers, default=0) + 1

    await state.update_data(
        code=code,
        next_number=next_number
    )
    await state.set_state(EpisodeAdd.videos)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Qismlarni tugatish",
                callback_data="finish_episodes"
            )
        ]
    ])

    await message.answer(
        f"🎬 <b>{anime['title']}</b>\n"
        f"Endi videolarni ketma-ket yuboring.\n\n"
        f"Birinchi yuborilgan video — {next_number}-qism.\n"
        "Keyingilari avtomatik raqamlanadi.\n\n"
        "Tugatgach, «Qismlarni tugatish» tugmasini bosing.",
        reply_markup=keyboard
    )


@dp.message(EpisodeAdd.videos, F.video | F.document)
async def save_episode_video(message: Message, state: FSMContext):
    data = await state.get_data()

    code = data["code"]
    number = data["next_number"]

    if message.video:
        file_id = message.video.file_id
        file_type = "video"
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
    else:
        return

    anime = await get_anime(code)

    caption = f"{anime['title']} — {number}-qism | {code}"

    try:
        if file_type == "video":
            stored = await bot.send_video(
                STORAGE_CHANNEL_ID,
                file_id,
                caption=caption
            )
            saved_file_id = stored.video.file_id
        else:
            stored = await bot.send_document(
                STORAGE_CHANNEL_ID,
                file_id,
                caption=caption
            )
            saved_file_id = stored.document.file_id

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO episodes
                (anime_code, episode_no, file_id, file_type, caption)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    code,
                    number,
                    saved_file_id,
                    file_type,
                    caption
                )
            )
            await db.commit()

        await state.update_data(next_number=number + 1)

        await message.answer(
            f"✅ {number}-qism saqlandi!\n"
            f"Keyingi video {number + 1}-qism bo'ladi."
        )

    except Exception as error:
        logger.exception("Qism saqlashda xato: %s", error)
        await message.answer(
            "❌ Videoni saqlashda xatolik. "
            "Storage kanal ruxsatlarini tekshiring."
        )


@dp.callback_query(F.data == "finish_episodes")
async def finish_episodes(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != EpisodeAdd.videos.state:
        await callback.answer("Faol jarayon topilmadi.")
        return

    data = await state.get_data()
    code = data["code"]
    await state.clear()

    numbers = await get_episode_numbers(code)

    await callback.message.answer(
        f"✅ Qismlar saqlandi!\n"
        f"Anime kodi: <code>{code}</code>\n"
        f"Jami qismlar: {len(numbers)}"
    )
    await callback.answer()


# ==============================
# ADMIN: ADMIN QO'SHISH
# ==============================

@dp.callback_query(F.data == "add_admin")
async def add_admin_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer(
            "Faqat bot egasi admin qo'sha oladi.",
            show_alert=True
        )
        return

    await state.set_state(AdminAdd.user_id)

    await callback.message.answer(
        "Yangi adminning Telegram ID raqamini yuboring.\n"
        "U ID raqamini /myid orqali bilishi mumkin."
    )
    await callback.answer()


@dp.message(AdminAdd.user_id)
async def add_admin_finish(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await state.clear()
        return

    try:
        user_id = int(message.text.strip())
        if user_id <= 0:
            raise ValueError
    except Exception:
        await message.answer("❌ ID faqat raqamlardan iborat bo'lishi kerak.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO admin_users (user_id) VALUES (?)",
            (user_id,)
        )
        await db.commit()

    await state.clear()

    await message.answer(
        f"✅ <code>{user_id}</code> adminlar ro'yxatiga qo'shildi."
    )


# ==============================
# ADMIN: ADMINNI O'CHIRISH
# ==============================

@dp.callback_query(F.data == "remove_admin")
async def remove_admin_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer(
            "Faqat bot egasi adminni o'chira oladi.",
            show_alert=True
        )
        return

    await state.set_state(AdminRemove.user_id)

    await callback.message.answer(
        "O'chiriladigan adminning Telegram ID raqamini yuboring:"
    )
    await callback.answer()


@dp.message(AdminRemove.user_id)
async def remove_admin_finish(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await state.clear()
        return

    try:
        user_id = int(message.text.strip())
    except Exception:
        await message.answer("❌ ID raqamini to'g'ri kiriting.")
        return

    if user_id == OWNER_ID:
        await message.answer("Bot egasini o'chirib bo'lmaydi.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM admin_users WHERE user_id=?",
            (user_id,)
        )
        await db.commit()

    await state.clear()

    await message.answer(
        f"✅ <code>{user_id}</code> adminlar ro'yxatidan o'chirildi."
    )


# ==============================
# ADMINLAR RO'YXATI
# ==============================

@dp.callback_query(F.data == "anime_list")
async def anime_list_callback(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT code, title FROM anime ORDER BY title"
        )
        rows = await cursor.fetchall()

    if not rows:
        await callback.message.answer("Hozircha anime yo'q.")
    else:
        text = "📚 <b>Anime ro'yxati:</b>\n\n"
        for code, title in rows:
            text += f"• {title} — <code>{code}</code>\n"

        await callback.message.answer(text)

    await callback.answer()


@dp.callback_query(F.data == "statistics")
async def statistics_callback(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        anime_count = await (
            await db.execute("SELECT COUNT(*) FROM anime")
        ).fetchone()

        episode_count = await (
            await db.execute("SELECT COUNT(*) FROM episodes")
        ).fetchone()

        user_count = await (
            await db.execute("SELECT COUNT(*) FROM users")
        ).fetchone()

    await callback.message.answer(
        "📊 <b>Bot statistikasi</b>\n\n"
        f"🎌 Anime: {anime_count[0]}\n"
        f"🎬 Qismlar: {episode_count[0]}\n"
        f"👥 Foydalanuvchilar: {user_count[0]}"
    )

    await callback.answer()
# ==============================
# ESKI ADMINLARNI SAQLASH
# ==============================

async def load_env_admins():
    raw_admins = os.getenv("ADMIN_IDS", "")

    ids = []

    for item in raw_admins.split(","):
        item = item.strip()
        if item.isdigit():
            ids.append(int(item))

    async with aiosqlite.connect(DB_PATH) as db:
        for user_id in ids:
            await db.execute(
                "INSERT OR IGNORE INTO admin_users (user_id) VALUES (?)",
                (user_id,)
            )

        await db.execute(
            "INSERT OR IGNORE INTO admin_users (user_id) VALUES (?)",
            (OWNER_ID,)
        )

        await db.commit()


# ==============================
# BOTNI ISHGA TUSHIRISH
# ==============================

async def main():
    await init_db()
    await load_env_admins()

    api_runner = await start_api()

    await bot.delete_webhook(drop_pending_updates=True)

    logger.info("AniWorldDub bot ishga tushdi!")

    try:
        await dp.start_polling(bot)
    finally:
        await api_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
