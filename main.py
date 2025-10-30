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

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 🔑 ТОКЕН (добавь в Render Environment Variables)
BOT_TOKEN = os.getenv("BOT_TOKEN", "8407995258:AAE1I7paypCciMW4hBTdCjLhByGlwF35PNs")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ADMINS
ADMINS = [7450525550, 5946158486]

# Инициализация БД
def init_db():
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id BIGINT UNIQUE NOT NULL,
            full_name TEXT,
            birth_date DATE,
            is_admin BOOLEAN DEFAULT 0,
            joined_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day_of_week INTEGER NOT NULL,
            lesson_number INTEGER NOT NULL,
            subject TEXT NOT NULL,
            classroom TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS homework (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            description TEXT NOT NULL,
            due_date DATE NOT NULL,
            added_by BIGINT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id BIGINT NOT NULL,
            date DATE NOT NULL,
            status TEXT NOT NULL DEFAULT 'present',
            reason TEXT,
            marked_by BIGINT NOT NULL,
            marked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, date)
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

# Состояния
class Form(StatesGroup):
    waiting_for_fio = State()

class AttendanceForm(StatesGroup):
    choosing_reason = State()

# Утилиты
def get_user(user_id: int):
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT full_name, is_admin FROM users WHERE telegram_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result

def execute_query(query, params=(), fetch=False):
    conn = sqlite3.connect('school_bot.db')
    cursor = conn.cursor()
    cursor.execute(query, params)
    if fetch:
        result = cursor.fetchall() if "SELECT" in query.upper() else cursor.fetchone()
    else:
        result = cursor.rowcount
    conn.commit()
    conn.close()
    return result

# Клавиатура причин
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

# Хендлеры
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    result = get_user(user_id)
    
    if result and result[0]:
        await message.answer(
            f"Привет, {result[0]}! 👋\n\n"
            "/schedule — Расписание\n"
            "/homework — ДЗ\n"
            "/attendance — Посещаемость\n"
            "/support — Помощь"
        )
    else:
        execute_query(
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
    
    execute_query(
        "UPDATE users SET full_name = ? WHERE telegram_id = ?",
        (fio, message.from_user.id)
    )
    
    await message.answer(f"✅ ФИО сохранено: **{fio}**")
    await state.clear()

@dp.message(Command("support"))
async def cmd_support(message: types.Message):
    await message.answer(
        "🛠️ **Помощь**\n\n"
        "• Проблемы с ботом пиши мне: [@vvertazuu](https://t.me/vvertazuu)\n"
        "• Проблемы с Учёбой пищи мне: [@lilalusc](https://t.me/lilalusc)",
        parse_mode="Markdown"
    )

@dp.message(Command("schedule"))
async def cmd_schedule(message: types.Message):
    raw = message.text.replace("/schedule", "", 1).strip()
    
    try:
        if raw:
            target_date = datetime.datetime.strptime(raw, "%d.%m.%Y").date()
        else:
            target_date = datetime.date.today()
    except ValueError:
        await message.answer("Формат: /schedule 30.10.2025")
        return

    day_of_week = target_date.isoweekday()
    DAYS = {
        1: "Понедельник", 2: "Вторник", 3: "Среда", 4: "Четверг",
        5: "Пятница", 6: "Суббота", 7: "Воскресенье"
    }
    
    lessons = execute_query(
        "SELECT lesson_number, subject, classroom FROM schedule WHERE day_of_week = ? ORDER BY lesson_number",
        (day_of_week,), fetch=True
    )
    
    if not lessons:
        await message.answer(f"📅 На {DAYS[day_of_week].lower()} ({target_date:%d.%m.%Y}) — пусто")
        return
    
    text = f"📅 **{DAYS[day_of_week]} ({target_date:%d.%m.%Y})**\n\n"
    for num, subject, room in lessons:
        room_str = f" (каб. {room})" if room else ""
        text += f"{num}. {subject}{room_str}\n"
    
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("announce"))
async def cmd_announce(message: types.Message):
    user = get_user(message.from_user.id)
    if not user or not user[1]:
        await message.answer("Только админ")
        return

    text = message.text.replace("/announce", "", 1).strip()
    if not text:
        await message.answer("Использование: /announce Текст")
        return

    users = execute_query("SELECT telegram_id FROM users", fetch=True)
    sent = failed = 0
    for (tg_id,) in users:
        try:
            await bot.send_message(tg_id, f"**Объявление**\n\n{text}", parse_mode="Markdown")
            sent += 1
        except:
            failed += 1

    await message.answer(f"Отправлено: {sent}, ошибок: {failed}")

ADMINS = [7450525550, 5946158486]

@dp.message(Command("make_admin"))
async def make_admin(message: types.Message):
    if message.from_user.id in ADMINS:
        conn = sqlite3.connect('school_bot.db')
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_admin = 1 WHERE telegram_id = ?", (message.from_user.id,))
        conn.commit()
        conn.close()
        await message.answer("✅ Ты теперь админ!")
    else:
        await message.answer("❌ Эта команда только для владельца.")

@dp.message(Command("homework"))
async def cmd_homework(message: types.Message):
    today = datetime.date.today()
    hw_list = execute_query(
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
    user = get_user(message.from_user.id)
    if not user or not user[1]:
        await message.answer("Только админ")
        return
    
    raw = message.text.replace("/add_hw", "", 1).strip()
    if ":" not in raw:
        await message.answer("/add_hw Математика: Задачи 1-10 до 01.11")
        return
    
    subject, rest = raw.split(":", 1)
    subject = subject.strip()
    rest = rest.strip()
    
    today = datetime.date.today()  # ← Сначала получаем today
    due_date = today + datetime.timedelta(days=2)  # ← По умолчанию +2 дня
    desc_part = rest

    if " до " in rest:
        try:
            desc_part, date_part = rest.rsplit(" до ", 1)
            date_part = date_part.strip()
            due_date = datetime.datetime.strptime(date_part, "%d.%m").date()
            
            # Если дата в прошлом — переносим на следующий год
            if due_date < today:
                due_date = due_date.replace(year=today.year + 1)
            else:
                due_date = due_date.replace(year=today.year)
        except ValueError:
            # Если дата не распознана — оставляем +2 дня
            desc_part = rest
            due_date = today + datetime.timedelta(days=2)
    else:
        desc_part = rest

    execute_query(
        "INSERT INTO homework (subject, description, due_date, added_by) VALUES (?, ?, ?, ?)",
        (subject, desc_part.strip(), due_date, message.from_user.id)
    )
    
    await message.answer(f"ДЗ по **{subject}** до {due_date:%d.%m}", parse_mode="Markdown")@dp.message(Command("add_hw"))
async def cmd_add_hw(message: types.Message):
    user = get_user(message.from_user.id)
    if not user or not user[1]:
        await message.answer("Только админ")
        return
    
    raw = message.text.replace("/add_hw", "", 1).strip()
    if ":" not in raw:
        await message.answer("/add_hw Математика: Задачи 1-10 до 01.11")
        return
    
    subject, rest = raw.split(":", 1)
    subject = subject.strip()
    rest = rest.strip()
    
    today = datetime.date.today()  # ← Сначала получаем today
    due_date = today + datetime.timedelta(days=2)  # ← По умолчанию +2 дня
    desc_part = rest

    if " до " in rest:
        try:
            desc_part, date_part = rest.rsplit(" до ", 1)
            date_part = date_part.strip()
            due_date = datetime.datetime.strptime(date_part, "%d.%m").date()
            
            # Если дата в прошлом — переносим на следующий год
            if due_date < today:
                due_date = due_date.replace(year=today.year + 1)
            else:
                due_date = due_date.replace(year=today.year)
        except ValueError:
            # Если дата не распознана — оставляем +2 дня
            desc_part = rest
            due_date = today + datetime.timedelta(days=2)
    else:
        desc_part = rest

    execute_query(
        "INSERT INTO homework (subject, description, due_date, added_by) VALUES (?, ?, ?, ?)",
        (subject, desc_part.strip(), due_date, message.from_user.id)
    )
    
    await message.answer(f"ДЗ по **{subject}** до {due_date:%d.%m}", parse_mode="Markdown")

@dp.message(Command("add_schedule"))
async def cmd_add_schedule(message: types.Message):
    if not get_user(message.from_user.id)[1]:
        await message.answer("🚫 Только админ")
        return
    
    raw = message.text.replace("/add_schedule", "", 1).strip()
    if ":" not in raw:
        await message.answer("/add_schedule 30.10.2025: 1. Математика (304), 2. Физика")
        return
    
    date_part, lessons_part = raw.split(":", 1)
    date_part = date_part.strip()
    
    try:
        if len(date_part) == 10:
            date_obj = datetime.datetime.strptime(date_part, "%d.%m.%Y").date()
        else:
            raise ValueError
    except:
        await message.answer("Формат даты: 30.10.2025")
        return
    
    day_of_week = date_obj.isoweekday()
    
    execute_query("DELETE FROM schedule WHERE day_of_week = ?", (day_of_week,))
    
    for lesson in lessons_part.split(","):
        lesson = lesson.strip()
        if "." not in lesson:
            continue
        try:
            num_part, rest = lesson.split(".", 1)
            lesson_num = int(num_part.strip())
            if "(" in rest and ")":
                subject = rest.split("(")[0].strip()
                classroom = rest.split("(")[1].split(")")[0].strip()
            else:
                subject, classroom = rest.strip(), ""
            
            execute_query(
                "INSERT INTO schedule VALUES (NULL, ?, ?, ?, ?)",
                (day_of_week, lesson_num, subject, classroom)
            )
        except:
            continue
    
    await message.answer(f"✅ Расписание на {date_obj:%d.%m.%Y} обновлено")

@dp.message(Command("attendance"))
async def cmd_attendance(message: types.Message):
    today = datetime.date.today()
    month_ago = today - datetime.timedelta(days=30)
    
    total_rows = execute_query(
        "SELECT COUNT(*) FROM attendance WHERE user_id = ? AND date BETWEEN ? AND ?",
        (message.from_user.id, month_ago, today), fetch=True
    )
    total = total_rows[0][0] if total_rows else 0

    present_rows = execute_query(
        "SELECT COUNT(*) FROM attendance WHERE user_id = ? AND date BETWEEN ? AND ? AND status = 'present'",
        (message.from_user.id, month_ago, today), fetch=True
    )
    present = present_rows[0][0] if present_rows else 0
    
    percentage = round((present / total * 100) if total > 0 else 0, 1)
    
    await message.answer(
        f"**Посещаемость (30 дней)**\n\n"
        f"Присутствовал: {present}/{total}\n"
        f"**{percentage}%**\n\n"
        "Напиши дату: 30.10.2025",
        parse_mode="Markdown"
    )

@dp.message(F.text.regexp(r'\d{2}\.\d{2}\.\d{4}'))
async def handle_date(message: types.Message):
    try:
        date = datetime.datetime.strptime(message.text, "%d.%m.%Y").date()
        result = execute_query(
            "SELECT status, reason FROM attendance WHERE user_id = ? AND date = ?",
            (message.from_user.id, date), fetch=True
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
    except:
        pass

# === ДЕНЬ РОЖДЕНИЯ: УСТАНОВКА (ТОЛЬКО АДМИН) ===
@dp.message(Command("birthday"))
async def cmd_birthday(message: types.Message):
    user = get_user(message.from_user.id)
    if not user or not user[1]:
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
        # Устанавливаем год как текущий, только день и месяц
        birth_date = birth_date.replace(year=datetime.date.today().year)
    except ValueError:
        await message.answer("Неверный формат даты. Используй: ДД.ММ")
        return

    # Ищем пользователя по ФИО (частичное совпадение)
    matches = execute_query(
        "SELECT telegram_id FROM users WHERE full_name LIKE ?",
        (f"%{name}%",), fetch=True
    )

    if not matches:
        await message.answer(f"Студент '{name}' не найден")
        return
    if len(matches) > 1:
        names = "\n".join([f"• {row[1]}" for row in execute_query(
            "SELECT full_name FROM users WHERE full_name LIKE ?", (f"%{name}%",), fetch=True
        )])
        await message.answer(f"Найдено несколько:\n{names}\n\nУточни ФИО")
        return

    user_id = matches[0][0]
    execute_query(
        "UPDATE users SET birth_date = ? WHERE telegram_id = ?",
        (birth_date, user_id)
    )
    await message.answer(f"ДР для **{name}** установлен: **{date_str}**", parse_mode="Markdown")


# === СПИСОК СТУДЕНТОВ С ДР (ТОЛЬКО АДМИН) ===
@dp.message(Command("birthdays"))
async def cmd_birthdays_list(message: types.Message):
    user = get_user(message.from_user.id)
    if not user or not user[1]:
        await message.answer("Только админ")
        return

    students = execute_query(
        "SELECT full_name, birth_date, telegram_id FROM users WHERE full_name IS NOT NULL ORDER BY full_name",
        fetch=True
    )

    if not students:
        await message.answer("Нет студентов в базе")
        return

    text = "**Список студентов и ДР**\n\n"
    for name, bdate, tg_id in students:
        bdate_str = bdate.strftime("%d.%m") if bdate else "не указан"
        text += f"• {name} (`{tg_id}`) — {bdate_str}\n"

    await message.answer(text, parse_mode="Markdown")


# === СПИСОК ВСЕХ ПОЛЬЗОВАТЕЛЕЙ (ТОЛЬКО АДМИН) ===
@dp.message(Command("users"))
async def cmd_users(message: types.Message):
    user = get_user(message.from_user.id)
    if not user or not user[1]:
        await message.answer("Только админ")
        return

    all_users = execute_query(
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

    # Если слишком длинно — разобьём
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
        await message.answer("Отменено", reply_markup=ReplyKeyboardRemove())
        await state.clear()
        return
    
    today = datetime.date.today()
    execute_query(
        "INSERT OR REPLACE INTO attendance (user_id, date, status, reason, marked_by) VALUES (?, ?, ?, ?, ?)",
        (message.from_user.id, today, 'absent', message.text, message.from_user.id)
    )
    
    await message.answer(f"✅ Причина: **{message.text}**", reply_markup=ReplyKeyboardRemove())
    await state.clear()

# Админ команды (announce, mark, students, reasons, birthday, make_admin)
# ... (добавь их аналогично, если нужно. Я сократил для краткости)

async def birthday_task():
    while True:
        now = datetime.datetime.now()
        next_run = (now + datetime.timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        await asyncio.sleep((next_run - now).total_seconds())

        today = datetime.date.today()
        today_str = today.strftime("%m-%d")

        birthdays = execute_query(
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
