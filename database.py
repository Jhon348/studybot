"""
database.py — Conexión persistente con Supabase
Reemplaza los archivos JSON locales para que los datos no se pierdan al reiniciar.
"""

import os
import logging
from supabase import create_client, Client

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

_client: Client = None


def get_db() -> Client:
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("Faltan SUPABASE_URL o SUPABASE_KEY")
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


# ── CLIENTES ──────────────────────────────────────────────────────────────────

def db_get_client(user_id: int) -> dict | None:
    try:
        res = get_db().table("clients").select("*").eq("user_id", user_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"db_get_client error: {e}")
        return None


def db_upsert_client(user_id: int, name: str = "Cliente", active: bool = False, trial_questions: int = 0):
    try:
        existing = db_get_client(user_id)
        if existing:
            get_db().table("clients").update({
                "name": name,
                "active": active,
                "trial_questions": trial_questions
            }).eq("user_id", user_id).execute()
        else:
            get_db().table("clients").insert({
                "user_id": user_id,
                "name": name,
                "active": active,
                "trial_questions": trial_questions
            }).execute()
    except Exception as e:
        logger.error(f"db_upsert_client error: {e}")


def db_set_active(user_id: int, active: bool, name: str = None, days: int = 30):
    from datetime import datetime, timedelta
    try:
        existing = db_get_client(user_id)
        expires_at = (datetime.utcnow() + timedelta(days=days)).isoformat() if active else None
        if existing:
            update = {"active": active, "notified_expiry": False}
            if name:
                update["name"] = name
            if active:
                update["expires_at"] = expires_at
            get_db().table("clients").update(update).eq("user_id", user_id).execute()
        else:
            get_db().table("clients").insert({
                "user_id": user_id,
                "name": name or "Cliente",
                "active": active,
                "trial_questions": 0,
                "expires_at": expires_at,
                "notified_expiry": False
            }).execute()
    except Exception as e:
        logger.error(f"db_set_active error: {e}")


def db_increment_trial(user_id: int):
    try:
        existing = db_get_client(user_id)
        if existing:
            new_count = existing.get("trial_questions", 0) + 1
            get_db().table("clients").update({"trial_questions": new_count}).eq("user_id", user_id).execute()
        else:
            get_db().table("clients").insert({
                "user_id": user_id,
                "name": "Trial",
                "active": False,
                "trial_questions": 1
            }).execute()
    except Exception as e:
        logger.error(f"db_increment_trial error: {e}")


def db_get_all_clients() -> list[dict]:
    try:
        res = get_db().table("clients").select("*").order("created_at", desc=True).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"db_get_all_clients error: {e}")
        return []


# ── PROGRESO ──────────────────────────────────────────────────────────────────

def db_record_answer(user_id: int, question_id: str, is_correct: bool):
    try:
        res = get_db().table("progress").select("*").eq("user_id", user_id).eq("question_id", question_id).execute()
        if res.data:
            rec = res.data[0]
            if is_correct:
                get_db().table("progress").update({"correct": rec["correct"] + 1}).eq("user_id", user_id).eq("question_id", question_id).execute()
            else:
                get_db().table("progress").update({"wrong": rec["wrong"] + 1}).eq("user_id", user_id).eq("question_id", question_id).execute()
        else:
            get_db().table("progress").insert({
                "user_id": user_id,
                "question_id": question_id,
                "correct": 1 if is_correct else 0,
                "wrong": 0 if is_correct else 1
            }).execute()
    except Exception as e:
        logger.error(f"db_record_answer error: {e}")


def db_get_progress(user_id: int) -> list[dict]:
    try:
        res = get_db().table("progress").select("*").eq("user_id", user_id).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"db_get_progress error: {e}")
        return []


def db_get_stats(user_id: int) -> dict:
    try:
        progress = db_get_progress(user_id)
        total_correct = sum(p["correct"] for p in progress)
        total_wrong = sum(p["wrong"] for p in progress)
        total = total_correct + total_wrong
        failed_ids = sum(1 for p in progress if p["wrong"] > p["correct"])
        return {
            "total": total,
            "correct": total_correct,
            "failed": total_wrong,
            "streak": 0,
            "to_review": failed_ids,
            "sessions": 1
        }
    except Exception as e:
        logger.error(f"db_get_stats error: {e}")
        return {"total": 0, "correct": 0, "failed": 0, "streak": 0, "to_review": 0}


def db_get_failed_questions(user_id: int, questions: list[dict]) -> list[dict]:
    try:
        progress = db_get_progress(user_id)
        failed_ids = {p["question_id"] for p in progress if p["wrong"] > p["correct"]}
        failed = [(next((p["wrong"] for p in progress if p["question_id"] == q["id"]), 0), q)
                  for q in questions if q.get("id") in failed_ids]
        failed.sort(key=lambda x: x[0], reverse=True)
        return [q for _, q in failed]
    except Exception as e:
        logger.error(f"db_get_failed_questions error: {e}")
        return []

def db_get_expiring_clients() -> list[dict]:
    """Devuelve clientes activos cuya suscripción vence hoy o ya venció y no han sido notificados."""
    from datetime import datetime, timedelta
    try:
        tomorrow = (datetime.utcnow() + timedelta(days=1)).isoformat()
        res = (get_db().table("clients")
               .select("*")
               .eq("active", True)
               .eq("notified_expiry", False)
               .lte("expires_at", tomorrow)
               .execute())
        return res.data or []
    except Exception as e:
        logger.error(f"db_get_expiring_clients error: {e}")
        return []


def db_mark_notified(user_id: int):
    try:
        get_db().table("clients").update({"notified_expiry": True}).eq("user_id", user_id).execute()
    except Exception as e:
        logger.error(f"db_mark_notified error: {e}")


def db_deactivate_expired():
    """Desactiva clientes cuya suscripción ya venció."""
    from datetime import datetime
    try:
        now = datetime.utcnow().isoformat()
        res = (get_db().table("clients")
               .select("user_id")
               .eq("active", True)
               .lt("expires_at", now)
               .execute())
        for c in (res.data or []):
            get_db().table("clients").update({"active": False}).eq("user_id", c["user_id"]).execute()
        return len(res.data or [])
    except Exception as e:
        logger.error(f"db_deactivate_expired error: {e}")
        return 0
