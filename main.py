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
WORK_DIR = os.getenv("WORK_DIR", "/workspaces/telegram-management-bot/.bot_data/")
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
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔐 Начать авторизацию", callback_data="start_auth")],
        [InlineKeyboardButton(text="Получить токен (my.telegram.org)", url="http://my.telegram.org")]
    ])

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
    user_name = message.from_user.first_name or "Друже"
    await message.answer(
        f"👋 Добро пожаловать, {user_name}!\nВыберите функцию из меню ниже.\n\nЕсли нужен токен или доступ к API — используйте команду /token или кнопку 'Получить токен (my.telegram.org)'.",
        reply_markup=get_main_kb()
    )
    logger.info(f"Пользователь {message.from_user.id} ({user_name}) запустил бота")

@dp.callback_query(F.data == "start_auth")
async def start_auth(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Введите API ID:")
    await state.set_state(AuthStates.waiting_for_api_id)
    await callback.answer()


@dp.message(Command("token"))
async def cmd_token(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть my.telegram.org", url="http://my.telegram.org")]
    ])
    text = (
        "Откройте http://my.telegram.org для доступа к API ID и API Hash.\n"
        "Для получения Bot Token создайте бота на my.telegram.org и/или используйте @BotFather в Telegram, выполнив /newbot.\n"
        "Инструкция: https://core.telegram.org/bots#3-how-do-i-create-a-bot"
    )
    await message.answer(text, reply_markup=kb)

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
    phone = message.text.strip()
    if not phone.startswith('+'): phone = '+' + phone
    user_id = message.from_user.id
    
    # Очистка старой сессии
    session_path = os.path.join(WORK_DIR, f"session_{user_id}.session")
    if os.path.exists(session_path): os.remove(session_path)
    
    client = Client(
        name=f"session_{user_id}",
        api_id=int(data['api_id']),
        api_hash=data['api_hash'],
        phone_number=phone,
        workdir=WORK_DIR,
        in_memory=False
    )
    
    try:
        logger.info(f"[{phone}] Подключение...")
        await client.connect()
        logger.info(f"[{phone}] Отправка кода...")
        await message.answer(f"⏳ Отправляю код на {phone}...")
        
        # send_code возвращает SentCode с phone_code_hash и type (SMS/APP)
        sent_code = await client.send_code(phone)
        code_type = getattr(sent_code.type, 'name', str(sent_code.type))
        logger.info(f"[{phone}] Код отправлен. Hash: {sent_code.phone_code_hash[:10]}..., type={code_type}")

        # Сохраняем данные
        user_clients[user_id] = client
        await state.update_data(
            phone=phone,
            phone_code_hash=sent_code.phone_code_hash,
            code_type=code_type
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отправить код повторно", callback_data="resend_code")]])
        dest_text = "SMS" if code_type and "SMS" in code_type.upper() else "уведомление в Telegram"
        await message.answer(
            f"✅ Код отправлен на {phone} ({dest_text}).\n📱 Введите код из {dest_text}:",
            reply_markup=kb
        )
        await state.set_state(AuthStates.waiting_for_code)
    except Exception as e:
        logger.error(f"[{phone}] Ошибка send_code: {type(e).__name__}: {str(e)[:100]}")
        try:
            await client.disconnect()
        except:
            pass
        await message.answer(
            f"❌ Ошибка подключения:\n{str(e)[:80]}\n\n" +
            f"Проверьте:\n" +
            f"• API ID (на my.telegram.org)\n" +
            f"• API Hash (на my.telegram.org)\n" +
            f"• Номер телефона (формат: +7ХХХХХХХХХХ)"
        )
        await state.clear()

@dp.message(AuthStates.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    data = await state.get_data()
    client = user_clients.get(message.from_user.id)
    if not client or not client.is_connected: 
        logger.error(f"[User {message.from_user.id}] Клиент не подключен")
        return await message.answer("❌ Ошибка: Клиент отключен. Нажмите /start")
    
    import re
    code = message.text.strip().replace(" ", "").replace("-", "")
    if not re.match(r'^[A-Za-z0-9]{4,10}$', code):
        return await message.answer("❌ Неверный формат кода. Код должен содержать 4–10 букв/цифр. Попробуйте ещё раз:")
    
    try:
        logger.info(f"[User {message.from_user.id}] Проверка кода: {code}")
        result = await client.sign_in(phone_number=data['phone'], phone_code_hash=data['phone_code_hash'], phone_code=code)
        logger.info(f"[User {message.from_user.id}] ✅ ВХОД УСПЕШЕН! Тип: {type(result).__name__}")

        await message.answer(
            "✅ Вы успешно авторизованы!\n" +
            "Теперь можете использовать все функции бота.",
            reply_markup=get_main_kb()
        )
        await state.clear()
    except errors.SessionPasswordNeeded:
        logger.info(f"[User {message.from_user.id}] 2FA требуется")
        await message.answer("🔐 На аккаунте включена 2-фактор аутентификация.\nВведите пароль:")
        await state.set_state(AuthStates.waiting_for_password)
    except errors.PhoneNumberInvalid:
        logger.error(f"Неверный номер телефона: {data.get('phone')}")
        await message.answer("❌ Неверный номер телефона. Попробуйте снова с /start")
        await state.clear()
    except errors.PhoneCodeInvalid:
        logger.warning(f"[User {message.from_user.id}] ❌ Неверный код")
        attempts = (await state.get_data()).get('attempts', 0) + 1
        await state.update_data(attempts=attempts)
        if attempts >= 3:
            await message.answer("❌ Слишком много попыток. Начните заново: /start")
            await state.clear()
        else:
            await message.answer(f"❌ Неверный код ({attempts}/3).\nПопробуйте еще раз:")
    except errors.CodeExpired:
        await message.answer("❌ Код истёк. Нажмите кнопку 'Отправить код повторно' или запустите /start")
    except Exception as e:
        logger.error(f"[User {message.from_user.id}] ❌ Ошибка sign_in: {type(e).__name__}: {str(e)[:200]}")
        await message.answer(f"❌ Ошибка авторизации:\n{str(e)[:200]}")

@dp.message(AuthStates.waiting_for_password)
async def process_password(message: types.Message, state: FSMContext):
    client = user_clients.get(message.from_user.id)
    if not client or not client.is_connected:
        logger.error("Клиент не подключен при проверке пароля")
        return await message.answer("❌ Ошибка сессии. /start")
    
    try:
        result = await client.check_password(message.text.strip())
        logger.info(f"Пользователь {message.from_user.id} прошел 2FA. Результат: {type(result).__name__}")
        await message.answer("✅ Авторизовано успешно! 2FA пройдена.", reply_markup=get_main_kb())
        await state.clear()
    except errors.PasswordHashInvalid:
        logger.warning(f"Неверный пароль для {message.from_user.id}")
        await message.answer("❌ Неверный пароль 2FA. Попробуйте еще раз:")
    except errors.PasswordEmpty:
        logger.warning(f"Пароль не установлен для {message.from_user.id}")
        await message.answer("❌ На аккаунте не установлен пароль 2FA, но требуется. Попробуйте с начала.")
        await state.clear()
    except Exception as e: 
        logger.error(f"Ошибка 2FA: {type(e).__name__}: {e}")
        await message.answer(f"❌ Ошибка: {str(e)[:100]}")
        await state.clear()

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


@dp.callback_query(F.data == "resend_code")
async def handle_resend_code(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    data = await state.get_data()
    client = user_clients.get(uid)
    if not client or not client.is_connected:
        await callback.answer("❌ Клиент не подключен. Начните заново: /start", show_alert=True)
        return
    phone = data.get('phone')
    phone_code_hash = data.get('phone_code_hash')
    if not phone or not phone_code_hash:
        await callback.answer("❌ Нет данных для повторной отправки. Начните заново: /start", show_alert=True)
        return
    try:
        sent = await client.resend_code(phone, phone_code_hash)
        code_type = getattr(sent.type, 'name', str(sent.type))
        await state.update_data(phone_code_hash=sent.phone_code_hash, code_type=code_type)
        dest_text = "SMS" if code_type and "SMS" in code_type.upper() else "уведомление в Telegram"
        await callback.message.answer(f"✅ Код повторно отправлен на {phone} ({dest_text})")
        await callback.answer()
    except errors.FloodWait as e:
        logger.warning(f"FloodWait при повторной отправке кода для {uid}: {e.seconds}s")
        await callback.answer(f"⏳ Частые попытки. Попробуйте через {e.seconds} секунд", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка при повторной отправке кода для {uid}: {type(e).__name__}: {e}")
        await callback.answer(f"❌ Не удалось отправить код повторно: {type(e).__name__}", show_alert=True)

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
    logger.warning(f"Запрос перезапуска от пользователя {message.from_user.id}")
    await message.answer("🔄 Бот перезапускается...")
    await asyncio.sleep(0.5)
    logger.info("🔄 БОТ ПЕРЕЗАПУЩЕН")
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
