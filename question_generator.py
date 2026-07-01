"""
question_generator.py — Genera preguntas con la API de OpenAI (GPT-4o mini)
"""

import os
import json
import uuid
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

OPENAI_MODEL = "gpt-4o-mini"   # económico y muy capaz
client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


class QuestionGenerator:

    async def _ask(self, prompt: str, max_tokens: int = 4000) -> str:
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content.strip()

    async def generate_questions(self, chapter: dict) -> list[dict]:
        existing = chapter.get("_existing", [])
        avoid_text = ""
        if existing:
            sample = existing[:10]
            avoid_text = f"\n\nIMPORTANTE: Ya existen estas preguntas, NO las repitas:\n" + "\n".join(f"- {q}" for q in sample)

        text = chapter['text'][:8000]

        prompt = f"""Eres un profesor experto en cualquier área (ingeniería, medicina, aviación, derecho, etc).
Analiza el siguiente texto y genera exactamente 15 preguntas de estudio.

IMPORTANTE: Si el texto es técnico o contiene términos especializados, genera preguntas sobre:
- Definiciones y funciones de los componentes mencionados
- Procedimientos o pasos descritos
- Características técnicas específicas
- Relaciones entre componentes o sistemas
- Propósito o uso de cada elemento

CAPÍTULO: {chapter['title']}

TEXTO:
{text}{avoid_text}

Genera exactamente:
- 9 preguntas de opción múltiple (tipo "multiple_choice")
- 3 preguntas de verdadero/falso (tipo "true_false")
- 3 preguntas abiertas (tipo "open")

Si el texto tiene poco contenido, genera preguntas con la información disponible aunque sean pocas.
Responde ÚNICAMENTE con JSON válido, sin texto adicional ni backticks:

{{
  "questions": [
    {{
      "type": "multiple_choice",
      "question": "¿Pregunta aquí?",
      "options": {{"A": "opción1", "B": "opción2", "C": "opción3", "D": "opción4"}},
      "answer": "A",
      "explanation": "Explicación breve"
    }},
    {{
      "type": "true_false",
      "question": "Afirmación a evaluar",
      "options": {{"Verdadero": "Verdadero", "Falso": "Falso"}},
      "answer": "Verdadero",
      "explanation": "Explicación breve"
    }},
    {{
      "type": "open",
      "question": "¿Pregunta abierta?",
      "options": {{}},
      "answer": "Respuesta modelo completa",
      "explanation": "Puntos clave"
    }}
  ]
}}"""

        for attempt in range(3):  # hasta 3 intentos
            try:
                raw  = await self._ask(prompt, max_tokens=4000)
                raw  = raw.replace("```json", "").replace("```", "").strip()
                # Intentar encontrar JSON aunque haya texto extra
                start = raw.find("{")
                end   = raw.rfind("}") + 1
                if start >= 0 and end > start:
                    raw = raw[start:end]
                data = json.loads(raw)
                questions = data.get("questions", [])
                if questions:
                    chapter_idx = chapter.get("_idx", 0)
                    return [
                        {**q, "id": str(uuid.uuid4()), "chapter_idx": chapter_idx, "chapter": chapter["title"]}
                        for q in questions
                    ]
                logger.warning(f"Intento {attempt+1}: JSON vacío, reintentando...")
            except json.JSONDecodeError as e:
                logger.error(f"Intento {attempt+1} - Error JSON: {e}\nRaw: {raw[:200]}")
            except Exception as e:
                logger.error(f"Intento {attempt+1} - Error: {e}")
        return []

    async def evaluate_open_answer(self, question: str, ideal_answer: str, user_answer: str) -> dict:
        prompt = f"""Evalúa la respuesta del estudiante.

PREGUNTA: {question}
RESPUESTA IDEAL: {ideal_answer}
RESPUESTA DEL ESTUDIANTE: {user_answer}

Devuelve ÚNICAMENTE JSON válido:
{{"score": <0-100>, "feedback": "<retroalimentación en 2-3 oraciones>"}}"""

        try:
            raw  = await self._ask(prompt, max_tokens=300)
            raw  = raw.replace("```json", "").replace("```", "").strip()
            data = json.loads(raw)
            return {"score": data.get("score", 50), "feedback": data.get("feedback", "")}
        except Exception:
            return {"score": 50, "feedback": "No se pudo evaluar automáticamente."}

    async def get_detailed_explanation(self, question: dict) -> str:
        prompt = f"""Eres un profesor. Explica en detalle esta pregunta del capítulo "{question.get('chapter', '')}".

PREGUNTA: {question.get('question', '')}
RESPUESTA CORRECTA: {question.get('answer', '')}
EXPLICACIÓN BASE: {question.get('explanation', '')}

1. Explica el concepto clave claramente
2. Por qué las otras opciones son incorrectas (si aplica)
3. Da un ejemplo práctico si es útil
Máximo 250 palabras, tono amigable."""

        try:
            return await self._ask(prompt, max_tokens=500)
        except Exception:
            return question.get("explanation", "No hay explicación disponible.")

    async def summarize_chapter(self, chapter: dict) -> str:
        prompt = f"""Genera un resumen estructurado de este capítulo para un estudiante.

CAPÍTULO: {chapter['title']}
TEXTO: {chapter['text'][:6000]}

Incluye:
1. 🎯 Idea principal (2-3 oraciones)
2. 📌 Puntos clave (4-6 puntos)
3. 💡 Conceptos importantes
4. 🔗 Conexión con otros temas (si aplica)

Máximo 400 palabras."""

        try:
            return await self._ask(prompt, max_tokens=700)
        except Exception:
            return "No se pudo generar el resumen."
