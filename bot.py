import os
import json
import logging
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)
from pdf_processor import PDFProcessor
from question_generator import QuestionGenerator
from progress_tracker import ProgressTracker
from spaced_repetition import SpacedRepetition

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

WAITING_PDF = 1
CHOOSING_MODE = 2
IN_QUIZ = 3
CHOOSING_CHAPTER = 4
CHOOSING_EXAM_LEN = 5

pdf_processor = PDFProcessor()
question_gen = QuestionGenerator()
progress_tracker = ProgressTracker()
spaced_rep = SpacedRepetition()

ADMIN_ID = int(os.environ.get("ADMIN_ID", "735702535"))
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
CLIENTS_FILE = DATA_DIR / "clients.json"

def load_clients():
    if CLIENTS_FILE.exists():
        try:
            return json.loads(CLIENTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_clients(data):
    CLIENTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def is_active(user_id):
    if user_id == ADMIN_ID:
        return True
    clients = load_clients()
    return str(user_id) in clients and clients[str(user_id)].get("active", False)

async def check_access(update):
    if is_active(update.effective_user.id):
        return True
    await update.effective_message.reply_text(
        "🔒 *Acceso restringido*\n\nEste bot es de uso privado.\nContacta al administrador para obtener acceso.",
        parse_mode="Markdown")
    return False

async def cmd_activar(update, ctx):
    if update.effective_user.id != ADMIN_ID:
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("Uso: /activar <user_id> <nombre>")
        return
    uid = args[0]
    nombre = " ".join(args[1:]) if len(args) > 1 else "Cliente"
    clients = load_clients()
    clients[uid] = {"active": True, "name": nombre}
    save_clients(clients)
    await update.message.reply_text(f"✅ *{nombre}* (`{uid}`) activado.", parse_mode="Markdown")

async def cmd_desactivar(update, ctx):
    if update.effective_user.id != ADMIN_ID:
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("Uso: /desactivar <user_id>")
        return
    uid = args[0]
    clients = load_clients()
    if uid in clients:
        clients[uid]["active"] = False
        save_clients(clients)
        await update.message.reply_text(f"🚫 Usuario `{uid}` desactivado.", parse_mode="Markdown")
    else:
        await update.message.reply_text("No encontré ese usuario.")

async def cmd_clientes(update, ctx):
    if update.effective_user.id != ADMIN_ID:
        return
    clients = load_clients()
    if not clients:
        await update.message.reply_text("No hay clientes registrados.")
        return
    lines = [f"{'✅' if i.get('active') else '🚫'} {i.get('name','?')} — `{uid}`" for uid, i in clients.items()]
    await update.message.reply_text("👥 *Clientes:*\n\n" + "\n".join(lines), parse_mode="Markdown")

async def cmd_miid(update, ctx):
    await update.message.reply_text(f"🆔 Tu ID: `{update.effective_user.id}`", parse_mode="Markdown")
