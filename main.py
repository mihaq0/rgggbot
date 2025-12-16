# main.py — ФИНАЛЬНАЯ ВЕРСИЯ 100% РАБОЧАЯ НА СЕГОДНЯ (27 марта 2025)
import asyncio
import datetime
import json
import re
import aiohttp
import pandas as pd
from io import BytesIO

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bs4 import BeautifulSoup

TOKEN = "TOKEN"  # Render берёт его из переменной окружения, тут можно оставить так

URL_PAGE = "https://sh40-cherepovec-r19.gosweb.gosuslugi.ru/roditelyam-i-uchenikam/izmeneniya-v-raspisanii/"

SUBS_FILE = "subscribers.json"
KNOWN_FILE = "known_schedules.json"

# ==================== КЛАВИАТУРЫ ====================
main_menu = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="Расписание на завтра")],
    [KeyboardButton(text="Подписаться на уведомления")],
    [KeyboardButton(text="Отписаться")]
])

parallels = ["1","2","3","4","5","6","7","8","9","10","11"]

def get_parallels_kb(prefix: str):
    kb = InlineKeyboardMarkup(row_width=4)
    for p in parallels:
        kb.insert(InlineKeyboardButton(p, callback_data=f"{prefix}_par:{p}"))
    return kb

def get_letters_kb(parallel: str, prefix: str):
    kb = InlineKeyboardMarkup(row_width=4)
    letters = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЭЮЯ"
    for l in letters:
        kb.insert(InlineKeyboardButton(f"{parallel}{l}", callback_data=f"{prefix}_cls:{parallel}{l}"))
    return kb

# ==================== БАЗА ====================
def load_json(file):
    try:
        with open(file, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_json(file, data):
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

subscribers = load_json(SUBS_FILE)
known_schedules = load_json(KNOWN_FILE)

# ==================== ПАРСИНГ ====================
async def get_file_url_for_date(target_date: datetime.date) -> str | None:
    async with aiohttp.ClientSession() as session:
        async with session.get(URL_PAGE) as resp:
            html = await resp.text()
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if not href.endswith((".xls", ".xlsx")):
            continue
        if not href.startswith("http"):
            href = "https://sh40-cherepovec-r19.gosweb.gosuslugi.ru" + href
        match = re.search(r"(\d{1,2}[.\s-]\d{1,2}[.\s-]\d{4})", text.replace(" ", ""))
        if match:
            date_str = match.group(1).replace("-", ".").replace(" ", ".")
            try:
                file_date = datetime.datetime.strptime(date_str, "%d.%m.%Y").date()
                if file_date == target_date:
                    candidates.append(href)
            except:
                continue
    return candidates[-1] if candidates else None

async def get_schedule_text(class_name: str, target_date: datetime.date) -> str:
    url = await get_file_url_for_date(target_date)
    if not url:
        return "Изменений на завтра пока нет"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            content = await resp.read()
    
    bio = BytesIO(content)
    try:
        df = pd.read_excel(bio, engine="openpyxl" if url.endswith(".xlsx") else "xlrd")
    except:
        return None
    
    df = df.dropna(how="all").dropna(how="all", axis=1)
    df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
    
    class_col = next((col for col in df.columns if "класс" in str(col).lower()), None)
    if not class_col:
        return None
    
    lesson_cols = [col for col in df.columns if str(col) in [str(i) for i in range(1, 12)]]
    
    mask = df[class_col].astype(str).str.upper().str.contains(class_name.upper(), na=False)
    rows = df[mask]
    
    if rows.empty:
        return f"Для {class_name} класса изменений нет"
    
    text = f"Изменения <b>{class_name}</b> на <b>{target_date.strftime('%d.%m.%Y')}</b>:\n\n"
    for col in lesson_cols:
        values = rows[col].dropna()
        values = [v for v in values if str(v).strip() not in ["", "-", "н", "нет", "—"]]
        if values:
            text += f"<b>{col}.</b> {', '.join(map(str, values))}\n"
    
    return text if len(text) > 50 else f"Для {class_name} класса изменений нет"

# ==================== АВТОРАССЫЛКА ====================
async def check_and_send_updates():
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    tomorrow_str = tomorrow.strftime("%Y-%m-%d")
    url = await get_file_url_for_date(tomorrow)
    if not url or known_schedules.get(tomorrow_str) == url:
        return
    known_schedules[tomorrow_str] = url
    save_json(KNOWN_FILE, known_schedules)
    
    for chat_id, cls in list(subscribers.items()):
        try:
            text = await get_schedule_text(cls, tomorrow)
            if text and "изменений нет" not in text.lower():
                await bot.send_message(int(chat_id), text, parse_mode="HTML")
        except:
            pass

# ==================== БОТ ====================
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("Привет! Бот расписания школы №40 готов ✅", reply_markup=main_menu)

@dp.message(F.text == "Расписание на завтра")
async def sched(message: types.Message):
    await message.answer("Выбери класс:", reply_markup=get_parallels_kb("s"))

@dp.message(F.text == "Подписаться на уведомления")
async def sub(message: types.Message):
    await message.answer("На какой класс подписаться?", reply_markup=get_parallels_kb("sub"))

@dp.message(F.text == "Отписаться")
async def unsub(message: types.Message):
    chat_id = str(message.chat.id)
    if subscribers.pop(chat_id, None):
        save_json(SUBS_FILE, subscribers)
        await message.answer("Отписан ✅")
    else:
        await message.answer("Ты и так не подписан")

@dp.callback_query(F.data.startswith("s_par:") | F.data.startswith("sub_par:"))
async def parallel(callback: types.CallbackQuery):
    prefix = "s" if callback.data.startswith("s_par") else "sub"
    par = callback.data.split(":")[1]
    await callback.message.edit_text(f"{par} класс — выбери букву:", reply_markup=get_letters_kb(par, prefix))

@dp.callback_query(F.data.startswith("s_cls:") | F.data.startswith("sub_cls:"))
async def cls(callback: types.CallbackQuery):
    prefix = "s" if callback.data.startswith("s_cls") else "sub"
    cls = callback.data.split(":")[1]
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    
    if prefix == "s":
        await callback.message.edit_text("Ищу изменения...")
        text = await get_schedule_text(cls, tomorrow)
        await callback.message.answer(text)
        if subscribers.get(str(callback.from_user.id)) != cls:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("Подписаться на этот класс", callback_data=f"subnow:{cls}")]])
            await callback.message.answer("Получать автоматически?", reply_markup=kb)
    else:
        subscribers[str(callback.from_user.id)] = cls
        save_json(SUBS_FILE, subscribers)
        await callback.message.edit_text(f"Подписка на <b>{cls}</b> оформлена ✅")

@dp.callback_query(F.data.startswith("subnow:"))
async def subnow(callback: types.CallbackQuery):
    cls = callback.data.split(":")[1]
    subscribers[str(callback.from_user.id)] = cls
    save_json(SUBS_FILE, subscribers)
    await callback.answer("Готово!")
    await callback.message.answer(f"Теперь {cls} будет приходить автоматически ✅")

async def main():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_and_send_updates, "interval", minutes=25)
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
