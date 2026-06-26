import asyncio
import io
import json
import os
import zipfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters


def load_env_file() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()
TOKEN = os.getenv("TG_BOT_TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TKN") or os.getenv("TOKEN")

DATA_FILE = "reading_bot_data.json"
ASTANA_TZ = ZoneInfo("Asia/Almaty")

BOOKS = {}

FAKE_USERS = {
    "100001": {
        "id": 100001,
        "first_name": "Maqsat",
        "username": "maksat_oq",
        "current_book": "atomic_habits",
        "book_title": "Atomic Habits",
        "book_source": None,
        "daily_pages_goal": 25,
        "pages_read": 120,
        "last_read_date": (date.today() - timedelta(days=1)).isoformat(),
        "streak": 5,
        "best_streak": 7,
        "reminder_time": "08:00",
        "read_history": [
            {"date": (date.today() - timedelta(days=4)).isoformat(), "pages": 20},
            {"date": (date.today() - timedelta(days=3)).isoformat(), "pages": 30},
            {"date": (date.today() - timedelta(days=2)).isoformat(), "pages": 25},
            {"date": (date.today() - timedelta(days=1)).isoformat(), "pages": 45},
        ],
        "group_name": None,
    },
    "100002": {
        "id": 100002,
        "first_name": "Zhalgas",
        "username": "zhalgas_oq",
        "current_book": "clean_code",
        "book_title": "Clean Code",
        "book_source": None,
        "daily_pages_goal": 20,
        "pages_read": 95,
        "last_read_date": (date.today() - timedelta(days=2)).isoformat(),
        "streak": 3,
        "best_streak": 5,
        "reminder_time": "21:00",
        "read_history": [
            {"date": (date.today() - timedelta(days=5)).isoformat(), "pages": 15},
            {"date": (date.today() - timedelta(days=3)).isoformat(), "pages": 40},
            {"date": (date.today() - timedelta(days=2)).isoformat(), "pages": 40},
        ],
        "group_name": None,
    },
}

def add_friend(profile: Dict[str, Any], username: str) -> str:
    normalized = username.strip().lstrip("@")
    friends = profile.setdefault("friends", [])
    if normalized not in friends:
        friends.append(normalized)
    return f"{username} добавлен в друзья."


def calculate_question_count(books_count: int) -> int:
    if books_count <= 1:
        return 2
    if books_count <= 20:
        return 5
    if books_count <= 50:
        return 10
    return max(2, round(books_count / 10))


def load_data() -> Dict[str, Any]:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {"users": {}, "books": {}, "groups": {}}

def save_data(data: Dict[str, Any]) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)

def get_or_create_user(data: Dict[str, Any], user: Any) -> Dict[str, Any]:
    user_id = str(user.id)
    if user_id not in data["users"]:
        data["users"][user_id] = {
            "id": user.id,
            "first_name": user.first_name or "User",
            "username": user.username or "",
            "current_book": None,
            "book_title": None,
            "book_source": None,
            "daily_pages_goal": 20,
            "pages_read": 0,
            "last_read_date": None,
            "streak": 0,
            "best_streak": 0,
            "reminder_time": None,
            "read_history": [],
            "group_name": None,
            "awaiting": None,
        }
    profile = data["users"][user_id]
    profile["first_name"] = user.first_name or profile.get("first_name", "User")
    profile["username"] = user.username or profile.get("username", "")
    if "awaiting" not in profile:
        profile["awaiting"] = None
    return profile

def update_progress(profile: Dict[str, Any], pages_read: int) -> None:
    profile["pages_read"] += pages_read
    today = date.today().isoformat()
    history = profile.setdefault("read_history", [])
    today_entry = next((entry for entry in history if entry.get("date") == today), None)
    if today_entry is None:
        history.append({"date": today, "pages": pages_read})
    else:
        today_entry["pages"] = today_entry.get("pages", 0) + pages_read

    last_date = profile.get("last_read_date")
    if last_date == today:
        return
    if last_date:
        last_dt = date.fromisoformat(last_date)
        if last_dt + timedelta(days=1) == date.today():
            profile["streak"] = profile.get("streak", 0) + 1
        else:
            profile["streak"] = 1
    else:
        profile["streak"] = 1
    profile["best_streak"] = max(profile.get("best_streak", 0), profile["streak"])
    profile["last_read_date"] = today

def get_user_by_id(data: Dict[str, Any], friend_id: str) -> Optional[Dict[str, Any]]:
    if friend_id in data.get("users", {}):
        return data["users"][friend_id]
    if friend_id in FAKE_USERS:
        return FAKE_USERS[friend_id]
    return None

def build_progress_chart_bytes(profile: Dict[str, Any]) -> Optional[bytes]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        history = profile.get("read_history", [])
        if not history:
            return None

        dates = [datetime.strptime(e["date"], "%Y-%m-%d") for e in history[-14:]]
        pages = [e.get("pages", 0) for e in history[-14:]]
        goal = profile.get("daily_pages_goal", 20)

        fig, ax = plt.subplots(figsize=(8, 4))
        ax.bar(dates, pages, color="#4CAF50", alpha=0.8, label="Прочитано")
        ax.axhline(y=goal, color="#FF5722", linestyle="--", linewidth=1.5, label=f"Цель: {goal} стр/день")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        ax.xaxis.set_major_locator(mdates.DayLocator())
        plt.xticks(rotation=45)
        ax.set_ylabel("Страниц")
        ax.set_title(f"📈 История чтения: {profile.get('book_title') or 'книга'}")
        ax.legend()
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()
    except Exception:
        return None

def create_progress_report(profile: Dict[str, Any]) -> str:
    book_name = profile.get("book_title") or (BOOKS.get(profile.get("current_book"), {}).get("title", "не выбрана"))
    total_pages = profile.get("pages_read", 0)
    goal = profile.get("daily_pages_goal", 20)
    today = date.today().isoformat()
    history = profile.get("read_history", [])
    today_entry = next((e for e in history if e.get("date") == today), None)
    today_pages = today_entry.get("pages", 0) if today_entry else 0
    progress_pct = min(100, round(today_pages / max(1, goal) * 100, 1))

    chart = "📅 Последние записи:\n"
    for entry in history[-7:]:
        bar_len = min(10, entry.get("pages", 0) // max(1, goal // 10))
        bar = "█" * bar_len + "░" * (10 - bar_len)
        chart += f"  {entry['date']}: {bar} {entry.get('pages', 0)} стр.\n"
    if not history:
        chart += "  пока нет записей\n"

    return (
        f"� Прогресс чтения\n"
        f"📖 Книга: {book_name}\n"
        f"📄 Прочитано страниц: {total_pages} стр.\n"
        f"📆 Сегодня: {today_pages} стр. ({progress_pct}% от цели)\n"
        f"🔥 Серия: {profile.get('streak', 0)} дней\n"
        f"🏆 Лучшая серия: {profile.get('best_streak', 0)} дней\n"
        f"🎯 Цель: {goal} стр/день\n\n"
        f"{chart}"
    )

def get_leaderboard(data: Dict[str, Any]) -> list:
    users = list(data.get("users", {}).values())
    users.extend(FAKE_USERS.values())
    return sorted(users, key=lambda item: (item.get("pages_read", 0), item.get("streak", 0)), reverse=True)

def build_book_keyboard(data: Dict[str, Any]) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    combined_books = {**BOOKS}
    for book_id, book in data.get("books", {}).items():
        combined_books[book_id] = book
    for book_id, book in combined_books.items():
        buttons.append([InlineKeyboardButton(text=book["title"], callback_data=f"book:{book_id}")])
    return InlineKeyboardMarkup(buttons)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    data = load_data()
    get_or_create_user(data, user)
    save_data(data)
    
    # Используем HTML-теги: <b> — жирный, <i> — курсив
    text = (
        "👋 Привет! Я <b>Reading Bot</b> — твой помощник для регулярного чтения.\n\n"
        "📚 <b>Основные команды:</b>\n"
        "/book — выбрать книгу\n"
        "/add — загрузить книгу\n"
        "/plan — цель на день\n"
        "/completed 20 — отметить прочитанное\n"
        "/progress — прогресс\n"
        "/group — управление группами\n"
        "/leaderboard — рейтинг\n"
        "/remind 20:30 — напоминания"
    )
    # Меняем Markdown на HTML
    await update.message.reply_text(text, parse_mode="HTML")

async def book_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_data()
    await update.message.reply_text("📚 Выбери книгу из списка:", reply_markup=build_book_keyboard(data))

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("📎 Отправь мне файл книги (PDF, TXT, DOCX).")

async def plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_data()
    profile = get_or_create_user(data, update.effective_user)
    if context.args:
        try:
            pages = int(context.args[0])
            if pages <= 0: raise ValueError
        except ValueError:
            await update.message.reply_text("Введите целое число: /plan 23")
            return
        profile["daily_pages_goal"] = pages
        profile["awaiting"] = None
        save_data(data)
        await update.message.reply_text(f"✅ Цель: {pages} стр/день.")
    else:
        profile["awaiting"] = "plan_pages"
        save_data(data)
        await update.message.reply_text("Сколько страниц хотите читать в день?")

async def completed_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_data()
    profile = get_or_create_user(data, update.effective_user)
    if not context.args:
        await update.message.reply_text("Пример: /completed 20")
        return
    try:
        pages = int(context.args[0])
        if pages <= 0: raise ValueError
    except ValueError:
        return
    update_progress(profile, pages)
    save_data(data)
    await update.message.reply_text(f"✅ Прочитано: {pages}.\n\n{create_progress_report(profile)}", parse_mode="Markdown")

async def progress_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Мой личный прогресс", callback_data="progress:personal")],
        [InlineKeyboardButton("👥 Прогресс группы", callback_data="progress:group")],
    ])
    await update.message.reply_text("📊 Какой прогресс показать?", reply_markup=keyboard)

async def group_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👥 *Управление группами*\n\n"
        "Используйте `/create_group Название` для создания группы.\n"
        "Используйте `/add_to_group ID` для добавления участников.",
        parse_mode="Markdown"
    )

async def create_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_data()
    profile = get_or_create_user(data, update.effective_user)
    if not context.args:
        profile["awaiting"] = "create_group_name"
        save_data(data)
        await update.message.reply_text("📝 Название группы:")
        return
    group_name = " ".join(context.args)
    _create_group(data, profile, str(update.effective_user.id), group_name)
    save_data(data)
    await update.message.reply_text(f"✅ Группа *{group_name}* создана!", parse_mode="Markdown")

async def add_to_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_data()
    user_id = str(update.effective_user.id)
    groups = data.setdefault("groups", {})
    my_group_id = next((gid for gid, g in groups.items() if g.get("creator") == user_id), None)
    if not my_group_id:
        await update.message.reply_text("Сначала создайте группу через /create_group")
        return
    if not context.args: return
    friend_id = context.args[0]
    members = groups[my_group_id].setdefault("members", [])
    if friend_id not in members:
        members.append(friend_id)
        save_data(data)
        await update.message.reply_text(f"✅ {friend_id} добавлен в группу.")

def _create_group(data: Dict, profile: Dict, creator_id: str, group_name: str) -> str:
    groups = data.setdefault("groups", {})
    group_id = f"group_{creator_id}_{len(groups) + 1}"
    groups[group_id] = {
        "id": group_id,
        "name": group_name,
        "creator": creator_id,
        "members": [creator_id],
        "created_at": date.today().isoformat(),
    }
    profile["group_name"] = group_name
    return group_id

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_data()
    ranking = get_leaderboard(data)[:10]
    lines = ["🏆 *Рейтинг читателей:*"]
    for idx, item in enumerate(ranking, start=1):
        lines.append(f"{idx}. {item.get('first_name', 'User')} — {item.get('pages_read', 0)} стр.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("⏰ Пожалуйста, укажите время. Пример:\n/remind 20:30", parse_mode="HTML")
        return
    
    time_str = context.args[0]
    data = load_data()
    profile = get_or_create_user(data, update.effective_user)
    profile["reminder_time"] = time_str
    save_data(data)
    
    await update.message.reply_text(f"✅ Напоминание установлено на <b>{time_str}</b>", parse_mode="HTML")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = load_data()
    profile = get_or_create_user(data, update.effective_user)

    if query.data.startswith("book:"):
        book_id = query.data.split(":", 1)[1]
        book_title = BOOKS.get(book_id, {}).get("title")
        profile["current_book"] = book_id
        profile["book_title"] = book_title
        save_data(data)
        await query.edit_message_text(f"✅ Выбрана: *{book_title}*", parse_mode="Markdown")

    elif query.data == "progress:personal":
        report = create_progress_report(profile)
        chart_bytes = build_progress_chart_bytes(profile)
        if chart_bytes:
            await query.message.reply_photo(photo=io.BytesIO(chart_bytes), caption=report, parse_mode="Markdown")
            await query.delete_message()
        else:
            await query.edit_message_text(report, parse_mode="Markdown")

    elif query.data == "progress:group":
        lines = ["👥 *Прогресс группы:*"]
        for fu in FAKE_USERS.values():
            lines.append(f"• {fu['first_name']} — {fu['pages_read']} стр.")
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.document:
        await handle_uploaded_book(update, context)
        return
    data = load_data()
    profile = get_or_create_user(data, update.effective_user)
    text = update.message.text.strip()
    if profile.get("awaiting") == "plan_pages":
        profile["daily_pages_goal"] = int(text)
        profile["awaiting"] = None
        save_data(data)
        await update.message.reply_text("✅ Цель обновлена.")
    elif profile.get("awaiting") == "create_group_name":
        _create_group(data, profile, str(update.effective_user.id), text)
        profile["awaiting"] = None
        save_data(data)
        await update.message.reply_text(f"✅ Группа *{text}* создана!", parse_mode="Markdown")

async def handle_uploaded_book(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.message.document
    file = await context.bot.get_file(doc.file_id)
    local_path = os.path.join(os.getcwd(), doc.file_name or "book")
    await file.download_to_drive(custom_path=local_path)
    data = load_data()
    profile = get_or_create_user(data, update.effective_user)
    profile["book_title"] = doc.file_name
    profile["book_source"] = local_path
    save_data(data)
    await update.message.reply_text("📚 Книга загружена!")

if __name__ == "__main__":
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("book", book_command))
    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler("plan", plan_command))
    application.add_handler(CommandHandler("completed", completed_command))
    application.add_handler(CommandHandler("progress", progress_command))
    application.add_handler(CommandHandler("group", group_command))
    application.add_handler(CommandHandler("create_group", create_group_command))
    application.add_handler(CommandHandler("add_to_group", add_to_group_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard_command))
    application.add_handler(CommandHandler("remind", remind_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_uploaded_book))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.run_polling(allowed_updates=Update.ALL_TYPES)