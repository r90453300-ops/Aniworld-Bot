import asyncio
import logging
import os
import re
from html import escape

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
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
    code = State()
    title = State()
    photo = State()
    description = State()


class AddEpisode(StatesGroup):
    waiting_code = State()
    waiting_videos = State()


class PublishAnime(StatesGroup):
    waiting_code = State()


class SearchAnime(StatesGroup):
    waiting_code = State()


# admin_id -> {"code": str, "start": int, "items": [(message_id, file_id, type)]}
pending_episodes = {}


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

        try:
            await db.execute(
                "ALTER TABLE anime ADD COLUMN photo_id TEXT DEFAULT ''"
            )
        except aiosqlite.OperationalError:
            pass  # ustun allaqachon bor

        await db.commit()

    log.info("Database tayyor: %s", DB_PATH)


async def get_anime(code):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT code, title, description, photo_id
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


def episode_done_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tayyor", callback_data="ep:done"),
        InlineKeyboardButton(text="❌ Bekor", callback_data="ep:cancel"),
    ]])


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

    if numbers:
        text += "\n📺 Kerakli qismni tanlang:"
        kb = episode_keyboard(code, numbers)
    else:
        text += "\nHozircha qismlar mavjud emas."
        kb = None

    if anime[3]:
        await message.answer_photo(
            anime[3], caption=text[:1024], reply_markup=kb
        )
    else:
        await message.answer(text, reply_markup=kb)


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
            await callback.answer()
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
async def admin_handler(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Sizda admin huquqi yo‘q.")
        return

    await state.clear()
    await message.answer(
        "🛠 <b>AniJon admin paneli</b>",
        reply_markup=admin_keyboard(),
    )


# =====================================
# ANIME QO‘SHISH: kod -> nom -> rasm -> tavsif
# =====================================

@dp.callback_query(F.data == "admin:addanime")
async def admin_add_anime(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    await state.clear()
    await state.set_state(AddAnime.code)
    await callback.message.answer(
        "1/4 🔑 Anime kodini yuboring.\nMasalan: <code>AN001</code>"
    )
    await callback.answer()


@dp.message(AddAnime.code)
async def add_anime_code(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    code = normalize_code(message.text or "")
    if not valid_code(code):
        await message.answer("❌ Kod noto‘g‘ri. Faqat harf, raqam, _ va -.")
        return
    await state.update_data(code=code)
    await state.set_state(AddAnime.title)
    await message.answer("2/4 📝 Anime nomini yuboring.")


@dp.message(AddAnime.title)
async def add_anime_title(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("❌ Nomni matn ko‘rinishida yuboring.")
        return
    await state.update_data(title=title)
    await state.set_state(AddAnime.photo)
    await message.answer(
        "3/4 🖼 Anime rasmini yuboring.\nO‘tkazib yuborish: /skip"
    )


async def ask_description(message: Message, state: FSMContext, photo_id: str):
    await state.update_data(photo_id=photo_id)
    await state.set_state(AddAnime.description)
    await message.answer(
        "4/4 📖 Tavsifni yuboring.\nO‘tkazib yuborish: /skip"
    )


@dp.message(AddAnime.photo, F.photo)
async def add_anime_photo(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await ask_description(message, state, message.photo[-1].file_id)


@dp.message(AddAnime.photo, Command("skip"))
async def add_anime_photo_skip(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await ask_description(message, state, "")


@dp.message(AddAnime.photo)
async def add_anime_photo_wrong(message: Message):
    await message.answer("Iltimos, rasm yuboring yoki /skip bosing.")


@dp.message(AddAnime.description)
async def add_anime_description(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    description = "" if text == "/skip" else text

    data = await state.get_data()
    code, title = data["code"], data["title"]
    photo_id = data.get("photo_id", "")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO anime(code, title, description, photo_id)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                photo_id=excluded.photo_id
            """,
            (code, title, description, photo_id),
        )
        await db.commit()

    await state.clear()
    await message.answer(
        f"✅ <b>{escape(title)}</b> qo‘shildi.\n"
        f"Kodi: <code>{code}</code>\n\n"
        "Endi «🎬 Qism qo‘shish» orqali qismlarni yuklang."
    )


# =====================================
# QISM QO‘SHISH: ketma-ket, avtomatik raqamlash
# =====================================

@dp.callback_query(F.data == "admin:addepisode")
async def admin_add_episode(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    await state.clear()
    await state.set_state(AddEpisode.waiting_code)
    await callback.message.answer(
        "🔑 Anime kodini yuboring.\n"
        "Masalan: <code>AN001</code>\n\n"
        "Aniq raqamdan boshlash uchun: <code>AN001 | 5</code>"
    )
    await callback.answer()


@dp.message(AddEpisode.waiting_code)
async def episode_code(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    parts = [x.strip() for x in (message.text or "").split("|", 1)]
    code = normalize_code(parts[0])

    if not valid_code(code):
        await message.answer("❌ Kod noto‘g‘ri.")
        return

    anime = await get_anime(code)
    if not anime:
        await message.answer("❌ Anime topilmadi. Avval anime qo‘shing.")
        return

    if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) >= 1:
        start = int(parts[1])
    else:
        numbers = await get_episode_numbers(code)
        start = (max(numbers) + 1) if numbers else 1

    pending_episodes[message.from_user.id] = {
        "code": code,
        "start": start,
        "items": [],
    }
    await state.set_state(AddEpisode.waiting_videos)

    await message.answer(
        f"🎬 <b>{escape(anime[1])}</b>\n"
        f"{start}-qismdan boshlab videolarni ketma-ket yuboring "
        "(masalan 24 tasini birdan tashlang).\n"
        "Hammasini yuborib bo‘lgach «✅ Tayyor» ni bosing.",
        reply_markup=episode_done_keyboard(),
    )


@dp.message(AddEpisode.waiting_videos, F.video | F.document)
async def episode_collect(message: Message):
    if not is_admin(message.from_user.id):
        return

    job = pending_episodes.get(message.from_user.id)
    if not job:
        await message.answer("Jarayon topilmadi. Qaytadan boshlang.")
        return

    if message.video:
        item = (message.message_id, message.video.file_id, "video")
    else:
        item = (message.message_id, message.document.file_id, "document")

    job["items"].append(item)

    # har 5 ta faylda bir marta tasdiq
    if len(job["items"]) % 5 == 0:
        await message.answer(
            f"📥 {len(job['items'])} ta fayl qabul qilindi.",
            reply_markup=episode_done_keyboard(),
        )


async def send_to_storage(bot: Bot, file_id, file_type, caption):
    for _ in range(5):
        try:
            if file_type == "video":
                stored = await bot.send_video(
                    STORAGE_CHANNEL_ID, file_id, caption=caption
                )
                return stored.video.file_id, "video"
            stored = await bot.send_document(
                STORAGE_CHANNEL_ID, file_id, caption=caption
            )
            return stored.document.file_id, "document"
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
    raise RuntimeError("Ombor kanaliga yuborib bo‘lmadi")


async def finish_episodes(
    target: Message, state: FSMContext, bot: Bot, user_id: int
):
    job = pending_episodes.pop(user_id, None)
    await state.clear()

    if not job or not job["items"]:
        await target.answer("❌ Hech qanday fayl yuborilmadi.")
        return

    code = job["code"]
    anime = await get_anime(code)
    items = sorted(job["items"], key=lambda x: x[0])  # yuborilgan tartib

    status = await target.answer(f"⏳ {len(items)} ta qism saqlanmoqda...")
    saved = []

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            for i, (_, file_id, file_type) in enumerate(items):
                number = job["start"] + i
                caption = f"{anime[1]} — {number}-qism | {code}"

                new_id, new_type = await send_to_storage(
                    bot, file_id, file_type, caption
                )

                await db.execute(
                    """
                    INSERT INTO episodes(
                        anime_code, episode_no, file_id, file_type, caption
                    )
                    VALUES(?, ?, ?, ?, '')
                    ON CONFLICT(anime_code, episode_no) DO UPDATE SET
                        file_id=excluded.file_id,
                        file_type=excluded.file_type
                    """,
                    (code, number, new_id, new_type),
                )
                await db.commit()
                saved.append(number)
                await asyncio.sleep(1.2)  # Telegram limitidan qochish

    except Exception as exc:
        log.exception("Qism saqlash xatosi: %s", exc)
        await status.edit_text(
            f"⚠️ Xato bo‘ldi. Saqlangan qismlar: {len(saved)} ta"
            + (f" ({saved[0]}–{saved[-1]})" if saved else "")
        )
        return

    await status.edit_text(
        f"✅ <b>{escape(anime[1])}</b>: {len(saved)} ta qism qo‘shildi "
        f"({saved[0]}–{saved[-1]}-qism)."
    )


@dp.callback_query(F.data == "ep:done")
async def episode_done_cb(
    callback: CallbackQuery, state: FSMContext, bot: Bot
):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return
    await callback.answer()
    await finish_episodes(
        callback.message, state, bot, callback.from_user.id
    )


@dp.message(Command("done"))
async def episode_done_cmd(
    message: Message, state: FSMContext, bot: Bot
):
    if is_admin(message.from_user.id):
        await finish_episodes(message, state, bot, message.from_user.id)


@dp.callback_query(F.data == "ep:cancel")
async def episode_cancel(callback: CallbackQuery, state: FSMContext):
    pending_episodes.pop(callback.from_user.id, None)
    await state.clear()
    await callback.message.answer("❌ Bekor qilindi.")
    await callback.answer()
# =====================================
# ANIME RO‘YXATI
# =====================================

@dp.callback_query(F.data == "admin:list")
async def admin_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT a.code, a.title, COUNT(e.episode_no)
            FROM anime a
            LEFT JOIN episodes e ON e.anime_code = a.code
            GROUP BY a.code
            ORDER BY a.code
            """
        )
        rows = await cur.fetchall()

    if not rows:
        await callback.message.answer("📚 Hozircha anime yo‘q.")
        await callback.answer()
        return

    lines = [
        f"<code>{escape(code)}</code> — {escape(title)} ({count} qism)"
        for code, title, count in rows
    ]

    text = "📚 <b>Anime ro‘yxati</b>\n\n"
    for line in lines:
        if len(text) + len(line) > 3800:
            await callback.message.answer(text)
            text = ""
        text += line + "\n"

    if text:
        await callback.message.answer(text)

    await callback.answer()


# =====================================
# STATISTIKA
# =====================================

@dp.callback_query(F.data == "admin:stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    anime_count, episode_count = await get_statistics()

    await callback.message.answer(
        "📊 <b>Statistika</b>\n\n"
        f"🎬 Anime: <b>{anime_count}</b>\n"
        f"📺 Qismlar: <b>{episode_count}</b>"
    )
    await callback.answer()


# =====================================
# KANALGA CHIQARISH
# =====================================

@dp.callback_query(F.data == "admin:publish")
async def admin_publish(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    await state.clear()
    await state.set_state(PublishAnime.waiting_code)
    await callback.message.answer(
        "📣 Kanalga chiqariladigan anime kodini yuboring.\n"
        "Masalan: <code>AN001</code>"
    )
    await callback.answer()


@dp.message(PublishAnime.waiting_code)
async def publish_anime(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return

    code = normalize_code(message.text or "")

    if not valid_code(code):
        await message.answer("❌ Kod noto‘g‘ri.")
        return

    anime = await get_anime(code)

    if not anime:
        await message.answer("❌ Anime topilmadi.")
        return

    numbers = await get_episode_numbers(code)

    text = f"🎬 <b>{escape(anime[1])}</b>\n\n"

    if anime[2]:
        text += f"📝 {escape(anime[2])}\n\n"

    text += f"📺 Qismlar soni: <b>{len(numbers)}</b>"

    try:
        me = await bot.get_me()
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="▶️ Ko‘rish",
                url=f"https://t.me/{me.username}?start=anime_{code}",
            )
        ]])

        if anime[3]:
            await bot.send_photo(
                PUBLIC_CHANNEL_ID,
                anime[3],
                caption=text[:1024],
                reply_markup=kb,
            )
        else:
            await bot.send_message(
                PUBLIC_CHANNEL_ID, text, reply_markup=kb
            )

    except TelegramAPIError as exc:
        log.exception("Kanalga chiqarishda xato: %s", exc)
        await message.answer(
            "❌ Kanalga yuborib bo‘lmadi. "
            "Bot kanalda admin ekanini tekshiring."
        )
        return

    await state.clear()
    await message.answer("✅ Anime kanalga chiqarildi.")


# =====================================
# ISHGA TUSHIRISH
# =====================================

async def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN topilmadi. Railway Variables ga qo‘shing.")

    await init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    await bot.delete_webhook(drop_pending_updates=True)
    log.info("AniJon bot ishga tushdi")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
