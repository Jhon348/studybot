import os
import json
import uuid
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)
OPENAI_MODEL = "gpt-4o-mini"
client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


class QuestionGenerator:

    async def _ask(self, prompt, max_tokens=4000):
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content.strip()

    async def generate_questions(self, chapter):
        prompt = f"""Eres un profesor experto. Genera exactamente 15 preguntas de estudio.

CAPÍTULO: {chapter['title']}
TEXTO: {chapter['text'][:8000]}

Genera:
- 9 preguntas de opción múltiple (multiple_choice)
- 3 verdadero/falso (true_false)
- 3 abiertas (open)

Responde ÚNICAMENTE con JSON válido sin backticks:
{{"questions": [
  {{"type": "multiple_choice", "question": "?", "options": {{"A": "op1", "B": "op2", "C": "op3", "D": "op4"}}, "answer": "A", "explanation": "..."}},
  {{"type": "true_false", "question": "afirmación", "options": {{"Verdadero": "Verdadero", "Falso": "Falso"}}, "answer": "Verdadero", "explanation": "..."}},
  {{"type": "open", "question": "?", "options": {{}}, "answer": "respuesta modelo", "explanation": "..."}}
]}}"""
        try:
            raw = await self._ask(prompt)
            raw = raw.replace("```json", "").replace("```", "").strip()
            data = json.loads(raw)
            idx = chapter.get("_idx", 0)
            return [{**q, "id": str(uuid.uuid4()), "chapter_idx": idx, "chapter": chapter["title"]} for q in data.get("questions", [])]
        except Exception as e:
            logger.error(f"Error generando preguntas: {e}")
            return []

    async def evaluate_open_answer(self, question, ideal_answer, user_answer):
        prompt = f"""Evalúa la respuesta.
PREGUNTA: {question}
RESPUESTA IDEAL: {ideal_answer}
RESPUESTA ESTUDIANTE: {user_answer}
Devuelve SOLO JSON: {{"score": <0-100>, "feedback": "<2-3 oraciones>"}}"""
        try:
            raw = await self._ask(prompt, max_tokens=300)
            raw = raw.replace("```json", "").replace("```", "").strip()
            data = json.loads(raw)
            return {"score": data.get("score", 50), "feedback": data.get("feedback", "")}
        except Exception:
            return {"score": 50, "feedback": "No se pudo evaluar automáticamente."}

    async def get_detailed_explanation(self, question):
        prompt = f"""Explica esta pregunta del capítulo "{question.get('chapter', '')}".
PREGUNTA: {question.get('question', '')}
RESPUESTA: {question.get('answer', '')}
EXPLICACIÓN BASE: {question.get('explanation', '')}
Máximo 250 palabras, tono amigable y didáctico."""
        try:
            return await self._ask(prompt, max_tokens=500)
        except Exception:
            return question.get("explanation", "Sin explicación disponible.")

    async def summarize_chapter(self, chapter):
        prompt = f"""Resume este capítulo para un estudiante.
CAPÍTULO: {chapter['title']}
TEXTO: {chapter['text'][:6000]}
Incluye: 🎯 Idea principal, 📌 Puntos clave, 💡 Conceptos importantes. Máximo 400 palabras."""
        try:
            return await self._ask(prompt, max_tokens=700)
        except Exception:
            return "No se pudo generar el resumen."
