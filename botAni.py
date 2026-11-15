import asyncio
import logging
import os
import re
from typing import Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
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
from aiogram.utils.deep_linking import create_start_link


# =========================
# AniWorldDub bot sozlamalari
# Railway Variables bo'limida BOT_TOKEN ni kiriting.
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PUBLIC_CHANNEL_ID = int(os.getenv("PUBLIC_CHANNEL_ID", "-1004349473919"))
PUBLIC_CHANNEL_USERNAME = os.getenv("PUBLIC_CHANNEL_USERNAME", "@AniJon_Anime").strip()
STORAGE_CHANNEL_ID = int(os.getenv("STORAGE_CHANNEL_ID", "-1004368926932"))
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "8975803698").split(",")
    if x.strip().isdigit()
}
DB_PATH = os.getenv("DB_PATH", "anijon.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("AniWorldDub")

dp = Dispatcher(storage=MemoryStorage())


class AddAnime(StatesGroup):
    waiting_data = State()


class AddEpisode(StatesGroup):
    waiting_data = State()
    waiting_video = State()


class PublishAnime(StatesGroup):
    waiting_code = State()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
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
                FOREIGN KEY (anime_code) REFERENCES anime(code)
            )
        """)
        await db.commit()


async def get_anime(code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT code, title, description FROM anime WHERE code = ?",
            (code,),
        )
        return await cur.fetchone()


async def get_episode(code: str, episode_no: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """SELECT file_id, file_type, caption
               FROM episodes WHERE anime_code = ? AND episode_no = ?""",
            (code, episode_no),
        )
        return await cur.fetchone()


async def get_episode_numbers(code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT episode_no FROM episodes WHERE anime_code = ? ORDER BY episode_no",
            (code,),
        )
        rows = await cur.fetchall()
        return [row[0] for row in rows]


async def subscribed(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(PUBLIC_CHANNEL_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as exc:
        log.warning("Obunani tekshirib bo'lmadi: %s", exc)
        return False


def subscription_keyboard(payload: str = "") -> InlineKeyboardMarkup:
    check_data = "subcheck"
    if payload:
        check_data = f"subcheck:{payload}"[:64]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📢 Kanalga obuna bo‘lish",
            url=f"https://t.me/{PUBLIC_CHANNEL_USERNAME.lstrip('@')}",
        )],
        [InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data=check_data)],
    ])


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Anime qo‘shish", callback_data="admin:addanime")],
        [InlineKeyboardButton(text="🎬 Qism qo‘shish", callback_data="admin:addepisode")],
        [InlineKeyboardButton(text="📚 Anime ro‘yxati", callback_data="admin:list")],
        [InlineKeyboardButton(text="📣 Anime qismlarini kanalga chiqarish", callback_data="admin:publish")],
    ])


def episode_keyboard(code: str, episode_numbers: list[int]) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for number in episode_numbers:
        row.append(InlineKeyboardButton(
            text=f"{number}-qism",
            callback_data=f"get:{code}:{number}",
        ))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def deliver_episode(target, bot: Bot, code: str, episode_no: int):
    anime = await get_anime(code)
    episode = await get_episode(code, episode_no)

    if not anime:
        await target.answer("Bu anime kodi topilmadi.")
        return
    if not episode:
        await target.answer("Bu qism hali Omborga qo‘shilmagan.")
        return

    file_id, file_type, caption = episode
    text = caption or f"<b>{anime[1]}</b>\n{episode_no}-qism"
    if isinstance(target, CallbackQuery):
        chat_id = target.message.chat.id
        await target.answer("Qism yuborilmoqda…")
    else:
        chat_id = target.chat.id

    if file_type == "video":
        await bot.send_video(chat_id, file_id, caption=text)
    else:
        await bot.send_document(chat_id, file_id, caption=text)


@dp.message(CommandStart())
async def start_handler(message: Message, bot: Bot):
    payload = message.text.split(maxsplit=1)[1].strip() if len(message.text.split(maxsplit=1)) > 1 else ""

    if not await subscribed(bot, message.from_user.id):
        await message.answer(
            "👋 <b>AniWorldDub botiga xush kelibsiz!</b>\n\n"
            "Botdan foydalanish uchun avval kanalimizga obuna bo‘ling.",
            reply_markup=subscription_keyboard(payload),
        )
        return

    if payload:
        match = re.fullmatch(r"ep_([A-Za-z0-9_-]+)_(\d+)", payload)
        anime_match = re.fullmatch(r"anime_([A-Za-z0-9_-]+)", payload)

        if match:
            await deliver_episode(message, bot, match.group(1), int(match.group(2)))
            return
        if anime_match:
            code = anime_match.group(1)
            anime = await get_anime(code)
            numbers = await get_episode_numbers(code)
            if not anime:
                await message.answer("Bu anime hali bot bazasiga qo‘shilmagan.")
            elif not numbers:
                await message.answer(f"<b>{anime[1]}</b>\nHozircha qismlar mavjud emas.")
            else:
                await message.answer(
                    f"🎬 <b>{anime[1]}</b>\nKerakli qismni tanlang:",
                    reply_markup=episode_keyboard(code, numbers),
                )
            return

    await message.answer(
        "👋 <b>AniWorldDub botiga xush kelibsiz!</b>\n\n"
        "Anime qismini olish uchun saytdagi qism tugmasini bosing "
        "yoki anime havolasi orqali kiring."
    )


@dp.callback_query(F.data.startswith("subcheck"))
async def subscription_check(callback: CallbackQuery, bot: Bot):
    payload = ""
    if ":" in (callback.data or ""):
        payload = callback.data.split(":", 1)[1]

    if not await subscribed(bot, callback.from_user.id):
        await callback.answer("Avval kanalga obuna bo‘ling.", show_alert=True)
        return

    await callback.message.edit_text("✅ Obuna tasdiqlandi!")
    if payload:
        match = re.fullmatch(r"ep_([A-Za-z0-9_-]+)_(\d+)", payload)
        anime_match = re.fullmatch(r"anime_([A-Za-z0-9_-]+)", payload)
        if match:
            await deliver_episode(callback, bot, match.group(1), int(match.group(2)))
        elif anime_match:
            code = anime_match.group(1)
            anime = await get_anime(code)
            numbers = await get_episode_numbers(code)
            if anime and numbers:
                await callback.message.answer(
                    f"🎬 <b>{anime[1]}</b>\nKerakli qismni tanlang:",
                    reply_markup=episode_keyboard(code, numbers),
                )
            elif anime:
                await callback.message.answer(f"<b>{anime[1]}</b>\nHozircha qismlar mavjud emas.")
            else:
                await callback.message.answer("Bu anime kodi topilmadi.")
    else:
        await callback.message.answer("Endi botdan foydalanishingiz mumkin.")


@dp.callback_query(F.data.startswith("get:"))
async def get_episode_callback(callback: CallbackQuery, bot: Bot):
    if not await subscribed(bot, callback.from_user.id):
        await callback.message.answer(
            "Botdan foydalanish uchun kanalga obuna bo‘ling.",
            reply_markup=subscription_keyboard(),
        )
        await callback.answer()
        return
    try:
        _, code, number = callback.data.split(":")
        await deliver_episode(callback, bot, code, int(number))
    except (ValueError, TypeError):
        await callback.answer("Noto‘g‘ri qism.", show_alert=True)


@dp.message(Command("admin"))
async def admin_handler(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("🛠 <b>AniWorldDub admin paneli</b>", reply_markup=admin_keyboard())


@dp.callback_query(F.data == "admin:addanime")
async def admin_add_anime(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return
    await state.set_state(AddAnime.waiting_data)
    await callback.message.answer(
        "Anime ma’lumotini shu ko‘rinishda yuboring:\n"
        "<code>KOD | Anime nomi</code>\n\n"
        "Masalan: <code>123 | Naruto</code>\n"
        "Sayt havolasi MAL ID ishlatsa, KOD o‘rniga o‘sha ID ni yozing."
    )
    await callback.answer()


@dp.message(AddAnime.waiting_data)
async def save_anime(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if not message.text or "|" not in message.text:
        await message.answer("Format xato. Masalan: <code>123 | Naruto</code>")
        return
    code, title = [part.strip() for part in message.text.split("|", 1)]
    if not code or not title:
        await message.answer("Kod va anime nomini to‘liq kiriting.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO anime(code, title) VALUES(?, ?) "
            "ON CONFLICT(code) DO UPDATE SET title=excluded.title",
            (code, title),
        )
        await db.commit()
    await state.clear()
    await message.answer(f"✅ <b>{title}</b> bazaga qo‘shildi. Kodi: <code>{code}</code>")


@dp.callback_query(F.data == "admin:addepisode")
async def admin_add_episode(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return
    await state.set_state(AddEpisode.waiting_data)
    await callback.message.answer(
        "Anime kodi va qism raqamini yuboring:\n"
        "<code>KOD | QISM</code>\n"
        "Masalan: <code>123 | 1</code>"
    )
    await callback.answer()


@dp.message(AddEpisode.waiting_data)
async def episode_metadata(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if not message.text or "|" not in message.text:
        await message.answer("Format xato. Masalan: <code>123 | 1</code>")
        return
    code, number_text = [part.strip() for part in message.text.split("|", 1)]
    if not number_text.isdigit():
        await message.answer("Qism raqami son bo‘lishi kerak.")
        return
    anime = await get_anime(code)
    if not anime:
        await message.answer("Bu kodli anime topilmadi. Avval Anime qo‘shing.")
        return
    await state.update_data(code=code, episode_no=int(number_text))
    await state.set_state(AddEpisode.waiting_video)
    await message.answer(
        f"Endi <b>{anime[1]}</b> — {number_text}-qism videosini shu chatga yuboring.\n"
        "Video Ombor kanaliga saqlanadi."
    )


@dp.message(AddEpisode.waiting_video, F.video | F.document)
async def save_episode_file(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    code = data["code"]
    episode_no = data["episode_no"]
    anime = await get_anime(code)

    try:
        if message.video:
            stored = await bot.send_video(
                STORAGE_CHANNEL_ID,
                message.video.file_id,
                caption=f"{anime[1]} — {episode_no}-qism | {code}",
            )
            file_id = stored.video.file_id
            file_type = "video"
        else:
            stored = await bot.send_document(
                STORAGE_CHANNEL_ID,
                message.document.file_id,
                caption=f"{anime[1]} — {episode_no}-qism | {code}",
            )
            file_id = stored.document.file_id
            file_type = "document"

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO episodes(anime_code, episode_no, file_id, file_type, caption)
                   VALUES(?, ?, ?, ?, ?)
                   ON CONFLICT(anime_code, episode_no) DO UPDATE SET
                   file_id=excluded.file_id, file_type=excluded.file_type,
                   caption=excluded.caption""",
                (code, episode_no, file_id, file_type, ""),
            )
            await db.commit()

        await state.clear()
        await message.answer(
            f"✅ <b>{anime[1]}</b> — {episode_no}-qism saqlandi!\n"
            "Video maxfiy Ombor kanaliga joylandi."
        )
    except Exception:
        log.exception("Videoni Omborga saqlashda xato")
        await message.answer(
            "❌ Videoni Omborga saqlab bo‘lmadi. Bot Ombor kanalida administrator "
            "ekanini va post yuborish huquqi borligini tekshiring."
        )


@dp.message(AddEpisode.waiting_video)
async def wrong_episode_file(message: Message):
    await message.answer("Iltimos, video yoki video fayl (document) yuboring.")


@dp.callback_query(F.data == "admin:list")
async def admin_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT code, title FROM anime ORDER BY title")
        rows = await cur.fetchall()
    if not rows:
        text = "Hozircha anime qo‘shilmagan."
    else:
        text = "📚 <b>Anime ro‘yxati</b>\n\n" + "\n".join(
            f"• <b>{title}</b> — <code>{code}</code>" for code, title in rows
        )
    await callback.message.answer(text)
    await callback.answer()


@dp.callback_query(F.data == "admin:publish")
async def admin_publish_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return
    await state.set_state(PublishAnime.waiting_code)
    await callback.message.answer("Kanalga chiqarish uchun anime kodini yuboring.")
    await callback.answer()


@dp.message(PublishAnime.waiting_code)
async def publish_anime(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    code = (message.text or "").strip()
    anime = await get_anime(code)
    numbers = await get_episode_numbers(code)
    if not anime:
        await message.answer("Anime kodi topilmadi.")
        return
    if not numbers:
        await message.answer("Bu anime uchun hali qismlar qo‘shilmagan.")
        return

    try:
        await bot.send_message(
            PUBLIC_CHANNEL_ID,
            f"🎬 <b>{anime[1]}</b>\n\nKerakli qismni tanlang:",
            reply_markup=episode_keyboard(code, numbers),
        )
        await message.answer("✅ Anime qismlari ommaviy kanalga chiqarildi.")
    except Exception:
        log.exception("Kanalga post chiqarishda xato")
        await message.answer(
            "❌ Kanalga post chiqarilmadi. Bot ommaviy kanalda administrator "
            "ekanini va xabar yuborish huquqi borligini tekshiring."
        )
    await state.clear()


@dp.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Amal bekor qilindi.")


async def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN topilmadi. Railway Variables bo‘limiga BOT_TOKEN qo‘shing."
        )

    await init_db()
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    me = await bot.get_me()
    log.info("Bot ishga tushdi: @%s", me.username)

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
