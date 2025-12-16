# main.py
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
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bs4 import BeautifulSoup

# ==================== НАСТРОЙКИ ====================
TOKEN = "7605214341:AAFHG0AyEGLnDcjPFqTOjzAWZZ3Z7s7EsqA"  # ←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←
URL_PAGE = "https://sh40-cherepovec-r19.gosweb.gosuslugi.ru/roditelyam-i-uchenikam/izmeneniya-v-raspisanii/"

SUBS_FILE = "subscribers.json"
KNOWN_FILE = "known_schedules.json"

# ==================== КЛАВИАТУРЫ ====================
main_menu = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="Расписание на завтра")],
    [KeyboardButton(text="Подписаться на уведомления")],
    [KeyboardButton(text="Отписаться")]
])

parallels = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"]
letters = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЭЮЯ"

def get_parallels_kb(prefix: str):
    kb = InlineKeyboardMarkup(row_width=4)
    for p in parallels:
        kb.insert(InlineKeyboardButton(p, callback_data=f"{prefix}_par:{p}"))
    return kb

def get_letters_kb(parallel: str, prefix: str):
    kb = InlineKeyboardMarkup(row_width=4)
    for l in letters:
        kb.insert(InlineKeyboardButton(f"{parallel}{l}", callback_data=f"{prefix}_cls:{parallel}{l}"))
    return kb

# ==================== БАЗА ДАННЫХ ====================
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
        
        if not href.endswith((".xls", ".xlsx")):
            continue
            
        if not href.startswith("http"):
            href = "https://sh40-cherepovec-r19.gosweb.gosuslugi.ru" + href
        
        match = re.search(r"(\d{1,2}[.\s-]\d{1,2}[.\s-]\d{2,4})", text.replace(" ", ""))
        if match:
            date_str = match.group(1).replace("-", ".").replace(" ", ".")
            try:
                file_date = datetime.datetime.strptime(date_str.split(".")[-3:], "%y.%m.%d").date().replace(year=datetime.datetime.now().year + (100 if datetime.datetime.now().year % 100 > int(date_str.split(".")[-1]) else 0))
            except:
                try:
                    file_date = datetime.datetime.strptime(date_str, "%d.%m.%Y").date()
                except:
                    continue
            if file_date == target_date:
                candidates.append(href)
    
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
            df = pd.read_excel(bio, engine="openpyxl")
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
    
    text = f"Изменения в расписании <b>{class_name}</b> на <b>{target_date.strftime('%d.%m.%Y')}</b>:\n\n"
    has_changes = False
    
    for col in lesson_cols:
        values = rows[col].dropna()
        values = [v for v in values if str(v).strip() not in ["", "-", "н", "нет", "—"]]
        if values:
            text += f"<b>{col}.</b> {', '.join(map(str, values))}\n"
            has_changes = True
    
    return text if has_changes else f"Для {class_name} класса изменений нет"

# ==================== АВТО-РАССЫЛКА ====================
async def check_and_send_updates():
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    tomorrow_str = tomorrow.strftime("%Y-%m-%d")
    
    url = await get_file_url_for_date(tomorrow)
    if not url or known_schedules.get(tomorrow_str) == url:
        return
    
    known_schedules[tomorrow_str] = url
    save_json(KNOWN_FILE, known_schedules)
    
    for chat_id, class_name in list(subscribers.items()):
        try:
            text = await get_schedule_text(class_name, tomorrow)
            if text and "изменений нет" not in text.lower():
                await bot.send_message(int(chat_id), text, parse_mode="HTML")
            else:
                content = await download_file(url)
                if content:
                    await bot.send_document(int(chat_id), BufferedInputFile(content, f"изменения_{tomorrow_str}.xls"),
                                          caption=f"Появились изменения на завтра!")
        except:
            pass

# ==================== БОТ ====================
bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher()

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "Привет! Я бот расписания школы №40 Череповец\n\n"
        "▫ Расписание на завтра — выбираешь класс — получаешь только своё\n"
        "▫ Подпишись — и изменения будут приходить сами\n"
        "▫ Работает 24/7",
        reply_markup=main_menu
    )

@dp.message(F.text == "Расписание на завтра")
async def schedule_tomorrow(message: types.Message):
    await message.answer("Выбери параллель:", reply_markup=get_parallels_kb("sched"))

@dp.message(F.text == "Подписаться на уведомления")
async def subscribe_start(message: types.Message):
    current = subscribers.get(str(message.chat.id))
    if current:
        await message.answer(f"Ты уже подписан на {current}\nХочешь сменить класс?", reply_markup=get_parallels_kb("sub"))
    else:
        await message.answer("На какой класс подписаться?", reply_markup=get_parallels_kb("sub"))

@dp.message(F.text == "Отписаться")
async def unsubscribe(message: types.Message):
    chat_id = str(message.chat.id)
    if subscribers.pop(chat_id, None):
        save_json(SUBS_FILE, subscribers)
        await message.answer("Отписался от уведомлений")
    else:
        await message.answer("Ты и так не подписан")

@dp.callback_query(F.data.startswith("sched_par:") | F.data.startswith("sub_par:"))
async def process_parallel(callback: types.CallbackQuery):
    prefix = "sched" if callback.data.startswith("sched") else "sub"
    parallel = callback.data.split(":")[1]
    await callback.message.edit_text(f"Выбери букву для {parallel} класса:", reply_markup=get_letters_kb(parallel, prefix))

@dp.callback_query(F.data.startswith("sched_cls:") | F.data.startswith("sub_cls:"))
async def process_class(callback: types.CallbackQuery):
    prefix = "sched" if callback.data.startswith("sched") else "sub"
    class_name = callback.data.split(":")[1]
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    
    if prefix == "sched":
        await callback.message.edit_text("Ищу расписание... ⌛")
        text = await get_schedule_text(class_name, tomorrow)
        
        if text and "изменений нет" not in text.lower():
            await callback.message.answer(text)
            if subscribers.get(str(callback.from_user.id)) != class_name:
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("Подписаться на этот класс ✅", callback_data=f"directsub:{class_name}")]])
                await callback.message.answer("Хочешь получать это автоматически каждый день?", reply_markup=kb)
        else:
            url = await get_file_url_for_date(tomorrow)
            if url:
                content = await download_file(url)
                await callback.message.answer_document(BufferedInputFile(content, f"изменения_{tomorrow.strftime('%d.%m.%Y')}.xls"),
                                                     caption="Не смог распарсить — вот полный файл")
    
    else:  # подписка
        subscribers[str(callback.from_user.id)] = class_name
        save_json(SUBS_FILE, subscribers)
        await callback.message.edit_text(f"Готово! Ты подписан на <b>{class_name}</b>\nТеперь изменения будут приходить автоматически")

@dp.callback_query(F.data.startswith("directsub:"))
async def direct_sub(callback: types.CallbackQuery):
    class_name = callback.data.split(":")[1]
    subscribers[str(callback.from_user.id)] = class_name
    save_json(SUBS_FILE, subscribers)
    await callback.answer("Подписка оформлена!")
    await callback.message.answer(f"Теперь ты получаешь расписание {class_name} автоматически ✅")

# ==================== ЗАПУСК ====================
async def main():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_and_send_updates, "interval", minutes=25)
    scheduler.start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
