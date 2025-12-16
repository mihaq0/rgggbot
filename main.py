# main.py — 100% РАБОЧИЙ ДЛЯ ШКОЛЫ №40 ЧЕРЕПОВЕЦ
import asyncio
import datetime
import json
import os
import aiohttp
import pandas as pd
from io import BytesIO
from bs4 import BeautifulSoup

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ТВОЙ ТОКЕН — УЖЕ ВСТАВЛЕН
TOKEN = "7605214341:AAFHG0AyEGLnDcjPFqTOjzAWZZ3Z7s7EsqA"

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

URL = "https://sh40-cherepovec-r19.gosweb.gosuslugi.ru/roditelyam-i-uchenikam/izmeneniya-v-raspisanii/"

# База в памяти (на Render файлы не сохраняются надолго, но для бота хватает)
subs = {}      # {user_id: "10А"}
known = {}     # {дата: url}

# Главное меню
def main_menu():
    kb = [
        [InlineKeyboardButton(text="Расписание на завтра", callback_data="tomorrow")],
        [InlineKeyboardButton(text="Подписаться на уведомления", callback_data="subscribe")],
        [InlineKeyboardButton(text="Отписаться", callback_data="unsub")]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=kb)

# Параллели
def parallels_kb():
    kb = []
    row = []
    for p in "1 2 3 4 5 6 7 8 9 10 11".split():
        row.append(InlineKeyboardButton(text=p, callback_data=f"par_{p}"))
        if len(row) == 4:
            kb.append(row)
            row = []
    if row: kb.append(row)
    return types.InlineKeyboardMarkup(inline_keyboard=kb)

# Буквы
def letters_kb(parallel):
    letters = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЭЮЯ"
    kb = []
    row = []
    for letter in letters:
        row.append(InlineKeyboardButton(text=parallel + letter, callback_data=f"cls_{parallel}{letter}"))
        if len(row) == 5:
            kb.append(row)
            row = []
    if row: kb.append(row)
    return types.InlineKeyboardMarkup(inline_keyboard=kb)

# Поиск ссылки на завтра
async def get_tomorrow_url():
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%d.%m")
    async with aiohttp.ClientSession() as session:
        async with session.get(URL) as resp:
            html = await resp.text()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        text = a.text
        href = a["href"]
        if tomorrow in text and href.endswith((".xls", ".xlsx")):
            if href.startswith("/"):
                href = "https://sh40-cherepovec-r19.gosweb.gosuslugi.ru" + href
            return href
    return None

# Парсинг расписания
async def get_schedule(class_name):
    url = await get_tomorrow_url()
    if not url:
        return "Изменений на завтра пока нет 😴"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return "Ошибка загрузки файла"
            data = await resp.read()
    
    try:
        df = pd.read_excel(BytesIO(data))
    except:
        return "Не удалось прочитать файл 😭"
    
    df = df.applymap(lambda x: str(x).strip() if pd.notna(x) else "")
    
    # Ищем колонку с классами
    class_col = None
    for col in df.columns:
        if "класс" in str(col).lower():
            class_col = col
            break
    if not class_col:
        return "Не нашёл колонку с классами"
    
    rows = df[df[class_col].str.contains(class_name, case=False, na=False)]
    if rows.empty:
        return f"Для {class_name} изменений нет ✅"
    
    text = f"<b>Изменения для {class_name} на завтра:</b>\n\n"
    changes = False
    for _, row in rows.iterrows():
        for col in df.columns:
            if str(col).isdigit():
                val = row[col]
                if val and val not in ["", "-", "н", "нет", "—"]:
                    text += f"<b>{col}.</b> {val}\n"
                    changes = True
    return text if changes else f"Для {class_name} изменений нет ✅"

# Авто-рассылка
async def auto_send():
    url = await get_tomorrow_url()
    date_key = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    if not url or known.get(date_key) == url:
        return
    known[date_key] = url
    
    for user_id, cls in list(subs.items()):
        try:
            text = await get_schedule(cls)
            if "изменений нет" not in text.lower():
                await bot.send_message(int(user_id), text)
        except:
            pass  # если юзер заблокировал бота

# ==================== ХЭНДЛЕРЫ ====================
@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer(
        "Привет!\nЯ бот изменений в расписании школы №40 Череповец ❤️\n\n"
        "Нажми кнопку ниже 👇",
        reply_markup=main_menu()
    )

@dp.callback_query(F.data == "tomorrow")
async def tomorrow(cb: types.CallbackQuery):
    await cb.message.edit_text("Выбери параллель:", reply_markup=parallels_kb())

@dp.callback_query(F.data == "subscribe")
async def subscribe_start(cb: types.CallbackQuery):
    await cb.message.edit_text("На какой класс подписаться?", reply_markup=parallels_kb())

@dp.callback_query(F.data == "unsub")
async def unsub(cb: types.CallbackQuery):
    user_id = str(cb.from_user.id)
    if subs.pop(user_id, None):
        await cb.message.edit_text("Отписался от уведомлений ✅")
    else:
        await cb.message.edit_text("Ты и так не подписан 😉")

@dp.callback_query(F.data.startswith("par_"))
async def select_parallel(cb: types.CallbackQuery):
    parallel = cb.data.split("_")[1]
    await cb.message.edit_text(f"Теперь выбери букву для {parallel} класса:", reply_markup=letters_kb(parallel))

@dp.callback_query(F.data.startswith("cls_"))
async def show_schedule(cb: types.CallbackQuery):
    cls = cb.data.split("_", 1)[1]
    user_id = str(cb.from_user.id)
    
    await cb.message.edit_text("⌛ Ищу изменения...")
    text = await get_schedule(cls)
    
    await cb.message.edit_text(text)
    
    # Предлагаем подписку
    if subs.get(user_id) != cls:
        kb = types.InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Подписаться на этот класс ✅", callback_data=f"subfinal_{cls}")
        ]])
        await cb.message.answer("Хочешь получать это автоматически каждый день?", reply_markup=kb)

@dp.callback_query(F.data.startswith("subfinal_"))
async def final_sub(cb: types.CallbackQuery):
    cls = cb.data.split("_", 1)[1]
    subs[str(cb.from_user.id)] = cls
    await cb.answer("Готово!")
    await cb.message.edit_text(f"Теперь ты подписан на <b>{cls}</b>\nИзменения будут приходить автоматически ✅")

# Запуск
async def main():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(auto_send, "interval", minutes=30)
    scheduler.start()
    
    print("Бот запущен — школа №40 Череповец")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
