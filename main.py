import asyncio
import os
import logging
import sqlite3
import datetime
import re
import signal
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 🔑 ТОКЕН (обязательно в Render)
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен!")

# Пароль для входа в админы (будет храниться в БД, но инициализируется из env)
INITIAL_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "sunnatjalab")

# Список ID старших админов (только из переменной окружения)
SENIOR_ADMINS_STR = os.getenv("SENIOR_ADMINS", "")
SENIOR_ADMINS = set()
if SENIOR_ADMINS_STR.strip():
    try:
        SENIOR_ADMINS = set(int(x.strip()) for x in SENIOR_ADMINS_STR.split(",") if x.strip().isdigit())
    except Exception as e:
        logger.error(f"Ошибка парсинга SENIOR_ADMINS: {e}")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

def init_db():
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            full_name TEXT,
            birth_date TEXT,
            is_admin INTEGER DEFAULT 0,
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица для хранения пароля админки
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    # Расписание, ДЗ, Посещаемость — без изменений
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            lesson_number INTEGER NOT NULL,
            subject TEXT NOT NULL,
            classroom TEXT,
            start_time TEXT,
            end_time TEXT,
            lesson_type TEXT,
            teacher TEXT,
            UNIQUE(date, lesson_number)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS homework (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            description TEXT NOT NULL,
            due_date TEXT NOT NULL CHECK(due_date LIKE '____-__-__'),
            added_by INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'present',
            reason TEXT,
            marked_by INTEGER NOT NULL,
            marked_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, date)
        )
    ''')
    
    # Индексы
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_schedule_date ON schedule(date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_homework_due_date ON homework(due_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(date)')
    
    # Инициализация пароля, если ещё не задан
    cursor.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES ('admin_password', ?)", (INITIAL_ADMIN_PASSWORD,))
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")
    logger.info(f"👑 Старшие админы: {SENIOR_ADMINS}")

# Утилиты
def get_user_sync(user_id: int):
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT full_name, is_admin FROM users WHERE telegram_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result

def get_admin_password_sync():
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM bot_config WHERE key = 'admin_password'")
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else INITIAL_ADMIN_PASSWORD

def set_admin_password_sync(new_pass: str):
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE bot_config SET value = ? WHERE key = 'admin_password'", (new_pass,))
    conn.commit()
    conn.close()

def set_user_admin_sync(user_id: int, is_admin: bool):
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_admin = ? WHERE telegram_id = ?", (1 if is_admin else 0, user_id))
    conn.commit()
    conn.close()

def remove_admin_sync(user_id: int):
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_admin = 0 WHERE telegram_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_all_users_sync():
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, full_name, is_admin FROM users")
    result = cursor.fetchall()
    conn.close()
    return result

async def get_user(user_id: int):
    return await asyncio.to_thread(get_user_sync, user_id)

async def get_admin_password():
    return await asyncio.to_thread(get_admin_password_sync)

async def set_admin_password(new_pass: str):
    return await asyncio.to_thread(set_admin_password_sync, new_pass)

async def set_user_admin(user_id: int, is_admin: bool):
    return await asyncio.to_thread(set_user_admin_sync, user_id, is_admin)

async def remove_admin(user_id: int):
    return await asyncio.to_thread(remove_admin_sync, user_id)

async def get_all_users():
    return await asyncio.to_thread(get_all_users_sync)

def is_senior_admin(user_id: int) -> bool:
    return user_id in SENIOR_ADMINS

def is_admin(user: tuple, user_id: int) -> bool:
    if not user:
        return False
    return bool(user[1]) or is_senior_admin(user_id)

# Состояния
class Form(StatesGroup):
    waiting_for_fio = State()

class AttendanceForm(StatesGroup):
    choosing_reason = State()

class AdminPasswordState(StatesGroup):
    waiting_for_password = State()

class SetAdminPassState(StatesGroup):
    waiting_for_new_pass = State()

class DemoteState(StatesGroup):
    waiting_for_target = State()

# Клавиатуры
cancel_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Отменить")]],
    resize_keyboard=True,
    one_time_keyboard=True
)

reason_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Болею")],
        [KeyboardButton(text="В пробке / ДТП")],
        [KeyboardButton(text="Семейные обстоятельства")],
        [KeyboardButton(text="Другое")],
        [KeyboardButton(text="Отменить")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

# Обновлённый /make_admin
@dp.message(Command("make_admin"))
async def make_admin_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if is_senior_admin(user_id):
        await message.answer("✅ Ты старший админ — права уже максимальны!")
        return

    user = await get_user(user_id)
    if user and user[1]:
        await message.answer("✅ Ты уже обычный админ!")
        return

    await message.answer("🔐 Введи пароль для получения прав админа:", reply_markup=cancel_keyboard)
    await state.set_state(AdminPasswordState.waiting_for_password)

@dp.message(AdminPasswordState.waiting_for_password)
async def process_admin_password(message: types.Message, state: FSMContext):
    if message.text == "Отменить":
        await message.answer("❌ Отменено", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return

    correct_pass = await get_admin_password()
    if message.text == correct_pass:
        await set_user_admin(message.from_user.id, True)
        await message.answer(
            "✅ Ты теперь **обычный админ**!\n\n"
            "Доступные команды:\n"
            "/add_schedule — расписание\n"
            "/add_hw — ДЗ\n"
            "/announce — объявление\n"
            "/users — пользователи\n"
            "/demote — снять админа (только старшие!)",
            parse_mode="Markdown",
            reply_markup=types.ReplyKeyboardRemove()
        )
        logger.info(f"✅ Пользователь {message.from_user.id} стал обычным админом")
    else:
        await message.answer("❌ Неверный пароль!", reply_markup=types.ReplyKeyboardRemove())
        logger.warning(f"❌ Неудачная попытка входа: {message.from_user.id}")
    await state.clear()

# НОВАЯ КОМАНДА: /set_admin_pass
@dp.message(Command("set_admin_pass"))
async def set_admin_pass_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if not is_senior_admin(user_id):
        await message.answer("⛔ Только старшие админы могут менять пароль!")
        return

    await message.answer("🔑 Введи **новый пароль** для админки:", reply_markup=cancel_keyboard)
    await state.set_state(SetAdminPassState.waiting_for_new_pass)

@dp.message(SetAdminPassState.waiting_for_new_pass)
async def process_new_admin_password(message: types.Message, state: FSMContext):
    if message.text == "Отменить":
        await message.answer("❌ Отменено", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return

    new_pass = message.text.strip()
    if len(new_pass) < 4:
        await message.answer("Пароль должен быть не короче 4 символов. Попробуй ещё:", reply_markup=cancel_keyboard)
        return

    await set_admin_password(new_pass)
    await message.answer("✅ Пароль админки успешно обновлён!", reply_markup=types.ReplyKeyboardRemove())
    logger.info(f"🔑 Старший админ {message.from_user.id} сменил пароль")
    await state.clear()

# НОВАЯ КОМАНДА: /demote
@dp.message(Command("demote"))
async def demote_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if not is_senior_admin(user_id):
        await message.answer("⛔ Только старшие админы могут снимать админов!")
        return

    await message.answer(
        "🗑️ Укажи пользователя для снятия:\n"
        "• Ответь на его сообщение, ИЛИ\n"
        "• Напиши его @username или ID\n\n"
        "Пример: @vvertazuu или 123456789",
        reply_markup=cancel_keyboard
    )
    await state.set_state(DemoteState.waiting_for_target)

@dp.message(DemoteState.waiting_for_target)
async def process_demote_target(message: types.Message, state: FSMContext):
    if message.text == "Отменить":
        await message.answer("❌ Отменено", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return

    target_id = None
    target_input = message.text.strip().lstrip("@")

    # Попытка как ID
    if target_input.isdigit():
        target_id = int(target_input)
    else:
        # Поиск по username в чате
        try:
            chat_member = await bot.get_chat(target_input)
            target_id = chat_member.id
        except Exception as e:
            await message.answer("❌ Не удалось найти пользователя. Проверь @username или ID.")
            return

    # Проверка: нельзя снять старшего админа
    if is_senior_admin(target_id):
        await message.answer("⛔ Нельзя снимать старших админов!")
        return

    # Проверка: есть ли такой пользователь и является ли он админом
    user_data = await get_user(target_id)
    if not user_data:
        await message.answer("❌ Пользователь не найден в базе.")
        return

    if not user_data[1]:
        await message.answer("ℹ️ Этот пользователь не является админом.")
        return

    # Снятие прав
    await remove_admin(target_id)
    await message.answer(f"✅ Пользователь `{target_id}` больше не админ.", parse_mode="Markdown")
    logger.info(f"🗑️ Старший админ {message.from_user.id} снял админа {target_id}")
    await state.clear()

# НОВАЯ КОМАНДА: /admins_status
@dp.message(Command("admins_status"))
async def cmd_admins_status(message: types.Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not (user and (user[1] or is_senior_admin(user_id))):
        await message.answer("🔒 Только админы могут видеть этот список.")
        return

    all_users = await get_all_users()
    text = "📋 **Статус админов**\n\n"
    
    # Старшие админы
    for uid in SENIOR_ADMINS:
        name = "Неизвестен"
        for u in all_users:
            if u[0] == uid:
                name = u[1] or f"ID {uid}"
                break
        text += f"👑 **Старший**: {name} (`{uid}`)\n"
    
    # Обычные админы
    ordinary_admins = [u for u in all_users if u[2] and not is_senior_admin(u[0])]
    if ordinary_admins:
        text += "\n🛠 **Обычные админы**:\n"
        for uid, name, _ in ordinary_admins:
            name = name or f"ID {uid}"
            text += f"• {name} (`{uid}`)\n"
    else:
        text += "\n🛠 Обычные админы: *нет*\n"
    
    await message.answer(text, parse_mode="Markdown")

# Остальные хендлеры (без изменений)
# ... (все остальные твои команды: /start, /schedule, /homework, /add_hw, /add_schedule, /attendance и т.д.)
# Они остаются **полностью без изменений**, так как используют `is_admin` через БД

# Ниже — ВСЕ ТВОИ СУЩЕСТВУЮЩИЕ КОМАНДЫ БЕЗ ИЗМЕНЕНИЙ
# Я их не удаляю, просто не повторяю для краткости.
# Но в финальном коде они ДОЛЖНЫ БЫТЬ!

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    result = await get_user(user_id)
    
    if result and result[0]:
        await message.answer(
            f"Привет, {result[0]}! 👋\n\n"
            "/schedule — Расписание Пример:/schedule ДД.ММ.ГГГГ\n"
            "/homework — ДЗ\n"
            "/support — Помощь"
        )
    else:
        await execute_query(
            "INSERT OR IGNORE INTO users (telegram_id, full_name) VALUES (?, ?)",
            (user_id, None)
        )
        await message.answer("👋 Привет! Напиши **ФИО полностью**")
        await state.set_state(Form.waiting_for_fio)

@dp.message(Form.waiting_for_fio)
async def process_fio(message: types.Message, state: FSMContext):
    fio = message.text.strip()
    if len(fio) < 5:
        await message.answer("❌ ФИО слишком короткое. Попробуй ещё:")
        return
    
    await execute_query(
        "UPDATE users SET full_name = ? WHERE telegram_id = ?",
        (fio, message.from_user.id)
    )
    
    await message.answer(f"✅ ФИО сохранено: **{fio}**", parse_mode="Markdown")
    await state.clear()

@dp.message(Command("support"))
async def cmd_support(message: types.Message):
    await message.answer(
        "🛠️ **Помощь**\n\n"
        "• Проблемы с ботом пиши мне: [@vvertazuu](https://t.me/vvertazuu)\n"
        "• Проблемы с Учёбой пиши мне: [@lilalusc](https://t.me/lilalusc)",
        parse_mode="Markdown"
    )

@dp.message(Command("change_fio"))
async def cmd_change_fio(message: types.Message, state: FSMContext):
    await message.answer("✏️ Введите новое **ФИО полностью**", parse_mode="Markdown")
    await state.set_state(Form.waiting_for_fio)

@dp.message(Command("schedule"))
async def cmd_schedule(message: types.Message):
    raw = message.text.replace("/schedule", "", 1).strip()
    
    try:
        if raw:
            target_date = datetime.datetime.strptime(raw, "%d.%m.%Y").date()
        else:
            target_date = datetime.date.today()
    except ValueError:
        await message.answer("❌ Формат: /schedule 01.12.2025")
        return

    DAYS = {
        1: "Понедельник", 2: "Вторник", 3: "Среда", 4: "Четверг",
        5: "Пятница", 6: "Суббота", 7: "Воскресенье"
    }
    day_name = DAYS.get(target_date.isoweekday(), "Неизвестный день")
    
    lessons = await execute_query(
        "SELECT lesson_number, subject, classroom, start_time, end_time, lesson_type, teacher "
        "FROM schedule WHERE date = ? ORDER BY lesson_number",
        (target_date.strftime("%Y-%m-%d"),), fetch=True
    )
    
    if not lessons:
        await message.answer(f"📅 На {day_name.lower()} ({target_date:%d.%m.%Y}) — расписание не задано")
        return
    
    text = f"📅 **{day_name} ({target_date:%d.%m.%Y})**\n\n"
    for row in lessons:
        num, subject, room, start, end, ltype, teacher = row
        
        lesson_str = f"{num}. **{subject}**"
        if ltype:
            lesson_str += f" ({ltype})"
        
        details = []
        if start and end:
            details.append(f"🕗 {start}-{end}")
        if room:
            details.append(f"📍 {room}")
        if teacher:
            details.append(f"👩‍🏫 {teacher}")
        
        if details:
            lesson_str += "\n   • " + "\n   • ".join(details)
        
        text += lesson_str + "\n\n"
    
    if len(text) > 4000:
        text = text[:3997] + "..."
    
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("announce"))
async def cmd_announce(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user or not user[1]:
        if not is_senior_admin(message.from_user.id):
            await message.answer("Только админ")
            return

    text = message.text.replace("/announce", "", 1).strip()
    if not text:
        await message.answer("Использование: /announce Текст")
        return

    users = await execute_query("SELECT telegram_id FROM users", fetch=True)
    sent = failed = 0
    for (tg_id,) in users:
        try:
            await bot.send_message(tg_id, f"**Объявление**\n\n{text}", parse_mode="Markdown")
            sent += 1
        except Exception as e:
            logger.warning(f"Не удалось отправить {tg_id}: {e}")
            failed += 1

    await message.answer(f"Отправлено: {sent}, ошибок: {failed}")

class ClearHomework(StatesGroup):
    confirming = State()

@dp.message(Command("clear_homework"))
async def clear_homework_start(message: types.Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    if not user or not user[1]:
        if not is_senior_admin(message.from_user.id):
            await message.answer("🚫 Только админ")
            return
    
    count = await execute_query("SELECT COUNT(*) FROM homework", fetch=True)
    total = count[0][0] if count else 0
    
    await message.answer(
        f"⚠️ <b>Внимание!</b>\n\n"
        f"Вы собираетесь удалить <b>все домашние задания</b> ({total} записей).\n\n"
        "Это действие нельзя отменить!\n\n"
        "Подтвердите удаление, отправив: <code>ДА, УДАЛИТЬ ДЗ</code>",
        parse_mode="HTML",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="ДА, УДАЛИТЬ ДЗ")]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )
    await state.set_state(ClearHomework.confirming)

@dp.message(ClearHomework.confirming)
async def clear_homework_confirm(message: types.Message, state: FSMContext):
    if message.text == "ДА, УДАЛИТЬ ДЗ":
        result = await execute_query("DELETE FROM homework")
        await message.answer(
            f"✅ <b>Домашние задания очищены!</b>\n\n"
            f"Удалено записей: {result}",
            parse_mode="HTML",
            reply_markup=types.ReplyKeyboardRemove()
        )
        logger.info(f"🧹 Админ {message.from_user.id} очистил домашние задания ({result} записей)")
    else:
        await message.answer(
            "❌ Очистка ДЗ отменена",
            reply_markup=types.ReplyKeyboardRemove()
        )
    
    await state.clear()

class ClearSchedule(StatesGroup):
    confirming = State()

@dp.message(Command("clear_schedule"))
async def clear_schedule_start(message: types.Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    if not user or not user[1]:
        if not is_senior_admin(message.from_user.id):
            await message.answer("🚫 Только админ")
            return
    
    count = await execute_query("SELECT COUNT(*) FROM schedule", fetch=True)
    total = count[0][0] if count else 0
    
    await message.answer(
        f"⚠️ <b>Внимание!</b>\n\n"
        f"Вы собираетесь удалить <b>всё расписание</b> ({total} записей).\n\n"
        "Это действие нельзя отменить!\n\n"
        "Подтвердите удаление, отправив: <code>ДА, УДАЛИТЬ ВСЁ</code>",
        parse_mode="HTML",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="ДА, УДАЛИТЬ ВСЁ")]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )
    await state.set_state(ClearSchedule.confirming)

@dp.message(ClearSchedule.confirming)
async def clear_schedule_confirm(message: types.Message, state: FSMContext):
    if message.text == "ДА, УДАЛИТЬ ВСЁ":
        result = await execute_query("DELETE FROM schedule")
        await message.answer(
            f"✅ <b>Расписание очищено!</b>\n\n"
            f"Удалено записей: {result}",
            parse_mode="HTML",
            reply_markup=types.ReplyKeyboardRemove()
        )
        logger.info(f"🧹 Админ {message.from_user.id} очистил расписание ({result} записей)")
    else:
        await message.answer(
            "❌ Очистка расписания отменена",
            reply_markup=types.ReplyKeyboardRemove()
        )
    
    await state.clear()

@dp.message(Command("whoami"))
async def cmd_whoami(message: types.Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("❌ Вы не зарегистрированы. Напишите /start")
        return

    full_name, is_admin_db = user
    is_senior = is_senior_admin(user_id)
    if is_senior:
        admin_status = "👑 Старший админ"
    elif is_admin_db:
        admin_status = "🛠 Обычный админ"
    else:
        admin_status = "❌ Не админ"
    
    await message.answer(
        f"👤 **Ваша информация**\n\n"
        f"🔹 ID: `{user_id}`\n"
        f"🔹 ФИО: {full_name or 'не указано'}\n"
        f"🔹 Статус: {admin_status}",
        parse_mode="Markdown"
    )

@dp.message(Command("homework"))
async def cmd_homework(message: types.Message):
    today = datetime.date.today().strftime("%Y-%m-%d")
    hw_list = await execute_query(
        "SELECT subject, description, due_date FROM homework WHERE due_date >= ? ORDER BY due_date",
        (today,), fetch=True
    )
    
    if not hw_list:
        await message.answer("📚 Нет ДЗ")
        return
    
    text = "📚 **Домашние задания**\n\n"
    for subject, desc, due in hw_list:
        text += f"📌 *{subject}* (до {due})\n{desc}\n\n"
    
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("add_hw"))
async def cmd_add_hw(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user or not user[1]:
        if not is_senior_admin(message.from_user.id):
            await message.answer("Только админ")
            return
    
    raw = message.text.replace("/add_hw", "", 1).strip()
    if ":" not in raw:
        await message.answer("/add_hw Математика: Задачи 1-10 до 01.11")
        return
    
    subject, rest = raw.split(":", 1)
    subject = subject.strip()
    rest = rest.strip()
    
    today = datetime.date.today()
    due_date = today + datetime.timedelta(days=2)
    desc_part = rest

    if " до " in rest:
        try:
            desc_part, date_part = rest.rsplit(" до ", 1)
            date_part = date_part.strip()
            due_date = datetime.datetime.strptime(date_part, "%d.%m").date()
            if due_date < today.replace(year=due_date.year):
                due_date = due_date.replace(year=today.year + 1)
            else:
                due_date = due_date.replace(year=today.year)
        except ValueError:
            desc_part = rest
            due_date = today + datetime.timedelta(days=2)
    else:
        desc_part = rest

    due_date_str = due_date.strftime("%Y-%m-%d")

    await execute_query(
        "INSERT INTO homework (subject, description, due_date, added_by) VALUES (?, ?, ?, ?)",
        (subject, desc_part.strip(), due_date_str, message.from_user.id)
    )
    
    await message.answer(f"ДЗ по **{subject}** до {due_date:%d.%m}", parse_mode="Markdown")

@dp.message(Command("add_schedule"))
async def cmd_add_schedule(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user or not user[1]:
        if not is_senior_admin(message.from_user.id):
            await message.answer("🚫 Только админ")
            return
    
    raw = message.text.replace("/add_schedule", "", 1).strip()
    if ":" not in raw:
        await message.answer(
            "Формат: /add_schedule 01.12.2025: "
            "1. 11:50-13:20 Иностранный язык (семинар) (305к.1) Казакова Е.Д., "
            "2. 13:50-15:20 Правовое обеспечение (семинар) (315к.1) Магомедрасулова Э.З."
        )
        return
    
    date_part, lessons_part = raw.split(":", 1)
    date_part = date_part.strip()
    
    try:
        target_date = datetime.datetime.strptime(date_part, "%d.%m.%Y").date()
    except ValueError:
        await message.answer("❌ Формат даты: 01.12.2025")
        return
    
    await execute_query("DELETE FROM schedule WHERE date = ?", (target_date.strftime("%Y-%m-%d"),))
    
    lessons = [lesson.strip() for lesson in lessons_part.split(",") if lesson.strip()]
    if not lessons:
        await message.answer("⚠️ Не найдено уроков для добавления")
        return
    
    success_count = 0
    for lesson in lessons:
        try:
            num_part, rest = lesson.split(".", 1)
            lesson_num = int(num_part.strip())
            
            time_match = re.search(r"(\d{2}:\d{2})-(\d{2}:\d{2})", rest)
            start_time = time_match.group(1) if time_match else None
            end_time = time_match.group(2) if time_match else None
            
            if time_match:
                rest = rest.replace(f"{start_time}-{end_time}", "").strip()
            
            lesson_type = ""
            if "(" in rest and ")" in rest:
                lesson_type = rest.split("(")[1].split(")")[0].strip()
                rest = rest.replace(f"({lesson_type})", "").strip()
            
            classroom = ""
            if "(" in rest and ")" in rest:
                classroom = rest.split("(")[1].split(")")[0].strip()
                rest = rest.replace(f"({classroom})", "").strip()
            
            parts = rest.split()
            if len(parts) >= 2:
                subject = " ".join(parts[:-1])
                teacher = parts[-1]
            else:
                subject = rest
                teacher = ""
            
            await execute_query(
                "INSERT INTO schedule (date, lesson_number, subject, classroom, start_time, end_time, lesson_type, teacher) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (target_date.strftime("%Y-%m-%d"), lesson_num, subject, classroom, start_time, end_time, lesson_type, teacher)
            )
            success_count += 1
            
        except Exception as e:
            logger.error(f"Ошибка парсинга урока '{lesson}': {str(e)}")
            continue
    
    if success_count:
        await message.answer(f"✅ Добавлено {success_count} уроков на {target_date:%d.%m.%Y}")
    else:
        await message.answer("❌ Не удалось добавить ни одного урока")

@dp.message(Command("attendance"))
async def cmd_attendance(message: types.Message):
    today = datetime.date.today()
    month_ago = today - datetime.timedelta(days=30)
    
    total_rows = await execute_query(
        "SELECT COUNT(*) FROM attendance WHERE user_id = ? AND date BETWEEN ? AND ?",
        (message.from_user.id, month_ago.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")), fetch=True
    )
    total = total_rows[0][0] if total_rows else 0

    present_rows = await execute_query(
        "SELECT COUNT(*) FROM attendance WHERE user_id = ? AND date BETWEEN ? AND ? AND status = 'present'",
        (message.from_user.id, month_ago.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")), fetch=True
    )
    present = present_rows[0][0] if present_rows else 0
    
    percentage = round((present / total * 100) if total > 0 else 0, 1)
    
    await message.answer(
        f"**Посещаемость (30 дней)**\n\n"
        f"Присутствовал: {present}/{total}\n"
        f"**{percentage}%**\n\n"
        "Напиши дату: 17.11.2025",
        parse_mode="Markdown"
    )

@dp.message(lambda msg: msg.text and len(msg.text) == 10 and msg.text.count('.') == 2)
async def handle_date(message: types.Message):
    try:
        date = datetime.datetime.strptime(message.text, "%d.%m.%Y").date()
        result = await execute_query(
            "SELECT status, reason FROM attendance WHERE user_id = ? AND date = ?",
            (message.from_user.id, date.strftime("%Y-%m-%d")), fetch=True
        )
        
        if not result:
            await message.answer(f"❌ {date:%d.%m.%Y}: Нет отметки")
            return
        
        status, reason = result[0]
        if status == "present":
            await message.answer(f"✅ {date:%d.%m.%Y}: Присутствовал")
        elif status == "absent":
            reason_text = f"\nПричина: {reason}" if reason else ""
            await message.answer(f"❌ {date:%d.%m.%Y}: Отсутствовал{reason_text}")
        else:
            await message.answer(f"🕒 {date:%d.%m.%Y}: Опоздал")
    except Exception as e:
        logger.warning(f"Ошибка обработки даты: {e}")

@dp.message(Command("birthday"))
async def cmd_birthday(message: types.Message):
    user_is_admin = False
    user = await get_user(message.from_user.id)
    if user and user[1]:
        user_is_admin = True
    if is_senior_admin(message.from_user.id):
        user_is_admin = True
        
    if not user_is_admin:
        await message.answer("Только админ может устанавливать дни рождения")
        return

    raw = message.text.replace("/birthday", "", 1).strip()
    if not raw:
        await message.answer("Использование:\n/birthday ФИО ДД.ММ\nПример: /birthday Иванов Иван 15.05")
        return

    parts = raw.split()
    if len(parts) < 2:
        await message.answer("Укажи ФИО и дату: /birthday Иванов Иван 15.05")
        return

    date_str = parts[-1]
    name = " ".join(parts[:-1])

    try:
        birth_date = datetime.datetime.strptime(date_str, "%d.%m").date()
        birth_date = birth_date.replace(year=2000)
    except ValueError:
        await message.answer("Неверный формат даты. Используй: ДД.ММ")
        return

    matches = await execute_query(
        "SELECT telegram_id FROM users WHERE full_name LIKE ?",
        (f"%{name}%",), fetch=True
    )

    if not matches:
        await message.answer(f"Студент '{name}' не найден")
        return
    if len(matches) > 1:
        names_list = await execute_query(
            "SELECT full_name FROM users WHERE full_name LIKE ?", (f"%{name}%",), fetch=True
        )
        names = "\n".join([f"• {row[0]}" for row in names_list])
        await message.answer(f"Найдено несколько:\n{names}\n\nУточни ФИО")
        return

    user_id = matches[0][0]
    await execute_query(
        "UPDATE users SET birth_date = ? WHERE telegram_id = ?",
        (birth_date, user_id)
    )
    await message.answer(f"ДР для **{name}** установлен: **{date_str}**", parse_mode="Markdown")

@dp.message(Command("birthdays"))
async def cmd_birthdays_list(message: types.Message):
    user_is_admin = False
    user = await get_user(message.from_user.id)
    if user and user[1]:
        user_is_admin = True
    if is_senior_admin(message.from_user.id):
        user_is_admin = True
        
    if not user_is_admin:
        await message.answer("Только админ")
        return

    students = await execute_query(
        "SELECT full_name, birth_date, telegram_id FROM users WHERE full_name IS NOT NULL ORDER BY full_name",
        fetch=True
    )

    if not students:
        await message.answer("Нет студентов в базе")
        return

    text = "**Список студентов и ДР**\n\n"
    for name, bdate, tg_id in students:
        if bdate and len(bdate) >= 10:
            bdate_str = f"{bdate[8:10]}.{bdate[5:7]}"
        else:
            bdate_str = "не указан"
        text += f"• {name} (`{tg_id}`) — {bdate_str}\n"

    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("users"))
async def cmd_users(message: types.Message):
    user_is_admin = False
    user = await get_user(message.from_user.id)
    if user and user[1]:
        user_is_admin = True
    if is_senior_admin(message.from_user.id):
        user_is_admin = True
        
    if not user_is_admin:
        await message.answer("Только админ")
        return

    all_users = await execute_query(
        "SELECT full_name, telegram_id, joined_at, is_admin FROM users ORDER BY joined_at",
        fetch=True
    )

    if not all_users:
        await message.answer("Пользователей нет")
        return

    text = "**Все пользователи**\n\n"
    for name, tg_id, joined, is_admin in all_users:
        name = name or "ФИО не указано"
        admin_mark = " (админ)" if is_admin else ""
        joined_str = joined[:10] if joined else "?"
        text += f"• {name}{admin_mark} — `{tg_id}` — {joined_str}\n"

    if len(text) > 3900:
        parts = [text[i:i+3900] for i in range(0, len(text), 3900)]
        for part in parts:
            await message.answer(part, parse_mode="Markdown")
    else:
        await message.answer(text, parse_mode="Markdown")

@dp.message(Command("reason"))
async def cmd_reason(message: types.Message, state: FSMContext):
    await message.answer("Выбери причину:", reply_markup=reason_keyboard)
    await state.set_state(AttendanceForm.choosing_reason)

@dp.message(AttendanceForm.choosing_reason)
async def process_reason(message: types.Message, state: FSMContext):
    if message.text == "Отменить":
        await message.answer("Отменено", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return
    
    today = datetime.date.today()
    await execute_query(
        "INSERT OR REPLACE INTO attendance (user_id, date, status, reason, marked_by) VALUES (?, ?, ?, ?, ?)",
        (message.from_user.id, today.strftime("%Y-%m-%d"), 'absent', message.text, message.from_user.id)
    )
    
    await message.answer(f"✅ Причина: **{message.text}**", reply_markup=types.ReplyKeyboardRemove(), parse_mode="Markdown")
    await state.clear()

# Ежедневная задача: поздравление с ДР
async def birthday_task():
    while True:
        now = datetime.datetime.now()
        next_run = (now + datetime.timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        sleep_time = (next_run - now).total_seconds()
        if sleep_time < 0:
            sleep_time = 0
        await asyncio.sleep(sleep_time)

        today = datetime.date.today()
        today_str = today.strftime("%m-%d")

        birthdays = await execute_query(
            "SELECT telegram_id, full_name FROM users WHERE strftime('%m-%d', birth_date) = ? AND birth_date IS NOT NULL",
            (today_str,), fetch=True
        )

        for tg_id, name in birthdays:
            try:
                await bot.send_message(
                    tg_id,
                    f"**С ДНЁМ РОЖДЕНИЯ, {name}!**\n\n"
                    f"Пусть этот день будет полон радости, улыбок и хорошего настроения!\n"
                    f"Желаем успехов в учёбе и всего самого лучшего!",
                    parse_mode="Markdown"
                )
                logger.info(f"Поздравил {name} ({tg_id}) с ДР")
            except Exception as e:
                logger.error(f"Не удалось поздравить {tg_id}: {e}")

# ВЕБ-СЕРВЕР ДЛЯ RENDER
async def health_check(request):
    return web.Response(text="OK")

async def web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Веб-сервер запущен на порту {port}")

async def run_bot():
    init_db()
    logger.info("Бот запущен!")
    await dp.start_polling(bot)

async def shutdown(signal, loop):
    logger.info(f"Получен сигнал {signal.name}...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [task.cancel() for task in tasks]
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()

async def main():
    loop = asyncio.get_running_loop()
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda s=sig: asyncio.create_task(shutdown(s, loop))
        )
    
    await asyncio.gather(
        web_server(),
        run_bot(),
        birthday_task()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную")
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
        raise
