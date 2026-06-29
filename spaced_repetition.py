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
            data[question_id] = {"interval": 1, "ease": 2.5, "repetitions": 0, "next_review": now, "last_reviewed": now}
        rec = data[question_id]
