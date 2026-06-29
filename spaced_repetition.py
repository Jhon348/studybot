import json
import random
import logging
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)


class SpacedRepetition:

    def _path(self, user_id):
        return DATA_DIR / f"srs_{user_id}.json"

    def _load(self, user_id):
        p = self._path(user_id)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save(self, user_id, data):
        try:
            self._path(user_id).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"Error guardando SRS: {e}")

    def update(self, user_id, question_id, is_correct):
        data = self._load(user_id)
        now = datetime.now().isoformat()
        if question_id not in data:
            data[question_id] = {
                "interval": 1, "ease": 2.5,
                "repetitions": 0, "next_review": now, "last_reviewed": now
            }
        rec = data[question_id]
        rec["last_reviewed"] = now
        if is_correct:
            rec["repetitions"] = rec.get("repetitions", 0) + 1
            reps = rec["repetitions"]
            if reps == 1:
                rec["interval"] = 1
            elif reps == 2:
                rec["interval"] = 6
            else:
                rec["interval"] = round(rec.get("interval", 1) * rec.get("ease", 2.5))
            rec["interval"] = min(rec["interval"], 30)
            rec["ease"] = max(1.3, rec.get("ease", 2.5) + 0.1)
        else:
            rec["repetitions"] = 0
            rec["interval"] = 1
            rec["ease"] = max(1.3, rec.get("ease", 2.5) - 0.2)
        rec["next_review"] = (datetime.now() + timedelta(days=rec["interval"])).isoformat()
        data[question_id] = rec
        self._save(user_id, data)

    def get_study_queue(self, user_id, questions, limit=20):
        try:
            if not questions:
                return []
            result = list(questions)
            random.shuffle(result)
            return result[:limit]
        except Exception as e:
            logger.error(f"Error en get_study_queue: {e}")
            return list(questions)[:limit]

    def get_weak_questions(self, user_id, questions):
        try:
            data = self._load(user_id)
            weak = []
            for q in questions:
                qid = q.get("id")
                if qid and qid in data and data[qid].get("ease", 2.5) < 2.0:
                    weak.append((data[qid]["ease"], q))
            weak.sort(key=lambda x: x[0])
            return [q for _, q in weak]
        except Exception as e:
            logger.error(f"Error en get_weak_questions: {e}")
            return []
