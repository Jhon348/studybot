import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)


class ProgressTracker:

    def _path(self, user_id):
        return DATA_DIR / f"progress_{user_id}.json"

    def _load(self, user_id):
        p = self._path(user_id)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"answers": {}, "total": 0, "correct": 0, "failed": 0, "streak": 0, "max_streak": 0, "sessions": 0, "last_session": None}

    def _save(self, user_id, data):
        try:
            self._path(user_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Error guardando progreso: {e}")

    def init_user(self, user_id, questions):
        data = self._load(user_id)
        for q in questions:
            if q["id"] not in data["answers"]:
                data["answers"][q["id"]] = {"correct": 0, "wrong": 0, "last_seen": None}
        data["sessions"] = data.get("sessions", 0) + 1
        data["last_session"] = datetime.now().isoformat()
        self._save(user_id, data)

    def record_answer(self, user_id, question_id, is_correct):
        data = self._load(user_id)
        if question_id not in data["answers"]:
            data["answers"][question_id] = {"correct": 0, "wrong": 0, "last_seen": None}
        rec = data["answers"][question_id]
        rec["last_seen"] = datetime.now().isoformat()
        data["total"] = data.get("total", 0) + 1
        if is_correct:
            rec["correct"] = rec.get("correct", 0) + 1
            data["correct"] = data.get("correct", 0) + 1
            data["streak"] = data.get("streak", 0) + 1
            data["max_streak"] = max(data.get("max_streak", 0), data["streak"])
        else:
            rec["wrong"] = rec.get("wrong", 0) + 1
            data["failed"] = data.get("failed", 0) + 1
            data["streak"] = 0
        self._save(user_id, data)

    def get_failed_questions(self, user_id, questions):
        data = self._load(user_id)
        answers = data.get("answers", {})
        failed = [(answers.get(q["id"], {}).get("wrong", 0), q) for q in questions if answers.get(q["id"], {}).get("wrong", 0) > 0]
        failed.sort(key=lambda x: x[0], reverse=True)
        return [q for _, q in failed]

    def get_stats(self, user_id):
        data = self._load(user_id)
        failed_ids = sum(1 for rec in data.get("answers", {}).values() if rec.get("wrong", 0) > rec.get("correct", 0))
        return {"total": data.get("total", 0), "correct": data.get("correct", 0), "failed": data.get("failed", 0), "streak": data.get("streak", 0), "to_review": failed_ids, "sessions": data.get("sessions", 0)}
