import asyncio
import logging
import os
import sqlite3
import sys
from datetime import datetime
from typing import Dict

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

from pyrogram import Client, errors, types as pyro_types

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Конфигурация ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
WORK_DIR = "/home/ubuntu/telegram_bot/"
DEVELOPERS = "YTSmailDog, SmailLabs"

# --- База данных ---
def init_db():
    if not os.path.exists(WORK_DIR): os.makedirs(WORK_DIR)
    conn = sqlite3.connect(os.path.join(WORK_DIR, 'bot_data.db'))
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, user_id INTEGER, text TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS scheduled_messages (id INTEGER PRIMARY KEY, user_id INTEGER, chat_id TEXT, text TEXT, send_at DATETIME)')
    cursor.execute('CREATE TABLE IF NOT EXISTS ghost_mode (user_id INTEGER PRIMARY KEY, enabled INTEGER DEFAULT 0)')
    conn.commit()
    conn.close()

init_db()

def get_ghost_mode(user_id):
    conn = sqlite3.connect(os.path.join(WORK_DIR, 'bot_data.db'))
    cursor = conn.cursor()
    cursor.execute("SELECT enabled FROM ghost_mode WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else 0

def set_ghost_mode(user_id, enabled):
    conn = sqlite3.connect(os.path.join(WORK_DIR, 'bot_data.db'))
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO ghost_mode (user_id, enabled) VALUES (?, ?)", (user_id, enabled))
    conn.commit()
    conn.close()

# --- Состояния FSM ---
class AuthStates(StatesGroup):
    waiting_for_api_id = State()
    waiting_for_api_hash = State()
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()

class ActionStates(StatesGroup):
    waiting_for_msg_target = State()
    waiting_for_msg_text = State()
    waiting_for_sticker_target = State()
    waiting_for_emoji_target = State()
    waiting_for_clear_target = State()
    waiting_for_scheduled_target = State()
    waiting_for_scheduled_text = State()
    waiting_for_scheduled_time = State()

# --- Глобальные объекты ---
bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher(storage=MemoryStorage())
user_clients: Dict[int, Client] = {}

# --- Клавиатуры ---
def get_main_kb():
    buttons = [
        [KeyboardButton(text="📱 Аккаунт"), KeyboardButton(text="📝 Заметки")],
        [KeyboardButton(text="✉️ Сообщение"), KeyboardButton(text="🕒 Отложенное")],
        [KeyboardButton(text="📸 История"), KeyboardButton(text="📢 Каналы")],
        [KeyboardButton(text="👻 Призрак"), KeyboardButton(text="🎭 Стикеров")],
        [KeyboardButton(text="😀 Эмодзи"), KeyboardButton(text="🧹 Очистка")],
        [KeyboardButton(text="🔄 Перезапуск")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_auth_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔐 Начать авторизацию", callback_data="start_auth")]])

def get_ghost_kb(user_id):
    enabled = get_ghost_mode(user_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Включить" if not enabled else "🟢 Включено", callback_data="ghost_on")],
        [InlineKeyboardButton(text="❌ Выключить" if enabled else "🔴 Выключено", callback_data="ghost_off")]
    ])

# --- Обработчики ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(f"👋 Привет! Разработчики: {DEVELOPERS}\nИспользуйте меню ниже:", reply_markup=get_main_kb())

@dp.callback_query(F.data == "start_auth")
async def start_auth(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Введите API ID:")
    await state.set_state(AuthStates.waiting_for_api_id)
    await callback.answer()

@dp.message(AuthStates.waiting_for_api_id)
async def process_api_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("API ID должен быть числом:")
    await state.update_data(api_id=message.text.strip())
    await message.answer("Введите API Hash:")
    await state.set_state(AuthStates.waiting_for_api_hash)

@dp.message(AuthStates.waiting_for_api_hash)
async def process_api_hash(message: types.Message, state: FSMContext):
    await state.update_data(api_hash=message.text.strip())
    await message.answer("Введите номер телефона (+7...):")
    await state.set_state(AuthStates.waiting_for_phone)

@dp.message(AuthStates.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    data = await state.get_data()
    phone = message.text.strip().replace(" ", "")
    user_id = message.from_user.id
    
    # Очистка старой сессии
    session_path = os.path.join(WORK_DIR, f"session_{user_id}.session")
    if os.path.exists(session_path): os.remove(session_path)
    
    client = Client(name=f"session_{user_id}", api_id=int(data['api_id']), api_hash=data['api_hash'], phone_number=phone, workdir=WORK_DIR)
    try:
        await client.connect()
        code_info = await client.send_code(phone)
        await state.update_data(phone=phone, phone_code_hash=code_info.phone_code_hash)
        user_clients[user_id] = client
        await message.answer("Введите код из Telegram:")
        await state.set_state(AuthStates.waiting_for_code)
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
        await state.clear()

@dp.message(AuthStates.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    data = await state.get_data()
    client = user_clients.get(message.from_user.id)
    if not client: return await message.answer("Ошибка сессии. /start")
    try:
        await client.sign_in(data['phone'], data['phone_code_hash'], message.text.strip().replace(" ", ""))
        await message.answer("✅ Авторизовано!", reply_markup=get_main_kb())
        await state.clear()
    except errors.SessionPasswordNeeded:
        await message.answer("Введите пароль 2FA:")
        await state.set_state(AuthStates.waiting_for_password)
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

@dp.message(AuthStates.waiting_for_password)
async def process_password(message: types.Message, state: FSMContext):
    client = user_clients.get(message.from_user.id)
    try:
        await client.check_password(message.text.strip())
        await message.answer("✅ Авторизовано!", reply_markup=get_main_kb())
        await state.clear()
    except Exception as e: await message.answer(f"Ошибка: {e}")

# --- Функции меню ---

@dp.message(F.text == "📱 Аккаунт")
async def account_info(message: types.Message):
    client = user_clients.get(message.from_user.id)
    if not client or not client.is_connected: return await message.answer("Вы не авторизованы.", reply_markup=get_auth_kb())
    me = await client.get_me()
    await message.answer(f"👤 Аккаунт: {me.first_name}\nID: `{me.id}`\nРазработчики: {DEVELOPERS}", parse_mode="Markdown", 
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚪 Выход", callback_data="logout")]]))

@dp.callback_query(F.data == "logout")
async def logout(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if uid in user_clients:
        try: await user_clients[uid].log_out()
        except: pass
        del user_clients[uid]
    await callback.message.answer("Вышли.", reply_markup=get_auth_kb())
    await callback.answer()

@dp.message(F.text == "👻 Призрак")
async def ghost_menu(message: types.Message):
    await message.answer("Управление призрачным режимом:", reply_markup=get_ghost_kb(message.from_user.id))

@dp.callback_query(F.data.startswith("ghost_"))
async def ghost_toggle(callback: types.CallbackQuery):
    enabled = 1 if callback.data == "ghost_on" else 0
    set_ghost_mode(callback.from_user.id, enabled)
    await callback.message.edit_reply_markup(reply_markup=get_ghost_kb(callback.from_user.id))
    await callback.answer("Статус изменен")

@dp.message(F.text == "🧹 Очистка")
async def clear_start(message: types.Message, state: FSMContext):
    await message.answer("Введите ID/username чата для очистки (удалит последние 100 ваших сообщений):")
    await state.set_state(ActionStates.waiting_for_clear_target)

@dp.message(ActionStates.waiting_for_clear_target)
async def clear_process(message: types.Message, state: FSMContext):
    client = user_clients.get(message.from_user.id)
    if not client: return await message.answer("Авторизуйтесь!")
    try:
        chat = message.text.strip()
        messages = []
        async for msg in client.get_chat_history(chat, limit=100):
            if msg.from_user and msg.from_user.is_self: messages.append(msg.id)
        if messages:
            await client.delete_messages(chat, messages)
            await message.answer(f"✅ Удалено {len(messages)} сообщений.")
        else: await message.answer("Ваших сообщений не найдено.")
    except Exception as e: await message.answer(f"Ошибка: {e}")
    await state.clear()

@dp.message(F.text == "🔄 Перезапуск")
async def restart(message: types.Message):
    await message.answer("🔄 Перезапуск...")
    os.execv(sys.executable, ['python3'] + sys.argv)

# --- Универсальный обработчик для текста и эмодзи ---
@dp.message(F.text | F.sticker)
async def handle_all(message: types.Message, state: FSMContext):
    curr = await state.get_state()
    client = user_clients.get(message.from_user.id)
    
    if curr == ActionStates.waiting_for_msg_target:
        await state.update_data(target=message.text.strip())
        await message.answer("Введите текст:")
        await state.set_state(ActionStates.waiting_for_msg_text)
    elif curr == ActionStates.waiting_for_msg_text:
        if not client: return
        await client.send_message((await state.get_data())['target'], message.text)
        await message.answer("✅ Отправлено")
        await state.clear()
    elif curr == ActionStates.waiting_for_sticker_target:
        await state.update_data(target=message.text.strip())
        await message.answer("Отправьте стикер:")
    elif message.sticker and curr is None: # Если просто прислали стикер без команды
        pass 
    elif message.sticker: # Если ждали стикер
        data = await state.get_data()
        if 'target' in data and client:
            await client.send_sticker(data['target'], message.sticker.file_id)
            await message.answer("✅ Стикер отправлен")
            await state.clear()
    
    # Обработка кнопок меню если нет активного состояния
    if curr is None:
        if message.text == "✉️ Сообщение":
            await message.answer("Введите ID получателя:")
            await state.set_state(ActionStates.waiting_for_msg_target)
        elif message.text == "🎭 Стикеров":
            await message.answer("Введите ID получателя:")
            await state.set_state(ActionStates.waiting_for_sticker_target)
        elif message.text == "😀 Эмодзи":
            await message.answer("Введите ID получателя:")
            await state.set_state(ActionStates.waiting_for_emoji_target)
        elif curr == ActionStates.waiting_for_emoji_target:
             # Логика эмодзи аналогична сообщению
             pass

async def main():
    if not bot: return
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
