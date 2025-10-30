import asyncio
import os
import logging
import sqlite3
import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8407995258:AAE1I7paypCciMW4hBTdCjLhByGlwF35PNs")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

OWNERS = [7450525550, 5946158486]

def init_db():
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        telegram_id BIGINT UNIQUE NOT NULL,
        full_name TEXT,
        is_admin BOOLEAN DEFAULT 0
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS homework (
        id INTEGER PRIMARY KEY,
        subject TEXT NOT NULL,
        description TEXT NOT NULL,
        due_date DATE NOT NULL,
        added_by BIGINT NOT NULL
    )''')
    conn.commit()
    conn.close()

def execute_query(query, params=(), fetch=False):
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute(query, params)
    result = cursor.fetchall() if fetch else cursor.rowcount
    conn.commit()
    conn.close()
    return result

def get_user(user_id):
    result = execute_query("SELECT full_name, is_admin FROM users WHERE telegram_id = ?", (user_id,), fetch=True)
    return result[0] if result else None

class Form(StatesGroup):
    waiting_for_fio = State()

# ТВОИ ХЕНДЛЕРЫ (ОСТАЮТСЯ БЕЗ ИЗМЕНЕНИЙ)
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user = get_user(message.from_user.id)
    if user and user[0]:
        await message.answer(f"Привет, {user[0]}!\n\n/homework\n/announce")
    else:
        execute_query("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (message.from_user.id,))
        await message.answer("Напиши **ФИО**")
        await state.set_state(Form.waiting_for_fio)

@dp.message(Form.waiting_for_fio)
async def process_fio(message: types.Message, state: FSMContext):
    fio = message.text.strip()
    execute_query("UPDATE users SET full_name = ? WHERE telegram_id = ?", (fio, message.from_user.id))
    await message.answer(f"ФИО: **{fio}**")
    await state.clear()

@dp.message(Command("announce"))
async def cmd_announce(message: types.Message):
    user = get_user(message.from_user.id)
    if not user or not user[1]:
        await message.answer("Только админ")
        return
    text = message.text.replace("/announce", "", 1).strip()
    if not text: 
        await message.answer("/announce Текст")
        return
    users = execute_query("SELECT telegram_id FROM users", fetch=True)
    sent = 0
    for (tg_id,) in users:
        try:
            await bot.send_message(tg_id, f"**Объявление**\n\n{text}")
            sent += 1
        except: pass
    await message.answer(f"Отправлено: {sent}")

@dp.message(Command("make_admin"))
async def cmd_make_admin(message: types.Message):
    if message.from_user.id not in OWNERS:
        await message.answer("Только владелец")
        return
    execute_query("UPDATE users SET is_admin = 1 WHERE telegram_id = ?", (message.from_user.id,))
    await message.answer("Ты админ!")

@dp.message(Command("homework"))
async def cmd_homework(message: types.Message):
    hw = execute_query("SELECT subject, description, due_date FROM homework ORDER BY due_date", fetch=True)
    if not hw:
        await message.answer("Нет ДЗ")
        return
    text = "**ДЗ**\n\n"
    for s, d, date in hw:
        text += f"*{s}* — до {date}\n{d}\n\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("add_hw"))
async def cmd_add_hw(message: types.Message):
    user = get_user(message.from_user.id)
    if not user or not user[1]:
        await message.answer("Только админ")
        return
    raw = message.text.replace("/add_hw", "", 1).strip()
    if ":" not in raw:
        await message.answer("/add_hw Математика: Задачи")
        return
    subject, rest = raw.split(":", 1)
    due_date = datetime.date.today() + datetime.timedelta(days=2)
    execute_query("INSERT INTO homework (subject, description, due_date, added_by) VALUES (?, ?, ?, ?)", 
                  (subject.strip(), rest.strip(), due_date, message.from_user.id))
    await message.answer(f"ДЗ: **{subject.strip()}**")

# ВЕБ-СЕРВЕР ДЛЯ RENDER
async def health_check(request):
    return web.Response(text="OK")

async def web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000)))
    await site.start()
    logger.info("Веб-сервер запущен")

async def run_bot():
    init_db()
    logger.info("Бот запущен!")
    await dp.start_polling(bot)

async def main():
    await asyncio.gather(
        web_server(),    # ← Веб-сервер для Render
        run_bot()        # ← Telegram бот
    )

if __name__ == "__main__":
    asyncio.run(main())
