import os
import json
import logging
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from pdf_processor import PDFProcessor
from question_generator import QuestionGenerator
from progress_tracker import ProgressTracker
from spaced_repetition import SpacedRepetition

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

pdf_processor = PDFProcessor()
question_gen = QuestionGenerator()
progress_tracker = ProgressTracker()
spaced_rep = SpacedRepetition()

ADMIN_ID = int(os.environ.get("ADMIN_ID", "735702535"))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "tu_usuario")
FREE_QUESTIONS = 5
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


def is_trial(user_id):
    """Devuelve True si el usuario está en período de prueba (no ha agotado sus preguntas gratis)."""
    if user_id == ADMIN_ID:
        return False
    clients = load_clients()
    uid = str(user_id)
    if uid not in clients:
        return True  # usuario nuevo = en prueba
    client = clients[uid]
    if client.get("active"):
        return False  # ya pagó
    return client.get("trial_questions", 0) < FREE_QUESTIONS


def get_trial_questions_used(user_id):
    clients = load_clients()
    uid = str(user_id)
    if uid not in clients:
        return 0
    return clients[uid].get("trial_questions", 0)


def increment_trial_questions(user_id):
    clients = load_clients()
    uid = str(user_id)
    if uid not in clients:
        clients[uid] = {"active": False, "name": "Trial", "trial_questions": 0}
    clients[uid]["trial_questions"] = clients[uid].get("trial_questions", 0) + 1
    save_clients(clients)


def has_trial_expired(user_id):
    if user_id == ADMIN_ID:
        return False
    clients = load_clients()
    uid = str(user_id)
    if uid not in clients:
        return False
    client = clients[uid]
    if client.get("active"):
        return False
    return client.get("trial_questions", 0) >= FREE_QUESTIONS


async def check_access(update):
    """Permite acceso si: es admin, tiene cuenta activa, o está en prueba gratuita."""
    user_id = update.effective_user.id
    if user_id == ADMIN_ID:
        return True
    if is_active(user_id):
        return True
    if is_trial(user_id):
        return True
    # Trial expirado
    await update.effective_message.reply_text(
        "🔒 *Has usado tus 5 preguntas gratuitas.*\n\n"
        "¿Te gustó StudyBot? 😊\n\n"
        "Para continuar estudiando sin límites activa tu acceso mensual por solo *$5/mes*.\n\n"
        f"📲 Escríbeme para activarte:\n@{ADMIN_USERNAME}",
        parse_mode="Markdown"
    )
    return False


async def check_trial_limit(update, ctx) -> bool:
    """
    Llama esto antes de enviar cada pregunta.
    Devuelve True si puede continuar, False si debe pagar.
    """
    user_id = update.effective_user.id if update else None
    if not user_id or user_id == ADMIN_ID or is_active(user_id):
        return True

    if has_trial_expired(user_id):
        await ctx.bot.send_message(
            chat_id=update.effective_chat.id if update else ctx.user_data.get("chat_id"),
            text=(
                "🔒 *¡Has completado tu prueba gratuita!*\n\n"
                f"Respondiste tus {FREE_QUESTIONS} preguntas de muestra. "
                "¿Qué tal la experiencia? 😊\n\n"
                "Para estudiar sin límites con todos tus PDFs, activa tu acceso:\n\n"
                "✅ Preguntas ilimitadas\n"
                "✅ Todos tus PDFs\n"
                "✅ Simulacros completos\n"
                "✅ Repaso inteligente\n\n"
                "*Solo $5/mes* 💜\n\n"
                f"📲 Escríbeme: @{ADMIN_USERNAME}"
            ),
            parse_mode="Markdown"
        )
        return False
    return True


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


async def send_question(ctx, chat_id, user_data, user_id=None):
    queue = user_data.get("quiz_queue", [])
    idx = user_data.get("quiz_index", 0)
    total = user_data.get("quiz_total", len(queue))

    if idx >= len(queue):
        correct = user_data.get("quiz_correct", 0)
        pct = round(correct / total * 100) if total else 0
        emoji = "🏆" if pct >= 80 else "👍" if pct >= 60 else "📚"

        # Verificar si el usuario respondió TODAS las preguntas disponibles
        all_questions = user_data.get("questions", [])
        answered_all = len(all_questions) > 0 and idx >= len(all_questions)

        if answered_all:
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"{emoji} *Resultados*\n\n✅ Correctas: {correct}/{total}\n📊 Puntuación: *{pct}%*\n\n"
                    "━━━━━━━━━━━━━━━\n"
                    "✅ *¡Ya respondiste todas las preguntas disponibles!*\n\n"
                    "¿Quieres que genere 15 preguntas nuevas del mismo PDF?"
                ),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Sí, generar más", callback_data="gen_more"),
                    InlineKeyboardButton("🏠 Volver al menú", callback_data="back_menu")
                ]]))
        else:
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=f"{emoji} *Resultados*\n\n✅ Correctas: {correct}/{total}\n📊 Puntuación: *{pct}%*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔁 Repasar fallos", callback_data="mode_review"),
                    InlineKeyboardButton("🏠 Menú", callback_data="back_menu")
                ]]))
        return

    # Verificar límite de prueba
    if user_id and has_trial_expired(user_id):
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=(
                "🔒 *¡Has completado tu prueba gratuita!*\n\n"
                f"Respondiste tus {FREE_QUESTIONS} preguntas de muestra 😊\n\n"
                "Para estudiar sin límites activa tu acceso:\n\n"
                "✅ Preguntas ilimitadas\n"
                "✅ Todos tus PDFs\n"
                "✅ Simulacros completos\n"
                "✅ Repaso inteligente\n\n"
                f"*Solo $5/mes* 💜\n\n📲 Escríbeme: @{ADMIN_USERNAME}"
            ),
            parse_mode="Markdown"
        )
        return

    # Mostrar cuántas preguntas gratis quedan
    trial_banner = ""
    if user_id and not is_active(user_id) and user_id != ADMIN_ID:
        used = get_trial_questions_used(user_id)
        remaining = FREE_QUESTIONS - used
        trial_banner = f"🆓 _Prueba gratuita: {remaining} pregunta{'s' if remaining != 1 else ''} restante{'s' if remaining != 1 else ''}_\n\n"

    question = queue[idx]
    user_data["current_q"] = question
    header = f"*Pregunta {idx+1}/{total}* {build_progress_bar(idx, total)}\n\n"

    if question["type"] == "multiple_choice":
        text = trial_banner + header + f"❓ {question['question']}\n\n" + "\n".join(
            f"*{k})* {v}" for k, v in question["options"].items())
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(opt, callback_data=f"ans_{opt}")
            for opt in question["options"]
        ]])
    elif question["type"] == "true_false":
        text = trial_banner + header + f"🔵 *¿Verdadero o Falso?*\n\n_{question['question']}_"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Verdadero", callback_data="ans_Verdadero"),
            InlineKeyboardButton("❌ Falso", callback_data="ans_Falso")
        ]])
    else:
        text = trial_banner + header + f"🟣 *Pregunta abierta:*\n\n{question['question']}\n\n_Escribe tu respuesta..._"
        user_data["waiting_open"] = True
        keyboard = None

    try:
        if keyboard:
            await ctx.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=keyboard)
        else:
            await ctx.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error enviando pregunta: {e}")


# ── Comandos ──────────────────────────────────────────────────────────────────

async def cmd_start(update, ctx):
    if not await check_access(update):
        return
    user = update.effective_user
    text = (
        f"👋 ¡Hola, {user.first_name}! Soy *StudyBot* 📚\n\n"
        "📎 *Envíame un PDF para empezar.*\n\n"
        "*Comandos:*\n/menu — Ver opciones de estudio\n"
        "/simulacro — Examen\n/resumen — Resumen\n"
        "/explicar — Explicar\n/progreso — Estadísticas\n"
        "/repasar — Fallos\n/nuevo — Nuevo PDF"
    )
    if ctx.user_data.get("questions"):
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())
    else:
        await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_menu(update, ctx):
    if not await check_access(update):
        return
    if ctx.user_data.get("questions"):
        await update.message.reply_text("¿Qué quieres hacer?", reply_markup=main_menu_keyboard())
    else:
        await update.message.reply_text("📎 Primero sube un PDF para ver el menú.")


async def cmd_nuevo(update, ctx):
    if not await check_access(update):
        return
    ctx.user_data.clear()
    await update.message.reply_text("📎 Envíame el nuevo PDF.")


async def cmd_progreso(update, ctx):
    if not await check_access(update):
        return
    stats = progress_tracker.get_stats(update.effective_user.id)
    await update.message.reply_text(format_progress(stats), parse_mode="Markdown")


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


async def cmd_repasar(update, ctx):
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    failed = progress_tracker.get_failed_questions(user_id, ctx.user_data.get("questions", []))
    if not failed:
        await update.message.reply_text("🎉 ¡No tienes fallos! Sigue practicando.")
        return
    ctx.user_data["quiz_queue"] = failed[:20]
    ctx.user_data["quiz_index"] = 0
    ctx.user_data["quiz_correct"] = 0
    ctx.user_data["quiz_total"] = len(failed[:20])
    await update.message.reply_text(f"🔁 Repasando {len(failed[:20])} preguntas falladas...")
    await send_question(ctx, update.effective_chat.id, ctx.user_data, update.effective_user.id)


async def cmd_simulacro(update, ctx):
    if not await check_access(update):
        return
    if not ctx.user_data.get("questions"):
        await update.message.reply_text("📎 Primero sube un PDF.")
        return
    await update.message.reply_text(
        "📝 ¿Cuántas preguntas?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("20 preguntas", callback_data="exam_20")],
            [InlineKeyboardButton("50 preguntas", callback_data="exam_50")],
            [InlineKeyboardButton("100 preguntas", callback_data="exam_100")],
        ]))


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
        await update.message.reply_text(f"🚫 `{uid}` desactivado.", parse_mode="Markdown")


async def cmd_clientes(update, ctx):
    if update.effective_user.id != ADMIN_ID:
        return
    clients = load_clients()
    if not clients:
        await update.message.reply_text("No hay clientes registrados aún.")
        return
    lines = []
    for uid, info in clients.items():
        if info.get("active"):
            estado = "✅ Activo"
        else:
            used = info.get("trial_questions", 0)
            estado = f"🆓 Prueba ({used}/{FREE_QUESTIONS} preguntas)"
        lines.append(f"{estado}\n  └ {info.get('name','?')} — `{uid}`")
    await update.message.reply_text(
        f"👥 *Clientes* ({len(clients)} total)\n\n" + "\n\n".join(lines),
        parse_mode="Markdown")


async def cmd_miid(update, ctx):
    await update.message.reply_text(f"🆔 Tu ID: `{update.effective_user.id}`", parse_mode="Markdown")


# ── PDF handler ───────────────────────────────────────────────────────────────

async def handle_pdf(update, ctx):
    if not await check_access(update):
        return
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
            return
        ctx.user_data.clear()
        ctx.user_data["chapters"] = chapters
        ctx.user_data["pdf_name"] = update.message.document.file_name
        ctx.user_data["current_q"] = None
        ctx.user_data["waiting_open"] = False
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
            f"❓ *{len(all_questions)} preguntas generadas*\n\n¿Qué quieres hacer?",
            parse_mode="Markdown", reply_markup=main_menu_keyboard())
    except Exception as e:
        logger.error(f"Error PDF: {e}")
        await msg.edit_text(f"❌ Error: {str(e)}")


# ── Callback handlers ─────────────────────────────────────────────────────────

async def handle_callback(update, ctx):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if data == "mode_quick":
        questions = ctx.user_data.get("questions", [])
        if not questions:
            await query.message.reply_text("📎 Primero sube un PDF con /nuevo")
            return
        queue = spaced_rep.get_study_queue(user_id, questions, limit=10)
        ctx.user_data["quiz_queue"] = queue
        ctx.user_data["quiz_index"] = 0
        ctx.user_data["quiz_correct"] = 0
        ctx.user_data["quiz_total"] = len(queue)
        ctx.user_data["waiting_open"] = False
        await query.message.reply_text("🎯 *Práctica rápida — 10 preguntas*", parse_mode="Markdown")
        await send_question(ctx, chat_id, ctx.user_data, user_id)

    elif data == "mode_exam":
        await query.message.reply_text(
            "📝 ¿Cuántas preguntas?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("20 preguntas", callback_data="exam_20")],
                [InlineKeyboardButton("50 preguntas", callback_data="exam_50")],
                [InlineKeyboardButton("100 preguntas", callback_data="exam_100")],
            ]))

    elif data.startswith("exam_"):
        n = {"exam_20": 20, "exam_50": 50, "exam_100": 100}.get(data, 20)
        questions = ctx.user_data.get("questions", [])
        if not questions:
            await query.message.reply_text("📎 Primero sube un PDF con /nuevo")
            return
        queue = spaced_rep.get_study_queue(user_id, questions, limit=n)
        ctx.user_data["quiz_queue"] = queue
        ctx.user_data["quiz_index"] = 0
        ctx.user_data["quiz_correct"] = 0
        ctx.user_data["quiz_total"] = len(queue)
        ctx.user_data["waiting_open"] = False
        await query.message.reply_text(f"📝 *Simulacro de {len(queue)} preguntas*", parse_mode="Markdown")
        await send_question(ctx, chat_id, ctx.user_data, user_id)

    elif data == "mode_review":
        questions = ctx.user_data.get("questions", [])
        failed = progress_tracker.get_failed_questions(user_id, questions)
        if not failed:
            await query.message.reply_text("🎉 ¡No tienes fallos todavía!", reply_markup=main_menu_keyboard())
            return
        ctx.user_data["quiz_queue"] = failed[:20]
        ctx.user_data["quiz_index"] = 0
        ctx.user_data["quiz_correct"] = 0
        ctx.user_data["quiz_total"] = len(failed[:20])
        ctx.user_data["waiting_open"] = False
        await query.message.reply_text(f"🔁 *Repaso — {len(failed[:20])} preguntas*", parse_mode="Markdown")
        await send_question(ctx, chat_id, ctx.user_data, user_id)

    elif data == "mode_chapter":
        chapters = ctx.user_data.get("chapters", [])
        if not chapters:
            await query.message.reply_text("📎 Primero sube un PDF con /nuevo")
            return
        keyboard = [[InlineKeyboardButton(f"📖 {ch['title'][:40]}", callback_data=f"chapter_{i}")]
                    for i, ch in enumerate(chapters)]
        await query.message.reply_text("📚 Selecciona un capítulo:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("chapter_"):
        idx = int(data.split("_")[1])
        all_q = ctx.user_data.get("questions", [])
        ch_qs = [q for q in all_q if q.get("chapter_idx") == idx][:20]
        ctx.user_data["quiz_queue"] = ch_qs
        ctx.user_data["quiz_index"] = 0
        ctx.user_data["quiz_correct"] = 0
        ctx.user_data["quiz_total"] = len(ch_qs)
        ctx.user_data["waiting_open"] = False
        chapters = ctx.user_data.get("chapters", [])
        title = chapters[idx]["title"] if idx < len(chapters) else "Capítulo"
        await query.message.reply_text(f"📖 *{title}*\n{len(ch_qs)} preguntas", parse_mode="Markdown")
        await send_question(ctx, chat_id, ctx.user_data, user_id)

    elif data == "show_progress":
        stats = progress_tracker.get_stats(user_id)
        await query.message.reply_text(format_progress(stats), parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="back_menu")]]))

    elif data == "gen_more":
        chapters = ctx.user_data.get("chapters", [])
        if not chapters:
            await query.message.reply_text("📎 No hay PDF cargado. Usa /nuevo para subir uno.")
            return
        msg = await query.message.reply_text("🤖 Generando nuevas preguntas...")
        new_questions = []
        existing = ctx.user_data.get("questions", [])
        existing_texts = {q.get("question", "") for q in existing}
        for i, chapter in enumerate(chapters):
            await msg.edit_text(f"🤖 Generando nuevas preguntas... capítulo {i+1}/{len(chapters)}")
            # Pasamos las preguntas existentes para que no las repita
            chapter["_idx"] = i
            chapter["_existing"] = list(existing_texts)
            questions = await question_gen.generate_questions(chapter)
            # Filtrar duplicados
            for q in questions:
                if q.get("question") not in existing_texts:
                    new_questions.append(q)
                    existing_texts.add(q.get("question", ""))
        if not new_questions:
            await msg.edit_text("⚠️ No pude generar preguntas nuevas. Intenta más tarde.")
            return
        ctx.user_data["questions"] = existing + new_questions
        progress_tracker.init_user(user_id, ctx.user_data["questions"])
        await msg.edit_text(
            f"✅ *¡{len(new_questions)} preguntas nuevas generadas!*\n\n"
            f"📊 Total ahora: *{len(ctx.user_data['questions'])} preguntas*\n\n"
            "¿Qué quieres hacer?",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard())

    elif data == "back_menu":
        await query.message.reply_text("¿Qué quieres hacer?", reply_markup=main_menu_keyboard())

    elif data.startswith("ans_"):
        answer = data.replace("ans_", "")
        question = ctx.user_data.get("current_q")
        if not question:
            return
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
        # Contar pregunta de prueba
        if not is_active(user_id) and user_id != ADMIN_ID:
            increment_trial_questions(user_id)
        await query.message.reply_text(result_text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("➡️ Siguiente", callback_data="next_question"),
                InlineKeyboardButton("💬 Explicar", callback_data="explain_current")
            ]]))

    elif data == "next_question":
        ctx.user_data["waiting_open"] = False
        await send_question(ctx, chat_id, ctx.user_data, user_id)

    elif data == "explain_current":
        question = ctx.user_data.get("current_q")
        if not question:
            return
        explanation = await question_gen.get_detailed_explanation(question)
        await query.message.reply_text(f"💡 *Explicación*\n\n{explanation}", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Siguiente", callback_data="next_question")]]))

    elif data.startswith("summary_"):
        idx = int(data.split("_")[1])
        chapters = ctx.user_data.get("chapters", [])
        if idx >= len(chapters):
            return
        chapter = chapters[idx]
        msg = await query.message.reply_text("📝 Generando resumen...")
        summary = await question_gen.summarize_chapter(chapter)
        await msg.edit_text(f"📖 *{chapter['title']}*\n\n{summary}", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="back_menu")]]))


async def handle_text(update, ctx):
    if not ctx.user_data.get("waiting_open"):
        return
    ctx.user_data["waiting_open"] = False
    user_id = update.effective_user.id
    question = ctx.user_data.get("current_q")
    if not question:
        return
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
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Siguiente", callback_data="next_question")]]))


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Falta TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("nuevo", cmd_nuevo))
    app.add_handler(CommandHandler("progreso", cmd_progreso))
    app.add_handler(CommandHandler("explicar", cmd_explicar))
    app.add_handler(CommandHandler("resumen", cmd_resumen))
    app.add_handler(CommandHandler("repasar", cmd_repasar))
    app.add_handler(CommandHandler("simulacro", cmd_simulacro))
    app.add_handler(CommandHandler("activar", cmd_activar))
    app.add_handler(CommandHandler("desactivar", cmd_desactivar))
    app.add_handler(CommandHandler("clientes", cmd_clientes))
    app.add_handler(CommandHandler("miid", cmd_miid))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("🤖 StudyBot corriendo...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
