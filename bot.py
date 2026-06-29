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
        "🔒 *Acceso restringido*\n\nContacta al administrador para obtener acceso.",
        parse_mode="Markdown")
    return False


# ── Admin commands ────────────────────────────────────────────────────────────

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
    lines = [f"{'✅' if i.get('active') else '🚫'} {i.get('name','?')} — `{uid}`"
             for uid, i in clients.items()]
    await update.message.reply_text("👥 *Clientes:*\n\n" + "\n".join(lines), parse_mode="Markdown")


async def cmd_miid(update, ctx):
    await update.message.reply_text(f"🆔 Tu ID: `{update.effective_user.id}`", parse_mode="Markdown")


# ── Helpers ───────────────────────────────────────────────────────────────────

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Práctica rápida", callback_data="mode_quick")],
        [InlineKeyboardButton("📝 Simulacro de examen", callback_data="mode_exam")],
        [InlineKeyboardButton("🔁 Repasar mis fallos", callback_data="mode_review")],
        [InlineKeyboardButton("📚 Por capítulo", callback_data="mode_chapter")],
        [InlineKeyboardButton("📊 Mi progreso", callback_data="show_progress")],
    ])


def build_progress_bar(current, total):
    filled = int((current / total) * 10) if total else 0
    return "▓" * filled + "░" * (10 - filled)


def format_progress(stats):
    if not stats or not stats.get("total"):
        return "📊 Aún no tienes estadísticas. ¡Empieza a practicar!"
    pct = round(stats["correct"] / stats["total"] * 100) if stats["total"] else 0
    return (f"📊 *Tu progreso*\n\n📝 Respondidas: {stats['total']}\n"
            f"✅ Correctas: {stats['correct']}\n❌ Falladas: {stats['failed']}\n"
            f"📈 Precisión: *{pct}%*\n{build_progress_bar(pct, 100)}\n"
            f"🔁 Para repasar: {stats['to_review']}\n🔥 Racha: {stats.get('streak', 0)}")


# ── Conversation handlers ─────────────────────────────────────────────────────

async def start(update, ctx):
    if not await check_access(update):
        return ConversationHandler.END
    user = update.effective_user
    await update.message.reply_text(
        f"👋 ¡Hola, {user.first_name}! Soy *StudyBot* 📚\n\n"
        "Sube un PDF y te ayudaré a estudiarlo con preguntas IA.\n\n"
        "*Comandos:*\n/simulacro — Examen\n/resumen — Resumen\n"
        "/explicar — Explicar\n/progreso — Estadísticas\n/repasar — Fallos\n\n"
        "📎 *Envíame un PDF para empezar.*", parse_mode="Markdown")
    return WAITING_PDF


async def handle_pdf(update, ctx):
    if not await check_access(update):
        return ConversationHandler.END
    user_id = update.effective_user.id
    msg = await update.message.reply_text("⏳ Procesando tu PDF...")
    try:
        file = await update.message.document.get_file()
        pdf_path = f"/tmp/study_{user_id}.pdf"
        await file.download_to_drive(pdf_path)
        await msg.edit_text("📖 Extrayendo texto y capítulos...")
        chapters = pdf_processor.extract_chapters(pdf_path)
        if not chapters:
            await msg.edit_text("❌ No pude extraer texto. ¿Es un PDF escaneado?")
            return WAITING_PDF
        ctx.user_data.clear()
        ctx.user_data["chapters"] = chapters
        ctx.user_data["pdf_name"] = update.message.document.file_name
        ctx.user_data["current_q"] = None
        ctx.user_data["quiz_queue"] = []
        ctx.user_data["quiz_index"] = 0
        ctx.user_data["quiz_correct"] = 0
        ctx.user_data["quiz_total"] = 0
        all_questions = []
        for i, chapter in enumerate(chapters):
            chapter["_idx"] = i
            await msg.edit_text(f"🤖 Generando preguntas... {i+1}/{len(chapters)}")
            questions = await question_gen.generate_questions(chapter)
            all_questions.extend(questions)
        ctx.user_data["questions"] = all_questions
        progress_tracker.init_user(user_id, all_questions)
        chapter_list = "\n".join(f"  {i+1}. {ch['title']}" for i, ch in enumerate(chapters))
        await msg.edit_text(
            f"✅ *¡PDF procesado!*\n\n📘 *{ctx.user_data['pdf_name']}*\n"
            f"📂 Capítulos:\n{chapter_list}\n\n"
            f"❓ *{len(all_questions)} preguntas generadas*\n\n"
            f"¿Qué quieres hacer?",
            parse_mode="Markdown", reply_markup=main_menu_keyboard())
        return CHOOSING_MODE
    except Exception as e:
        logger.error(f"Error: {e}")
        await msg.edit_text(f"❌ Error: {str(e)}")
        return WAITING_PDF


async def menu_callback(update, ctx):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data == "mode_quick":
        questions = ctx.user_data.get("questions", [])
        queue = spaced_rep.get_study_queue(user_id, questions, limit=10)
        ctx.user_data["quiz_queue"] = queue
        ctx.user_data["quiz_index"] = 0
        ctx.user_data["quiz_correct"] = 0
        ctx.user_data["quiz_total"] = len(queue)
        await query.message.reply_text("🎯 *Práctica rápida — 10 preguntas*\n¡Vamos!", parse_mode="Markdown")
        return await send_question(update, ctx)

    elif data == "mode_exam":
        await query.edit_message_text(
            "📝 *Simulacro de examen*\n¿Cuántas preguntas?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("20 preguntas", callback_data="exam_20")],
                [InlineKeyboardButton("50 preguntas", callback_data="exam_50")],
                [InlineKeyboardButton("100 preguntas", callback_data="exam_100")],
            ]))
        return CHOOSING_EXAM_LEN

    elif data == "mode_review":
        questions = ctx.user_data.get("questions", [])
        failed = progress_tracker.get_failed_questions(user_id, questions)
        if not failed:
            await query.edit_message_text(
                "🎉 ¡No tienes fallos todavía!\nHaz una práctica primero.",
                reply_markup=main_menu_keyboard())
            return CHOOSING_MODE
        ctx.user_data["quiz_queue"] = failed[:20]
        ctx.user_data["quiz_index"] = 0
        ctx.user_data["quiz_correct"] = 0
        ctx.user_data["quiz_total"] = len(failed[:20])
        await query.message.reply_text(f"🔁 *Repaso — {len(failed[:20])} preguntas*", parse_mode="Markdown")
        return await send_question(update, ctx)

    elif data == "mode_chapter":
        chapters = ctx.user_data.get("chapters", [])
        keyboard = [[InlineKeyboardButton(f"📖 {ch['title'][:40]}", callback_data=f"chapter_{i}")]
                    for i, ch in enumerate(chapters)]
        await query.edit_message_text("📚 Selecciona un capítulo:", reply_markup=InlineKeyboardMarkup(keyboard))
        return CHOOSING_CHAPTER

    elif data == "show_progress":
        stats = progress_tracker.get_stats(user_id)
        await query.edit_message_text(
            format_progress(stats), parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="back_menu")]]))
        return CHOOSING_MODE

    elif data == "back_menu":
        await query.edit_message_text("¿Qué quieres hacer?", reply_markup=main_menu_keyboard())
        return CHOOSING_MODE

    return CHOOSING_MODE


async def exam_length_callback(update, ctx):
    query = update.callback_query
    await query.answer()
    n = {"exam_20": 20, "exam_50": 50, "exam_100": 100}.get(query.data, 20)
    questions = ctx.user_data.get("questions", [])
    queue = spaced_rep.get_study_queue(update.effective_user.id, questions, limit=n)
    ctx.user_data["quiz_queue"] = queue
    ctx.user_data["quiz_index"] = 0
    ctx.user_data["quiz_correct"] = 0
    ctx.user_data["quiz_total"] = len(queue)
    await query.message.reply_text(f"📝 *Simulacro de {len(queue)} preguntas*\n¡Comenzamos!", parse_mode="Markdown")
    return await send_question(update, ctx)


async def chapter_callback(update, ctx):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    all_q = ctx.user_data.get("questions", [])
    ch_qs = [q for q in all_q if q.get("chapter_idx") == idx][:20]
    ctx.user_data["quiz_queue"] = ch_qs
    ctx.user_data["quiz_index"] = 0
    ctx.user_data["quiz_correct"] = 0
    ctx.user_data["quiz_total"] = len(ch_qs)
    chapter_title = ctx.user_data["chapters"][idx]["title"]
    await query.edit_message_text(f"📖 *{chapter_title}*\n{len(ch_qs)} preguntas", parse_mode="Markdown")
    return await send_question(update, ctx)


async def send_question(update, ctx):
    queue = ctx.user_data.get("quiz_queue", [])
    idx = ctx.user_data.get("quiz_index", 0)
    if idx >= len(queue):
        return await show_quiz_results(update, ctx)
    question = queue[idx]
    ctx.user_data["current_q"] = question
    total = ctx.user_data.get("quiz_total", len(queue))
    header = f"*Pregunta {idx+1}/{total}* {build_progress_bar(idx, total)}\n\n"

    if question["type"] == "multiple_choice":
        text = header + f"❓ {question['question']}\n\n" + "\n".join(
            f"*{k})* {v}" for k, v in question["options"].items())
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(opt, callback_data=f"ans_{opt}")
            for opt in question["options"]
        ]])
    elif question["type"] == "true_false":
        text = header + f"🔵 *¿Verdadero o Falso?*\n\n_{question['question']}_"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Verdadero", callback_data="ans_Verdadero"),
            InlineKeyboardButton("❌ Falso", callback_data="ans_Falso")
        ]])
    else:
        text = header + f"🟣 *Pregunta abierta:*\n\n{question['question']}\n\n_Escribe tu respuesta..._"
        ctx.user_data["waiting_open"] = True
        keyboard = None

    if update.callback_query:
        if keyboard:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        else:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown")
    else:
        if keyboard:
            await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
        else:
            await update.effective_message.reply_text(text, parse_mode="Markdown")
    return IN_QUIZ


async def handle_answer_callback(update, ctx):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    answer = query.data.replace("ans_", "")
    question = ctx.user_data.get("current_q")
    if not question:
        return IN_QUIZ
    correct = question["answer"]
    is_ok = answer.strip().lower() == correct.strip().lower()
    progress_tracker.record_answer(user_id, question["id"], is_ok)
    spaced_rep.update(user_id, question["id"], is_ok)
    if is_ok:
        ctx.user_data["quiz_correct"] = ctx.user_data.get("quiz_correct", 0) + 1
        result_text = f"✅ *¡Correcto!*\n\n💡 {question.get('explanation', '')}"
    else:
        result_text = (f"❌ *Incorrecto.*\nTu respuesta: *{answer}*\n"
                       f"Correcta: *{correct}*\n\n💡 {question.get('explanation', '')}")
    ctx.user_data["quiz_index"] = ctx.user_data.get("quiz_index", 0) + 1
    await query.edit_message_text(
        result_text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("➡️ Siguiente", callback_data="next_question"),
            InlineKeyboardButton("💬 Explicar", callback_data="explain_current")
        ]]))
    return IN_QUIZ


async def handle_open_answer(update, ctx):
    if not ctx.user_data.get("waiting_open"):
        return IN_QUIZ
    ctx.user_data["waiting_open"] = False
    user_id = update.effective_user.id
    question = ctx.user_data.get("current_q")
    if not question:
        return IN_QUIZ
    msg = await update.message.reply_text("🤖 Evaluando tu respuesta...")
    evaluation = await question_gen.evaluate_open_answer(
        question["question"], question["answer"], update.message.text)
    is_ok = evaluation.get("score", 0) >= 60
    progress_tracker.record_answer(user_id, question["id"], is_ok)
    spaced_rep.update(user_id, question["id"], is_ok)
    if is_ok:
        ctx.user_data["quiz_correct"] = ctx.user_data.get("quiz_correct", 0) + 1
    ctx.user_data["quiz_index"] = ctx.user_data.get("quiz_index", 0) + 1
    await msg.edit_text(
        f"{'✅' if is_ok else '⚠️'} *Puntuación: {evaluation['score']}/100*\n\n"
        f"{evaluation['feedback']}\n\n💡 *Respuesta ideal:* {question['answer'][:300]}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("➡️ Siguiente", callback_data="next_question")
        ]]))
    return IN_QUIZ


async def next_question_callback(update, ctx):
    await update.callback_query.answer()
    return await send_question(update, ctx)


async def explain_current_callback(update, ctx):
    query = update.callback_query
    await query.answer()
    question = ctx.user_data.get("current_q")
    if not question:
        return IN_QUIZ
    explanation = await question_gen.get_detailed_explanation(question)
    await query.edit_message_text(
        f"💡 *Explicación*\n\n{explanation}", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("➡️ Siguiente", callback_data="next_question")
        ]]))
    return IN_QUIZ


async def show_quiz_results(update, ctx):
    correct = ctx.user_data.get("quiz_correct", 0)
    total = ctx.user_data.get("quiz_total", 1)
    pct = round(correct / total * 100) if total else 0
    emoji = "🏆" if pct >= 80 else "👍" if pct >= 60 else "📚"
    text = f"{emoji} *Resultados*\n\n✅ Correctas: {correct}/{total}\n📊 Puntuación: *{pct}%*"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔁 Repasar fallos", callback_data="mode_review"),
        InlineKeyboardButton("🏠 Menú", callback_data="back_menu")
    ]])
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
    return CHOOSING_MODE


async def cmd_explicar(update, ctx):
    if not await check_access(update):
        return
    question = ctx.user_data.get("current_q")
    if not question:
        await update.message.reply_text("❓ No hay pregunta activa.")
        return
    msg = await update.message.reply_text("💬 Generando explicación...")
    explanation = await question_gen.get_detailed_explanation(question)
    await msg.edit_text(f"💡 *Explicación*\n\n{explanation}", parse_mode="Markdown")


async def cmd_resumen(update, ctx):
    if not await check_access(update):
        return
    chapters = ctx.user_data.get("chapters")
    if not chapters:
        await update.message.reply_text("📎 Primero sube un PDF.")
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"📖 {ch['title'][:40]}", callback_data=f"summary_{i}")]
        for i, ch in enumerate(chapters)])
    await update.message.reply_text("¿De qué capítulo?", reply_markup=keyboard)


async def summary_callback(update, ctx):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    chapter = ctx.user_data.get("chapters", [])[idx]
    await query.edit_message_text("📝 Generando resumen...")
    summary = await question_gen.summarize_chapter(chapter)
    await query.edit_message_text(
        f"📖 *{chapter['title']}*\n\n{summary}", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Volver", callback_data="back_menu")
        ]]))
    return CHOOSING_MODE


async def cmd_progreso(update, ctx):
    if not await check_access(update):
        return
    stats = progress_tracker.get_stats(update.effective_user.id)
    await update.message.reply_text(format_progress(stats), parse_mode="Markdown")


async def cmd_repasar(update, ctx):
    if not await check_access(update):
        return ConversationHandler.END
    user_id = update.effective_user.id
    failed = progress_tracker.get_failed_questions(user_id, ctx.user_data.get("questions", []))
    if not failed:
        await update.message.reply_text("🎉 ¡No tienes fallos! Sigue practicando.")
        return CHOOSING_MODE
    ctx.user_data["quiz_queue"] = failed[:20]
    ctx.user_data["quiz_index"] = 0
    ctx.user_data["quiz_correct"] = 0
    ctx.user_data["quiz_total"] = len(failed[:20])
    await update.message.reply_text(f"🔁 Repasando {len(failed[:20])} preguntas falladas...")
    return await send_question(update, ctx)


async def cmd_nuevo(update, ctx):
    if not await check_access(update):
        return ConversationHandler.END
    ctx.user_data.clear()
    await update.message.reply_text("📎 Envíame el nuevo PDF.")
    return WAITING_PDF


async def cmd_simulacro(update, ctx):
    if not await check_access(update):
        return ConversationHandler.END
    if not ctx.user_data.get("questions"):
        await update.message.reply_text("📎 Primero sube un PDF.")
        return ConversationHandler.END
    await update.message.reply_text(
        "📝 ¿Cuántas preguntas?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("20 preguntas", callback_data="exam_20")],
            [InlineKeyboardButton("50 preguntas", callback_data="exam_50")],
            [InlineKeyboardButton("100 preguntas", callback_data="exam_100")],
        ]))
    return CHOOSING_EXAM_LEN


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Falta TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(token).build()
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("nuevo", cmd_nuevo),
        ],
        states={
            WAITING_PDF: [
                MessageHandler(filters.Document.PDF, handle_pdf),
            ],
            CHOOSING_MODE: [
                CallbackQueryHandler(menu_callback, pattern="^(mode_|show_progress|back_menu)"),
                CallbackQueryHandler(summary_callback, pattern="^summary_"),
                CommandHandler("resumen", cmd_resumen),
                CommandHandler("progreso", cmd_progreso),
                CommandHandler("simulacro", cmd_simulacro),
                CommandHandler("repasar", cmd_repasar),
            ],
            CHOOSING_EXAM_LEN: [
                CallbackQueryHandler(exam_length_callback, pattern="^exam_"),
            ],
            CHOOSING_CHAPTER: [
                CallbackQueryHandler(chapter_callback, pattern="^chapter_"),
            ],
            IN_QUIZ: [
                CallbackQueryHandler(handle_answer_callback, pattern="^ans_"),
                CallbackQueryHandler(next_question_callback, pattern="^next_question$"),
                CallbackQueryHandler(explain_current_callback, pattern="^explain_current$"),
                CallbackQueryHandler(exam_length_callback, pattern="^exam_"),
                CallbackQueryHandler(menu_callback, pattern="^(mode_|back_menu)"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_open_answer),
                CommandHandler("explicar", cmd_explicar),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("nuevo", cmd_nuevo),
            CommandHandler("progreso", cmd_progreso),
            CommandHandler("resumen", cmd_resumen),
            CommandHandler("simulacro", cmd_simulacro),
            CommandHandler("repasar", cmd_repasar),
            CommandHandler("explicar", cmd_explicar),
        ],
        allow_reentry=True,
        per_message=False,
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("activar", cmd_activar))
    app.add_handler(CommandHandler("desactivar", cmd_desactivar))
    app.add_handler(CommandHandler("clientes", cmd_clientes))
    app.add_handler(CommandHandler("miid", cmd_miid))
    app.add_handler(CommandHandler("progreso", cmd_progreso))
    app.add_handler(CommandHandler("explicar", cmd_explicar))
    app.add_handler(CommandHandler("resumen", cmd_resumen))
    app.add_handler(CallbackQueryHandler(summary_callback, pattern="^summary_"))
    print("🤖 StudyBot corriendo...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
