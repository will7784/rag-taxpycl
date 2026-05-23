"""
Bot de Telegram simplificado — Taxpy Writer.

Sin cuotas, sin invites, sin RAG local.
Solo NotebookLM + GPT-4o para escribir manuales, artículos y guiones.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from rich.console import Console

import config
import exporter
from voice_processor import VoiceProcessor
from writer import WriterEngine

console = Console()


def _sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^a-zA-Z0-9\-_]+", "_", name).strip("_")
    return name or "documento"


class WriterTelegramBot:
    def __init__(self, token: str) -> None:
        self.token = token
        self.writer = WriterEngine()
        self.voice = VoiceProcessor() if config.GOOGLE_API_KEY else None
        # Sesiones en memoria: chat_id -> {"title": str, "content": str, "type": str}
        self._sessions: dict[int, dict] = {}

    # ── Comandos ──────────────────────────────────────────────

    async def _start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        text = (
            "🤖 *Taxpy Writer*\n\n"
            "Escribo manuales, artículos y guiones de video sobre "
            "derecho tributario chileno usando todo el conocimiento "
            "de mi cuaderno NotebookLM.\n\n"
            "*Comandos:*\n"
            "• /manual `<tema>` — manual completo con capítulos\n"
            "• /articulo `<tema>` — artículo editorial largo\n"
            "• /guion `<tema>` — guion de video con escenas y planos\n"
            "• /voz `on` / `off` — activa respuestas de voz\n\n"
            "También puedes escribirme directamente o mandarme un *audio* 🎙️"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _manual(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        topic = " ".join(context.args or []).strip()
        if not topic:
            await update.message.reply_text("Usa: /manual `<tema a desarrollar>`")
            return
        await self._process_request(update, topic, "manual")

    async def _articulo(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        topic = " ".join(context.args or []).strip()
        if not topic:
            await update.message.reply_text("Usa: /articulo `<tema a desarrollar>`")
            return
        await self._process_request(update, topic, "articulo")

    async def _guion(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        topic = " ".join(context.args or []).strip()
        if not topic:
            await update.message.reply_text("Usa: /guion `<tema a desarrollar>`")
            return
        await self._process_request(update, topic, "guion")

    async def _voz(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat_id = int(update.effective_chat.id)
        arg = (context.args[0] if context.args else "").strip().lower()

        if arg in ("on", "1", "true", "si", "sí"):
            self._sessions.setdefault(chat_id, {})["voice_enabled"] = True
            await update.message.reply_text("🎙️ Modo voz *ACTIVADO*.")
        elif arg in ("off", "0", "false", "no"):
            self._sessions.setdefault(chat_id, {})["voice_enabled"] = False
            await update.message.reply_text("📝 Modo voz *DESACTIVADO*.")
        else:
            enabled = self._sessions.get(chat_id, {}).get("voice_enabled", False)
            status = "ACTIVADO" if enabled else "DESACTIVADO"
            await update.message.reply_text(
                f"🎙️ Modo voz está *{status}*.\n\n"
                "Usa /voz on  para activar\n"
                "Usa /voz off para desactivar",
                parse_mode="Markdown",
            )

    # ── Procesamiento central ─────────────────────────────────

    async def _process_request(
        self,
        update: Update,
        topic: str,
        content_type: Optional[str] = None,
    ) -> None:
        chat_id = int(update.effective_chat.id)
        detected = content_type or self.writer.detect_content_type(topic)

        # 1. Research
        await update.message.chat.send_action(action="typing")
        await update.message.reply_text(
            f"🔍 Investigando *{topic}* en NotebookLM...",
            parse_mode="Markdown",
        )
        try:
            research = await self.writer.research(topic)
        except Exception as e:
            console.print(f"[red]Research error: {e}[/red]")
            research = ""

        # 2. Write
        await update.message.chat.send_action(action="typing")
        status_msg = await update.message.reply_text(
            f"✍️ Escribiendo tu *{detected}*... esto puede tardar unos segundos.",
            parse_mode="Markdown",
        )

        try:
            content = await self.writer.write(topic, research, detected)
        except Exception as e:
            console.print(f"[red]Write error: {e}[/red]")
            await status_msg.edit_text(
                "❌ Ocurrió un error escribiendo el contenido. Intenta de nuevo."
            )
            return

        # Guardar sesión
        self._sessions[chat_id] = {
            "title": topic,
            "content": content,
            "type": detected,
            "voice_enabled": self._sessions.get(chat_id, {}).get("voice_enabled", False),
        }

        await status_msg.delete()

        # 3. Enviar texto partido
        chunks = self.writer.split_for_telegram(content)
        for chunk in chunks:
            await update.message.reply_text(chunk)

        # 4. Botones de descarga
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📄 Descargar .md", callback_data="dl_md"),
                    InlineKeyboardButton("📝 Descargar .docx", callback_data="dl_docx"),
                ]
            ]
        )
        await update.message.reply_text(
            "¿Quieres guardarlo?", reply_markup=keyboard
        )

        # 5. Voz si está activada
        if self._sessions[chat_id].get("voice_enabled") and self.voice:
            await update.message.chat.send_action(action="upload_voice")
            try:
                voice_bytes = await self.voice.synthesize(content[:3800])
                await update.message.reply_voice(
                    voice=voice_bytes,
                    caption="🎙️ Resumen de voz",
                )
            except Exception as e:
                console.print(f"[yellow]TTS falló: {e}[/yellow]")

    # ── Handlers de mensajes ──────────────────────────────────

    async def _handle_text(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        text_override: Optional[str] = None,
    ) -> None:
        if not update.message:
            return
        text = (text_override or update.message.text or "").strip()
        if not text:
            return
        detected = self.writer.detect_content_type(text)
        await self._process_request(update, text, detected)

    async def _handle_voice(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not update.message or not update.message.voice:
            return
        if not self.voice:
            await update.message.reply_text(
                "🎙️ El procesamiento de voz no está configurado."
            )
            return

        await update.message.chat.send_action(action="typing")
        try:
            voice_file = await update.message.voice.get_file()
            voice_bytes = await voice_file.download_as_bytearray()
            transcript = await self.voice.transcribe(bytes(voice_bytes))
            if not transcript:
                await update.message.reply_text(
                    "🎙️ No pude entender el audio. Intenta hablar más claro."
                )
                return
            await update.message.reply_text(f'🎙️ Entendí: "{transcript}"')
            await self._handle_text(update, context, text_override=transcript)
        except Exception as e:
            console.print(f"[red]Voice handler error: {e}[/red]")
            await update.message.reply_text(
                "Ocurrió un error procesando el audio. Intenta con texto."
            )

    # ── Callbacks (descargas) ─────────────────────────────────

    async def _handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
        chat_id = int(query.message.chat.id)
        session = self._sessions.get(chat_id)

        if not session or not session.get("content"):
            await query.edit_message_text("El contenido expiró. Genera uno nuevo.")
            return

        title = session["title"]
        content = session["content"]

        if query.data == "dl_md":
            data = exporter.to_markdown(content, title)
            filename = f"{_sanitize_filename(title)}.md"
            mime = "text/markdown"
        elif query.data == "dl_docx":
            data = exporter.to_docx(content, title)
            filename = f"{_sanitize_filename(title)}.docx"
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            return

        await query.message.reply_document(
            document=data,
            filename=filename,
            caption=f"✅ Aquí tienes tu *{session['type']}*.",
            parse_mode="Markdown",
        )

    # ── Run ───────────────────────────────────────────────────

    def run(self) -> None:
        app = Application.builder().token(self.token).build()

        app.add_handler(CommandHandler("start", self._start))
        app.add_handler(CommandHandler("manual", self._manual))
        app.add_handler(CommandHandler("articulo", self._articulo))
        app.add_handler(CommandHandler("guion", self._guion))
        app.add_handler(CommandHandler("voz", self._voz))
        app.add_handler(CallbackQueryHandler(self._handle_callback))
        app.add_handler(MessageHandler(filters.VOICE, self._handle_voice))
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text)
        )

        voice_status = "🎙️ voz" if self.voice else "📝 solo texto"
        console.print(
            "[green]✅ Taxpy Writer Bot iniciado[/green]\n"
            f"[dim]NotebookLM: {config.NOTEBOOKLM_NOTEBOOK_NAME}[/dim]\n"
            f"[dim]LLM: {config.OPENAI_MODEL} | {voice_status}[/dim]"
        )
        app.run_polling(allowed_updates=Update.ALL_TYPES)
