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

# ТВОЙ ТОКЕН — БЕРЕТСЯ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if TOKEN else None
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
    tomorrow_dt = datetime.date.today() + datetime.timedelta(days=1)
    tomorrow_dot = tomorrow_dt.strftime("%d.%m")
    tomorrow_day = str(tomorrow_dt.day)

    months = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    tomorrow_month = months[tomorrow_dt.month - 1]

    async with aiohttp.ClientSession() as session:
        async with session.get(URL) as resp:
            html = await resp.text()

    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        text = a.text.lower()
        href = a["href"].lower()

        # Проверяем наличие даты в разных форматах
        has_date = (tomorrow_dot in text or
                    (tomorrow_day in text and tomorrow_month[:3] in text) or
                    tomorrow_dot in href or
                    (tomorrow_day in href and tomorrow_month[:3] in href))

        if has_date and href.endswith((".xls", ".xlsx")):
            full_href = a["href"]
            if full_href.startswith("/"):
                full_href = "https://sh40-cherepovec-r19.gosweb.gosuslugi.ru" + full_href

            # Приоритет ссылкам со словом "смена"
            if "смена" in text or "smena" in href:
                return full_href
            links.append(full_href)

    return links[0] if links else None

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
        df = pd.read_excel(BytesIO(data), header=None)
    except:
        return "Не удалось прочитать файл 😭"
    
    df = df.fillna("")
    search_name = class_name.replace(" ", "").lower()
    
    # Ищем колонку с классом
    target_col = None
    for j in range(df.shape[1]):
        for i in range(min(10, df.shape[0])):
            if search_name == str(df.iloc[i, j]).replace(" ", "").lower():
                target_col = j
                break
        if target_col is not None: break
    
    if target_col is None:
        return f"Для {class_name} изменений нет ✅"
    
    # Ищем колонку с номерами уроков
    num_col = None
    for j in range(df.shape[1]):
        for i in range(min(10, df.shape[0])):
            if "№" in str(df.iloc[i, j]):
                num_col = j
                break
        if num_col is not None: break
    if num_col is None: num_col = 1

    text = f"<b>Изменения для {class_name} на завтра:</b>\n\n"
    changes = []

    def clean(val):
        s = str(val).strip()
        if s.endswith(".0"): s = s[:-2]
        return s

    for i in range(df.shape[0]):
        lesson_num = clean(df.iloc[i, num_col])
        if lesson_num.isdigit():
            subject = clean(df.iloc[i, target_col])
            info = ""
            if target_col + 1 < df.shape[1]:
                info = clean(df.iloc[i, target_col+1])

            # Проверяем следующую строку (там может быть кабинет или продолжение названия)
            if i + 1 < df.shape[0] and not clean(df.iloc[i+1, num_col]).isdigit():
                sub2 = clean(df.iloc[i+1, target_col])
                if sub2: subject += f" {sub2}"
                if target_col + 1 < df.shape[1]:
                    inf2 = clean(df.iloc[i+1, target_col+1])
                    if inf2: info += f" {inf2}"

            if subject or info:
                changes.append(f"<b>{lesson_num}.</b> {subject} {info}".strip())

    if not changes:
        return f"Для {class_name} изменений нет ✅"

    return text + "\n".join(changes)

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
