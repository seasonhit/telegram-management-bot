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
    cursor.execute('CREATE TABLE IF NOT EXISTS user_api (user_id INTEGER PRIMARY KEY, api_id INTEGER, api_hash TEXT)')
    conn.commit()
    conn.close()

init_db()

def get_user_api(user_id):
    conn = sqlite3.connect(os.path.join(WORK_DIR, 'bot_data.db'))
    cursor = conn.cursor()
    cursor.execute("SELECT api_id, api_hash FROM user_api WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res if res else (None, None)

def save_user_api(user_id, api_id, api_hash):
    conn = sqlite3.connect(os.path.join(WORK_DIR, 'bot_data.db'))
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO user_api (user_id, api_id, api_hash) VALUES (?, ?, ?)", (user_id, api_id, api_hash))
    conn.commit()
    conn.close()

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
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()
    waiting_for_code_type = State()

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

def get_code_type_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 SMS", callback_data="code_sms")],
        [InlineKeyboardButton(text="☎️ Вызов", callback_data="code_call")],
        [InlineKeyboardButton(text="💬 Telegram App", callback_data="code_app")],
        [InlineKeyboardButton(text="↻ Повторная отправка", callback_data="resend_code")]
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
    user_id = callback.from_user.id
    api_id, api_hash = get_user_api(user_id)
    
    if api_id and api_hash:
        # Используем сохраненные значения
        await state.update_data(api_id=str(api_id), api_hash=api_hash)
        await callback.message.answer("Введите номер телефона (+7...):")
        await state.set_state(AuthStates.waiting_for_phone)
    else:
        # Запрашиваем API ID и Hash
        await callback.message.answer(
            "Вставьте API ID и API Hash из http://my.telegram.org в формате:\n"
            "API_ID API_HASH\n\n"
            "Например:\n12345678 abc123def456..."
        )
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
    parts = message.text.strip().split()
    if len(parts) < 2:
        return await message.answer(
            "❌ Неправильный формат. Введите в одной строке:\n"
            "API_ID API_HASH"
        )
    
    api_id_str, api_hash = parts[0], parts[1]
    if not api_id_str.isdigit():
        return await message.answer("❌ API ID должен быть числом. Попробуйте еще раз:")
    
    user_id = message.from_user.id
    api_id = int(api_id_str)
    save_user_api(user_id, api_id, api_hash)
    logger.info(f"[User {user_id}] Сохранены API ID: {api_id}, API Hash: {api_hash[:10]}...")
    
    await state.update_data(api_id=api_id_str, api_hash=api_hash)
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
    
    try:
        api_id = int(data['api_id'])
    except (ValueError, KeyError):
        await message.answer("❌ Ошибка: неверный API ID. Начните заново: /start")
        await state.clear()
        return
    
    client = Client(
        name=f"session_{user_id}",
        api_id=api_id,
        api_hash=data['api_hash'],
        phone_number=phone,
        workdir=WORK_DIR,
        in_memory=False
    )
    
    try:
        logger.info(f"[{phone}] Подключение к Telegram...")
        await client.connect()
        logger.info(f"[{phone}] ✅ Подключено. Отправляю код...")
        await message.answer(f"⏳ Подключено. Отправляю код на {phone}...")
        
        # send_code отправляет код на номер телефона
        logger.info(f"[{phone}] Вызов send_code()...")
        sent_code = await client.send_code(phone)
        code_type = getattr(sent_code, 'type', None)
        type_name = getattr(code_type, 'name', str(code_type)) if code_type else 'UNKNOWN'
        logger.info(f"[{phone}] ✅ send_code() успешен!")
        logger.info(f"[{phone}] Тип кода: {type_name}, Hash: {sent_code.phone_code_hash[:15]}...")

        # Сохраняем данные
        user_clients[user_id] = client
        phone_code_hash = sent_code.phone_code_hash
        logger.info(f"[{user_id}] Сохранены данные: phone={phone}, hash_len={len(phone_code_hash)}")
        
        await state.update_data(
            phone=phone,
            phone_code_hash=phone_code_hash,
            code_type=type_name
        )

        dest_text = "SMS" if type_name and "SMS" in type_name.upper() else "в приложении Telegram" if type_name and "APP" in type_name.upper() else "по номеру"
        await message.answer(
            f"✅ Код отправлен на {phone}\n"
            f"Доставка: {dest_text}\n\n"
            f"📱 Введите полученный код (4-10 символов):",
            reply_markup=get_code_type_kb()
        )
        await state.set_state(AuthStates.waiting_for_code)
    except Exception as e:
        err_name = type(e).__name__
        err_msg = str(e)
        logger.error(f"[{phone}] ❌ {err_name}: {err_msg}")
        logger.error(f"[{phone}] Полная ошибка: {repr(e)}")
        try:
            await client.disconnect()
        except:
            pass
        
        # Определяем помощь в зависимости от ошибки
        help_text = ""
        if "auth" in err_msg.lower() or "unauthorized" in err_msg.lower():
            help_text = "Проверьте API ID и API Hash на http://my.telegram.org"
        elif "phone" in err_msg.lower():
            help_text = "Проверьте формат номера: +7XXXXXXXXXX (11 цифр)"
        elif "flood" in err_msg.lower():
            help_text = "Слишком много попыток. Подождите 1-2 часа."
        else:
            help_text = "Проверьте интернет и пересчитайте API учетные данные"
        
        await message.answer(
            f"❌ Ошибка отправки кода\n"
            f"Тип: {err_name}\n\n"
            f"Сообщение: {err_msg[:100]}\n\n"
            f"💡 {help_text}\n\n"
            f"Начните заново: /start"
        )
        await state.clear()

@dp.message(AuthStates.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    data = await state.get_data()
    client = user_clients.get(message.from_user.id)
    if not client or not client.is_connected: 
        logger.error(f"[User {message.from_user.id}] Клиент не подключен при вводе кода")
        return await message.answer("❌ Ошибка: сессия потеряна. Начните заново: /start")
    
    import re
    raw_input = message.text.strip()
    code = raw_input.replace(" ", "").replace("-", "").replace("(", "").replace(")", "").replace("/", "")
    logger.info(f"[{message.from_user.id}] Ввод кода: raw='{raw_input}' → cleaned='{code}'")
    
    # Код может быть 4-10 символов (цифры/буквы)
    if not re.match(r'^[A-Za-z0-9]{4,10}$', code):
        logger.warning(f"[{message.from_user.id}] ❌ Код не прошел валидацию: '{code}' (4-10 буквенно-цифровых)")
        return await message.answer(
            "❌ Неверный формат кода.\n"
            "Код должен содержать 4–10 букв и/или цифр.\n\n"
            f"📝 Вы ввели: '{code}' ({len(code)} символов)\n\n"
            "Попробуйте еще раз:"
        )
    
    try:
        phone = data.get('phone', 'unknown')
        phone_code_hash = data.get('phone_code_hash', '')
        
        if not phone or not phone_code_hash:
            logger.error(f"[{message.from_user.id}] ❌ Критические данные не сохранены!")
            logger.error(f"[{message.from_user.id}] phone={phone}, phone_code_hash={'SET' if phone_code_hash else 'NOT_SET'}")
            return await message.answer("❌ Ошибка сессии: потеряны данные авторизации. /start")
        
        logger.info(f"[{message.from_user.id}] ========== ПОПЫТКА ВХОДА ==========")
        logger.info(f"[{message.from_user.id}] Телефон: {phone}")
        logger.info(f"[{message.from_user.id}] Hash длина: {len(phone_code_hash)}, первые 15 символов: {phone_code_hash[:15]}")
        logger.info(f"[{message.from_user.id}] Код (очищенный): {code} (длина={len(code)})")
        logger.info(f"[{message.from_user.id}] Клиент подключен: {client.is_connected}")
        logger.info(f"[{message.from_user.id}] =====================================")
        
        result = await client.sign_in(
            phone_number=phone,
            phone_code_hash=phone_code_hash,
            phone_code=code
        )
        logger.info(f"[{message.from_user.id}] ✅✅✅ ВХОД УСПЕШЕН! ✅✅✅")
        logger.info(f"[{message.from_user.id}] Тип результата: {type(result).__name__}")
        logger.info(f"[{message.from_user.id}] Пользователь: {result.first_name} {result.last_name or ''}")

        await message.answer(
            "✅ Вы успешно авторизованы!\n"
            "Теперь можете использовать все функции бота.",
            reply_markup=get_main_kb()
        )
        await state.clear()
    except errors.SessionPasswordNeeded:
        logger.info(f"[User {message.from_user.id}] Требуется 2FA пароль")
        await message.answer(
            "🔐 На аккаунте включена двухфакторная аутентификация.\n"
            "Введите ваш пароль:"
        )
        await state.set_state(AuthStates.waiting_for_password)
    except errors.PhoneNumberInvalid:
        logger.error(f"Неверный номер телефона: {data.get('phone')}")
        await message.answer("❌ Неверный номер телефона. Начните заново: /start")
        await state.clear()
    except errors.PhoneCodeInvalid as e:
        logger.warning(f"[{message.from_user.id}] ========== ОШИБКА: НЕВЕРНЫЙ КОД ==========")
        logger.warning(f"[{message.from_user.id}] Введенный код: '{code}'")
        logger.warning(f"[{message.from_user.id}] Длина: {len(code)}")
        logger.warning(f"[{message.from_user.id}] Телефон: {data.get('phone')}")
        logger.warning(f"[{message.from_user.id}] Исходная ошибка: {e}")
        logger.warning(f"[{message.from_user.id}] ======================================")
        attempts = (await state.get_data()).get('attempts', 0) + 1
        await state.update_data(attempts=attempts)
        if attempts >= 3:
            await message.answer(
                "❌ 3 неверных кода подряд.\n"
                "Запросите новый код: /start"
            )
            await state.clear()
        else:
            remaining = 3 - attempts
            await message.answer(
                f"❌ Неверный код ({attempts}/3)\n"
                f"Осталось: {remaining} попыток\n\n"
                f"📝 Ввод был: '{code}'\n"
                f"Убедитесь что это полный код из сообщения Telegram.\n"
                f"Попробуйте еще раз:"
            )
    except errors.CodeExpired:
        logger.warning(f"[User {message.from_user.id}] Код истёк")
        await message.answer(
            "❌ Код истёк.\n"
            "Нажмите кнопку '↻ Повторная отправка' для получения нового кода."
        )
    except Exception as e:
        err_name = type(e).__name__
        err_msg = str(e)
        logger.error(f"[{message.from_user.id}] {err_name} при sign_in: {err_msg}")
        logger.error(f"[{message.from_user.id}] Полная ошибка: {repr(e)}")
        await message.answer(
            f"❌ Ошибка: {err_name}\n\n"
            f"Детали: {err_msg[:120]}\n\n"
            f"Попробуйте: /start"
        )

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
        await callback.message.edit_text(
            f"✅ Код повторно отправлен на {phone} ({dest_text}).\n📱 Выберите способ получения кода или введите его:",
            reply_markup=get_code_type_kb()
        )
        await callback.answer()
    except errors.FloodWait as e:
        logger.warning(f"FloodWait при повторной отправке кода для {uid}: {e.seconds}s")
        await callback.answer(f"⏳ Частые попытки. Попробуйте через {e.seconds} секунд", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка при повторной отправке кода для {uid}: {type(e).__name__}: {e}")
        await callback.answer(f"❌ Не удалось отправить код повторно: {type(e).__name__}", show_alert=True)


@dp.callback_query(F.data.in_(["code_sms", "code_call", "code_app"]))
async def handle_code_type_selection(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    data = await state.get_data()
    client = user_clients.get(uid)
    if not client or not client.is_connected:
        await callback.answer("❌ Клиент не подключен. Начните заново: /start", show_alert=True)
        return
    
    phone = data.get('phone')
    phone_code_hash = data.get('phone_code_hash')
    if not phone or not phone_code_hash:
        await callback.answer("❌ Нет данных для выбора типа кода.", show_alert=True)
        return
    
    try:
        logger.info(f"[{uid}] Запрос повторной отправки кода")
        sent = await client.resend_code(phone, phone_code_hash)
        code_type = getattr(sent.type, 'name', str(sent.type))
        await state.update_data(phone_code_hash=sent.phone_code_hash, code_type=code_type)
        
        type_text = {
            "SMS": "📱 SMS",
            "CALL": "☎️ Вызов",
            "APP": "💬 Telegram App"
        }.get(code_type, code_type)
        
        await callback.message.edit_text(
            f"✅ Способ получения: {type_text}\n📱 Введите код:",
            reply_markup=get_code_type_kb()
        )
        await callback.answer(f"Выбран способ: {type_text}")
        logger.info(f"[{uid}] Код переотправлен способом: {type_text}")
    except errors.FloodWait as e:
        logger.warning(f"FloodWait для {uid}: {e.seconds}s")
        await callback.answer(f"⏳ Частые попытки. Попробуйте через {e.seconds} секунд", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка выбора типа кода для {uid}: {type(e).__name__}: {str(e)[:100]}")
        await callback.answer(f"❌ Ошибка: {type(e).__name__}\n{str(e)[:80]}", show_alert=True)

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
