"""
Motor de escritura inteligente.

Flujo:
1. Detecta tipo de contenido (manual / artículo / guion).
2. Investiga en NotebookLM (1–3 preguntas estratégicas).
3. Redacta contenido largo con GPT-4o usando la investigación como contexto.
"""

from __future__ import annotations

import re
from typing import Literal

from openai import AsyncOpenAI
from rich.console import Console

import config
from notebooklm_manager import NotebookLMManager

console = Console()

ContentType = Literal["manual", "articulo", "guion"]

_SYSTEM_PROMPTS: dict[ContentType, str] = {
    "manual": (
        "Eres un experto tributario chileno y redactor técnico. "
        "Escribe un manual completo, didáctico y riguroso en tono profesional. "
        "Estructura el contenido en capítulos y subcapítulos numerados. "
        "Incluye definiciones, ejemplos prácticos, casos de aplicación y referencias normativas. "
        "Cita artículos de ley, oficios y circulares del SII cuando corresponda. "
        "Usa formato Markdown (# para capítulos, ## para subcapítulos)."
    ),
    "articulo": (
        "Eres un experto tributario chileno y columnista especializado. "
        "Escribe un artículo largo, editorial y didáctico con rigor técnico. "
        "Estructura: introducción hook, desarrollo con subtemas, conclusión con take-away. "
        "Incluye citas normativas, jurisprudencia y criterios del SII. "
        "Tono: claro, profesional, accesible para contadores y abogados. "
        "Usa formato Markdown (# para título, ## para secciones)."
    ),
    "guion": (
        "Eres un experto tributario chileno y guionista de video educativo. "
        "Escribe un guion completo para video de YouTube/TikTok/Reel sobre el tema solicitado. "
        "Formato obligatorio por escena:\n"
        "ESCENA X\n"
        "PLANO: [plano cinematográfico]\n"
        "DIÁLOGO/VO: [texto a decir]\n"
        "GRÁFICO: [lo que aparece en pantalla]\n\n"
        "Incluye hook inicial, desarrollo didáctico y CTA final. "
        "Tono conversacional pero riguroso. Máximo 3–5 minutos de video."
    ),
}


class WriterEngine:
    def __init__(self) -> None:
        self.nb_manager = NotebookLMManager(
            notebook_name=config.NOTEBOOKLM_NOTEBOOK_NAME
        )
        self._nb_id: str | None = None
        self._openai = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

    async def _ensure_notebook(self) -> str:
        if self._nb_id is None:
            self._nb_id = await self.nb_manager.create_or_get_notebook()
        return self._nb_id

    @staticmethod
    def detect_content_type(prompt: str) -> ContentType:
        p = prompt.lower()
        guion_keywords = [
            "guion", "guión", "video", "youtube", "tiktok", "reel",
            "escena", "plano", "dialogo", "diálogo", "voz en off",
        ]
        manual_keywords = [
            "manual", "guia", "guía", "libro", "capitulo", "capítulo",
            "ebook", "e-book", "compendio", "tratado",
        ]
        if any(k in p for k in guion_keywords):
            return "guion"
        if any(k in p for k in manual_keywords):
            return "manual"
        return "articulo"

    async def research(self, topic: str) -> str:
        """Investiga en NotebookLM y devuelve un contexto consolidado."""
        nb_id = await self._ensure_notebook()
        questions = [
            f"Resume toda la información relevante sobre: {topic}. "
            "Incluye normativa aplicable, jurisprudencia y criterios del SII.",
            f"¿Qué oficios, circulares o resoluciones del SII existen relacionados con: {topic}? "
            "Incluye códigos y fechas si están disponibles.",
            f"¿Cuáles son los puntos clave, errores comunes y mejores prácticas sobre: {topic}?",
        ]
        findings: list[str] = []
        for i, q in enumerate(questions, 1):
            try:
                console.print(f"  [dim]🔍 NotebookLM research {i}/3...[/dim]")
                result = await self.nb_manager.ask_question(nb_id, q)
                answer = result.get("answer", "")
                if answer and len(answer) > 50:
                    findings.append(answer)
            except Exception as e:
                console.print(f"  [yellow]⚠️ Research {i} falló: {e}[/yellow]")
        return "\n\n---\n\n".join(findings) if findings else ""

    async def write(
        self,
        topic: str,
        research_ctx: str,
        content_type: ContentType,
    ) -> str:
        """Genera el contenido completo con GPT-4o."""
        system = _SYSTEM_PROMPTS[content_type]
        user_prompt = (
            f"Tema a desarrollar: {topic}\n\n"
            f"Información de fuentes (NotebookLM):\n{research_ctx}\n\n"
            "Escribe el contenido completo y detallado. "
            "No agregues notas al pie ni disclaimers sobre ser IA. "
            "Solo entrega el contenido profesional listo para publicar."
        )

        console.print(f"  [dim]✍️ GPT-4o redactando ({content_type})...[/dim]")
        response = await self._openai.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            temperature=config.WRITER_TEMPERATURE,
            max_tokens=config.WRITER_MAX_TOKENS,
        )
        return (response.choices[0].message.content or "").strip()

    @staticmethod
    def split_for_telegram(text: str, max_len: int = 4000) -> list[str]:
        """Divide texto largo en chunks respetando párrafos."""
        if len(text) <= max_len:
            return [text]
        chunks: list[str] = []
        current = ""
        for paragraph in text.split("\n\n"):
            if len(current) + len(paragraph) + 2 > max_len:
                if current:
                    chunks.append(current.strip())
                # Si el párrafo solo es más largo que max_len, cortar por líneas
                if len(paragraph) > max_len:
                    lines = paragraph.split("\n")
                    current = ""
                    for line in lines:
                        if len(current) + len(line) + 1 > max_len:
                            chunks.append(current.strip())
                            current = line
                        else:
                            current += "\n" + line if current else line
                else:
                    current = paragraph
            else:
                current += "\n\n" + paragraph if current else paragraph
        if current:
            chunks.append(current.strip())
        return chunks
