import asyncio
import datetime
import json
import re
import aiohttp
import pandas as pd
from io import BytesIO

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, Text
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram import F
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bs4 import BeautifulSoup

# ==================== НАСТРОЙКИ ====================
TOKEN = "7605214341:AAFHG0AyEGLnDcjPFqTOjzAWZZ3Z7s7EsqA"  # замени на свой
URL_PAGE = "https://sh40-cherepovec-r19.gosweb.gosuslugi.ru/roditelyam-i-uchenikam/izmeneniya-v-raspisanii/"

# файлы с данными
SUBS_FILE = "subscribers.json"
KNOWN_FILE = "known_schedules.json"

# ==================== КЛАВИАТУРЫ ====================
main_menu = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="Расписание на завтра")],
    [KeyboardButton(text="Подписаться на уведомления")],
    [KeyboardButton(text="Отписаться")]
])

parallels = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"]
letters = "АБВГДЕ"

def get_parallels_kb(prefix: str):
    kb = InlineKeyboardMarkup(row_width=4)
    for p in parallels:
        kb.insert(InlineKeyboardButton(p, callback_data=f"{prefix}_par:{p}"))
    return kb

def get_letters_kb(parallel: str, prefix: str):
    kb = InlineKeyboardMarkup(row_width=3)
    for l in letters:
        kb.insert(InlineKeyboardButton(f"{parallel}{l}", callback_data=f"{prefix}_cls:{parallel}{l}"))
    return kb

# ==================== РАБОТА С ФАЙЛАМИ ====================
def load_json(file):
    try:
        with open(file, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_json(file, data):
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

subscribers = load_json(SUBS_FILE)          # {"123456789": "10А"}
known_schedules = load_json(KNOWN_FILE)     # {"2024-10-12": "https://...xls"}

# ==================== ПАРСИНГ САЙТА ====================
async def download_file(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.read()
    return b""

async def get_file_url_for_date(target_date: datetime.date) -> str | None:
    async with aiohttp.ClientSession() as session:
        async with session.get(URL_PAGE) as resp:
            html = await resp.text()
    
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        
        if not (href.endswith(".xls") or href.endswith(".xlsx")):
            continue
            
        if not href.startswith("http"):
            href = "https://sh40-cherepovec-r19.gosweb.gosuslugi.ru" + href if href.startswith("/") else "https://sh40-cherepovec-r19.gosweb.gosuslugi.ru/" + href
        
        # ищем дату в тексте ссылки
        match = re.search(r"(\d{1,2}[.\-\s]\d{1,2}[.\-\s]\d{4})", text.replace(" ", ""))
        if match:
            date_str = match.group(1).replace("-", ".").replace(" ", ".")
            try:
                file_date = datetime.datetime.strptime(date_str, "%d.%m.%Y").date()
                if file_date == target_date:
                    candidates.append(href)
            except:
                pass
    
    return candidates[-1] if candidates else None

async def get_schedule_text(class_name: str, target_date: datetime.date) -> str | None:
    url = await get_file_url_for_date(target_date)
    if not url:
        return "На эту дату изменений пока нет"
    
    content = await download_file(url)
    if not content:
        return None
    
    bio = BytesIO(content)
    bio.name = "schedule.xls"
    
    try:
        df = pd.read_excel(bio, engine="xlrd")
    except:
        try:
            df = pd.read_excel(bio, engine="openpyxl")  # на случай xlsx
        except:
            return None
    
    df = df.dropna(how="all").dropna(how="all", axis=1)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.apply(lambda x: x.str.strip() if x.dtype == "object" else x)
    
    # ищем колонку с классами
    class_col = None
    for col in df.columns:
        if "класс" in str(col).lower():
            class_col = col
            break
    if not class_col:
        return None
    
    # ищем колонки уроков (цифры 1-8)
    lesson_cols = []
    for col in df.columns:
        if str(col) in [str(i) for i in range(1, 11)]:
            lesson_cols.append((int(col), col))
    lesson_cols = [col for _, col in sorted(lesson_cols)]
    
    if not lesson_cols:
        return None
    
    # фильтруем строки с нужным классом
    mask = df[class_col].astype(str).str.upper().str.contains(class_name.upper())
    rows = df[mask]
    
    if rows.empty:
        return f"Для {class_name} класса изменений нет"
    
    text = f"Изменения для <b>{class_name}</b> на <b>{target_date.strftime('%d.%m.%Y')}</b>:\n\n"
    has_changes = False
    
    for col in lesson_cols:
        values = rows[col].dropna()
        if values.empty:
            continue
        value = " | ".join([str(v) for v in values if str(v).strip() not in ["", "-", "н", "нет"]])
        if value:
            text += f"<b>{col}.</b> {value}\n"
            has_changes = True
    
    return text if has_changes else f"Для {class_name} класса изменений нет"

# ==================== АВТО-РАССЫЛКА ====================
async def check_and_send_updates():
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    tomorrow_str = tomorrow.strftime("%Y-%m-%d")
    
    url = await get_file_url_for_date(tomorrow)
    
    if not url:
        return
    
    if known_schedules.get(tomorrow_str) == url:
        return  # уже отправляли этот файл
    
    known_schedules[tomorrow_str] = url
    save_json(KNOWN_FILE, known_schedules)
    
    # рассылаем всем подписчикам
    for chat_id, class_name in subscribers.items():
        try:
            text = await get_schedule_text(class_name, tomorrow)
            if text and "изменений нет" not in text.lower():
                await bot.send_message(int(chat_id), text, parse_mode="HTML")
            else:
                # если ничего или не распарсилось — шлём файл
                content = await download_file(url)
                if content:
                    await bot.send_document(int(chat_id), BufferedInputFile(content, f"изменения_{tomorrow_str}.xls"),
                                          caption=f"Появились изменения на завтра для {class_name}!")
        except:
            pass  # пользователь заблокировал бота

# ==================== БОТ ====================
bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher()

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "Привет! Я бот расписания школы №40 (Череповец)\n\n"
        "• Нажми «Расписание на завтра» — выберешь класс — получишь только своё расписание\n"
        "• Подпишись — и изменения на завтра будут приходить автоматически\n",
        reply_markup=main_menu
    )

@dp.message(Text("Расписание на завтра"))
async def schedule_tomorrow(message: types.Message):
    await message.answer("Выбери параллель:", reply_markup=get_parallels_kb("sched"))

@dp.message(Text("Подписаться на уведомления"))
async def subscribe_start(message: types.Message):
    if str(message.chat.id) in subscribers:
        await message.answer(f"Ты уже подписан на {subscribers[str(message.chat.id)]}\n"
                           "Хочешь сменить класс?", reply_markup=get_parallels_kb("sub"))
    else:
        await message.answer("Выбери класс для автоматических уведомлений:", reply_markup=get_parallels_kb("sub"))

@dp.message(Text("Отписаться"))
async def unsubscribe(message: types.Message):
    chat_id = str(message.chat.id)
    if chat_id in subscribers:
        del subscribers[chat_id]
        save_json(SUBS_FILE, subscribers)
        await message.answer("Отписался от уведомлений")
    else:
        await message.answer("Ты и так не подписан")

@dp.callback_query(F.data.startswith("sched_par:") | F.data.startswith("sub_par:"))
async def process_parallel(callback: types.CallbackQuery):
    prefix = "sched" if callback.data.startswith("sched") else "sub"
    parallel = callback.data.split(":")[1]
    await callback.message.edit_text(f"Теперь выбери букву для {parallel} класса:", 
                                   reply_markup=get_letters_kb(parallel, prefix))

@dp.callback_query(F.data.startswith("sched_cls:") | F.data.startswith("sub_cls:"))
async def process_class(callback: types.CallbackQuery):
    data = callback.data
    class_name = data.split(":")[1]
    chat_id = str(callback.message.chat.id)
    
    if data.startswith("sched"):
        await callback.message.edit_text("Ищу расписание...")
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        text = await get_schedule_text(class_name, tomorrow)
        
        if text and "изменений нет" not in text.lower():
            await callback.message.answer(text)
            # предлагаем подписку
            if subscribers.get(chat_id) != class_name:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("Подписаться на этот класс", callback_data=f"directsub:{class_name}"))
                await callback.message.answer("Хочешь получать это автоматически?", reply_markup=kb)
        else:
            # шлём файл
            url = await get_file_url_for_date(tomorrow)
            if url:
                content = await download_file(url)
                await callback.message.answer_document(
                    BufferedInputFile(content, f"изменения_{tomorrow.strftime('%d.%m.%Y')}.xls"),
                    caption="Не смог распарсить — вот оригинальный файл"
                )
    
    else:  # подписка
        subscribers[chat_id] = class_name
        save_json(SUBS_FILE, subscribers)
        await callback.message.edit_text(f"✅ Ты подписан на класс <b>{class_name}</b>!\n"
                                       "Теперь изменения на завтра будут приходить автоматически.")

@dp.callback_query(F.data.startswith("directsub:"))
async def direct_sub(callback: types.CallbackQuery):
    class_name = callback.data.split(":")[1]
    subscribers[str(callback.message.chat.id)] = class_name
    save_json(SUBS_FILE, subscribers)
    await callback.answer("Подписка оформлена!")
    await callback.message.answer(f"Теперь ты будешь получать расписание {class_name} автоматически")

# ==================== ЗАПУСК ====================
async def main():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_and_send_updates, "interval", minutes=30)  # проверка каждые 30 минут
    scheduler.start()
    
    global subscribers, known_schedules
    subscribers = load_json(SUBS_FILE)
    known_schedules = load_json(KNOWN_FILE)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
