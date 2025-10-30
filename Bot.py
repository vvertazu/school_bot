import asyncio
import os
import logging
import sqlite3
import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

OWNERS = [7450525550, 5946158486]

def init_db():
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        telegram_id BIGINT UNIQUE NOT NULL,
        full_name TEXT,
        birth_date DATE,
        is_admin BOOLEAN DEFAULT 0,
        joined_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS homework (
        id INTEGER PRIMARY KEY,
        subject TEXT NOT NULL,
        description TEXT NOT NULL,
        due_date DATE NOT NULL,
        added_by BIGINT NOT NULL
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS schedule (
        id INTEGER PRIMARY KEY,
        day_of_week INTEGER NOT NULL,
        lesson_number INTEGER NOT NULL,
        subject TEXT NOT NULL,
        classroom TEXT
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY,
        user_id BIGINT NOT NULL,
        date DATE NOT NULL,
        status TEXT DEFAULT 'present',
        reason TEXT,
        marked_by BIGINT NOT NULL,
        UNIQUE(user_id, date)
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

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user = get_user(message.from_user.id)
    if user and user[0]:
        await message.answer(f"Привет, {user[0]}!\n\n/schedule\n/homework\n/attendance")
    else:
        execute_query("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (message.from_user.id,))
        await message.answer("Напиши **ФИО полностью**")
        await state.set_state(Form.waiting_for_fio)

@dp.message(Form.waiting_for_fio)
async def process_fio(message: types.Message, state: FSMContext):
    fio = message.text.strip()
    if len(fio) < 5:
        await message.answer("ФИО слишком короткое")
        return
    execute_query("UPDATE users SET full_name = ? WHERE telegram_id = ?", (fio, message.from_user.id))
    await message.answer(f"ФИО сохранено: **{fio}**")
    await state.clear()

@dp.message(Command("announce"))
async def cmd_announce(message: types.Message):
    user = get_user(message.from_user.id)
    if not user or not user[1]:
        await message.answer("Только админ")
        return
    text = message.text.replace("/announce", "", 1).strip()
    if not text:
        await message.answer("Напиши: /announce Текст")
        return
    users = execute_query("SELECT telegram_id FROM users", fetch=True)
    sent = 0
    for (tg_id,) in users:
        try:
            await bot.send_message(tg_id, f"**Объявление**\n\n{text}", parse_mode="Markdown")
            sent += 1
        except:
            pass
    await message.answer(f"Отправлено {sent} пользователям")

@dp.message(Command("users"))
async def cmd_users(message: types.Message):
    user = get_user(message.from_user.id)
    if not user or not user[1]:
        await message.answer("Только админ")
        return
    users = execute_query("SELECT full_name, telegram_id FROM users ORDER BY full_name", fetch=True)
    text = "**Пользователи**\n\n"
    for name, tg_id in users:
        text += f"• {name or 'Без ФИО'} — `{tg_id}`\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("make_admin"))
async def cmd_make_admin(message: types.Message):
    if message.from_user.id not in OWNERS:
        await message.answer("Только владелец")
        return
    execute_query("UPDATE users SET is_admin = 1 WHERE telegram_id = ?", (message.from_user.id,))
    await message.answer("Ты теперь админ!")

@dp.message(Command("homework"))
async def cmd_homework(message: types.Message):
    today = datetime.date.today()
    hw = execute_query("SELECT subject, description, due_date FROM homework WHERE due_date >= ? ORDER BY due_date", (today,), fetch=True)
    if not hw:
        await message.answer("Нет ДЗ")
        return
    text = "**ДЗ**\n\n"
    for subject, desc, due in hw:
        text += f"*{subject}* (до {due})\n{desc}\n\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("add_hw"))
async def cmd_add_hw(message: types.Message):
    user = get_user(message.from_user.id)
    if not user or not user[1]:
        await message.answer("Только админ")
        return
    raw = message.text.replace("/add_hw", "", 1).strip()
    if ":" not in raw:
        await message.answer("/add_hw Математика: Задачи до 01.11")
        return
    subject, rest = raw.split(":", 1)
    today = datetime.date.today()
    due_date = today + datetime.timedelta(days=2)
    desc = rest.strip()
    if " до " in rest:
        try:
            _, date_part = rest.rsplit(" до ", 1)
            due_date = datetime.datetime.strptime(date_part.strip(), "%d.%m").date()
            if due_date < today:
                due_date = due_date.replace(year=today.year + 1)
        except: pass
    execute_query("INSERT INTO homework (subject, description, due_date, added_by) VALUES (?, ?, ?, ?)", 
                  (subject.strip(), desc.strip(), due_date, message.from_user.id))
    await message.answer(f"ДЗ по **{subject.strip()}** до {due_date:%d.%m}")

async def main():
    init_db()
    logger.info("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
