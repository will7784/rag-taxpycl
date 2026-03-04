from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from rag_graph import RAGGraph

console = Console()


@dataclass
class TgUser:
    chat_id: int
    username: str
    first_name: str
    plan: str
    month_key: str
    queries_used: int
    free_quota: int
    is_active: int

    @property
    def remaining(self) -> int:
        return max(0, self.free_quota - self.queries_used)


class TelegramUsageStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _month_key(now: Optional[datetime] = None) -> str:
        dt = now or datetime.utcnow()
        return dt.strftime("%Y-%m")

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS telegram_users (
                    chat_id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL DEFAULT '',
                    first_name TEXT NOT NULL DEFAULT '',
                    plan TEXT NOT NULL DEFAULT 'free',
                    month_key TEXT NOT NULL,
                    queries_used INTEGER NOT NULL DEFAULT 0,
                    free_quota INTEGER NOT NULL DEFAULT 10,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    invite_code TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    month_key TEXT NOT NULL,
                    question TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(chat_id) REFERENCES telegram_users(chat_id)
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_chat_month "
                "ON usage_events(chat_id, month_key)"
            )

    def ensure_user(
        self,
        chat_id: int,
        username: str,
        first_name: str,
    ) -> TgUser:
        now_iso = datetime.utcnow().isoformat(timespec="seconds")
        current_month = self._month_key()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM telegram_users WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO telegram_users (
                        chat_id, username, first_name, plan, month_key,
                        queries_used, free_quota, is_active, invite_code,
                        created_at, last_seen_at
                    ) VALUES (?, ?, ?, 'free', ?, 0, ?, 0, '', ?, ?)
                    """,
                    (
                        chat_id,
                        username,
                        first_name,
                        current_month,
                        config.TELEGRAM_FREE_QUERIES_PER_MONTH,
                        now_iso,
                        now_iso,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM telegram_users WHERE chat_id=?",
                    (chat_id,),
                ).fetchone()
            else:
                updates = {
                    "username": username or row["username"],
                    "first_name": first_name or row["first_name"],
                    "last_seen_at": now_iso,
                }
                if row["month_key"] != current_month:
                    updates["month_key"] = current_month
                    updates["queries_used"] = 0
                    updates["free_quota"] = config.TELEGRAM_FREE_QUERIES_PER_MONTH

                set_sql = ", ".join(f"{k}=?" for k in updates.keys())
                conn.execute(
                    f"UPDATE telegram_users SET {set_sql} WHERE chat_id=?",
                    (*updates.values(), chat_id),
                )
                row = conn.execute(
                    "SELECT * FROM telegram_users WHERE chat_id=?",
                    (chat_id,),
                ).fetchone()

        return TgUser(
            chat_id=row["chat_id"],
            username=row["username"],
            first_name=row["first_name"],
            plan=row["plan"],
            month_key=row["month_key"],
            queries_used=row["queries_used"],
            free_quota=row["free_quota"],
            is_active=row["is_active"],
        )

    def set_active(self, chat_id: int, is_active: bool, invite_code: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE telegram_users SET is_active=?, invite_code=? WHERE chat_id=?",
                (1 if is_active else 0, invite_code, chat_id),
            )

    def get_user(self, chat_id: int) -> Optional[TgUser]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM telegram_users WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
        if row is None:
            return None
        return TgUser(
            chat_id=row["chat_id"],
            username=row["username"],
            first_name=row["first_name"],
            plan=row["plan"],
            month_key=row["month_key"],
            queries_used=row["queries_used"],
            free_quota=row["free_quota"],
            is_active=row["is_active"],
        )

    def can_consume_free(self, chat_id: int) -> tuple[bool, int]:
        user = self.get_user(chat_id)
        if user is None:
            return False, 0
        return (user.remaining > 0), user.remaining

    def register_usage(self, chat_id: int, question: str) -> TgUser:
        now_iso = datetime.utcnow().isoformat(timespec="seconds")
        month = self._month_key()
        with self._connect() as conn:
            conn.execute(
                "UPDATE telegram_users SET queries_used=queries_used+1, last_seen_at=? "
                "WHERE chat_id=?",
                (now_iso, chat_id),
            )
            conn.execute(
                """
                INSERT INTO usage_events (chat_id, month_key, question, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (chat_id, month, question[:1500], now_iso),
            )
            row = conn.execute(
                "SELECT * FROM telegram_users WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
        return TgUser(
            chat_id=row["chat_id"],
            username=row["username"],
            first_name=row["first_name"],
            plan=row["plan"],
            month_key=row["month_key"],
            queries_used=row["queries_used"],
            free_quota=row["free_quota"],
            is_active=row["is_active"],
        )


def _build_welcome_text(user: TgUser) -> str:
    return (
        "Hola, soy Taxpy 🤖\n\n"
        "Te ayudo con consultas tributarias chilenas usando normativa y "
        "jurisprudencia.\n\n"
        f"Plan actual: FREE ({user.free_quota} consultas/mes)\n"
        f"Consultas disponibles este mes: {user.remaining}\n\n"
        "Comandos:\n"
        "/saldo - ver tus consultas disponibles\n"
        "/plan - ver plan y upgrade\n"
        "/help - ayuda\n\n"
        "Escribe tu pregunta directamente para comenzar."
    )


def _is_valid_invite(code: str) -> bool:
    if not code:
        return False
    return code in set(config.TELEGRAM_INVITE_CODES)


class TaxpyTelegramBot:
    def __init__(
        self,
        token: str,
        include_derogadas: bool = False,
        top_juris: int = 6,
    ):
        self.token = token
        self.include_derogadas = include_derogadas
        self.top_juris = max(1, min(20, top_juris))
        self.store = TelegramUsageStore(config.TELEGRAM_DB_PATH)
        self.rag = RAGGraph()

    async def _start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat or not update.effective_user:
            return
        chat_id = int(update.effective_chat.id)
        tg_user = update.effective_user
        user = self.store.ensure_user(
            chat_id=chat_id,
            username=tg_user.username or "",
            first_name=tg_user.first_name or "",
        )

        invite_arg = context.args[0].strip() if context.args else ""
        if config.TELEGRAM_REQUIRE_INVITE and not user.is_active:
            if invite_arg and _is_valid_invite(invite_arg):
                self.store.set_active(chat_id, True, invite_arg)
                user = self.store.get_user(chat_id) or user
                await update.message.reply_text(
                    "Acceso beta activado ✅\n\n" + _build_welcome_text(user)
                )
                return
            await update.message.reply_text(
                "Este bot está en beta cerrada.\n"
                "Pide un código de invitación y vuelve a ejecutar:\n"
                "/start TU_CODIGO"
            )
            return

        if not user.is_active:
            self.store.set_active(chat_id, True, invite_arg or "")
            user = self.store.get_user(chat_id) or user

        await update.message.reply_text(_build_welcome_text(user))

    async def _help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Comandos disponibles:\n"
            "/start - iniciar bot\n"
            "/saldo - consultas disponibles del mes\n"
            "/plan - plan actual y upgrade\n"
            "/help - ayuda\n\n"
            "Luego envía tu pregunta tributaria en texto normal."
        )

    async def _saldo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat:
            return
        chat_id = int(update.effective_chat.id)
        user = self.store.ensure_user(
            chat_id=chat_id,
            username=(update.effective_user.username if update.effective_user else ""),
            first_name=(update.effective_user.first_name if update.effective_user else ""),
        )
        if not user.is_active:
            await update.message.reply_text("Primero ejecuta /start para activar tu acceso.")
            return
        await update.message.reply_text(
            f"Tu saldo FREE actual: {user.remaining}/{user.free_quota} consultas disponibles este mes."
        )

    async def _plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            f"Plan FREE: {config.TELEGRAM_FREE_QUERIES_PER_MONTH} consultas/mes.\n"
            f"Plan PRO: USD {config.TELEGRAM_PRO_PLAN_PRICE_USD}/mes (proximamente en taxpy.cl).\n\n"
            "Cuando llegues al limite FREE, te mostraremos el acceso prioritario al plan PRO."
        )

    async def _handle_question(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not update.message or not update.effective_chat:
            return
        chat_id = int(update.effective_chat.id)
        question = (update.message.text or "").strip()
        if not question:
            return

        user = self.store.ensure_user(
            chat_id=chat_id,
            username=(update.effective_user.username if update.effective_user else ""),
            first_name=(update.effective_user.first_name if update.effective_user else ""),
        )
        if not user.is_active:
            await update.message.reply_text("Primero ejecuta /start para activar tu acceso.")
            return

        can_use, remaining = self.store.can_consume_free(chat_id)
        if not can_use:
            await update.message.reply_text(
                f"Alcanzaste el limite FREE de {user.free_quota} consultas este mes.\n"
                f"Plan PRO (USD {config.TELEGRAM_PRO_PLAN_PRICE_USD}) proximamente en taxpy.cl."
            )
            return

        await update.message.chat.send_action(action="typing")
        try:
            result = await asyncio.to_thread(
                self.rag.query,
                question,
                self.include_derogadas,
                self.top_juris,
            )
            answer = (result.get("answer") or "").strip()
            if not answer:
                answer = "No logre generar respuesta en este intento."
            self.store.register_usage(chat_id, question)
            updated = self.store.get_user(chat_id) or user
            suffix = (
                "\n\n"
                f"Consultas restantes este mes: {updated.remaining}/{updated.free_quota}"
            )
            await update.message.reply_text(answer[:3800] + suffix)
        except Exception as e:
            console.print(f"[red]Error Telegram query[/red] chat_id={chat_id} err={e}")
            await update.message.reply_text(
                "Ocurrio un error procesando tu consulta. Intenta nuevamente en unos segundos."
            )

    def run(self) -> None:
        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler("start", self._start))
        app.add_handler(CommandHandler("help", self._help))
        app.add_handler(CommandHandler("saldo", self._saldo))
        app.add_handler(CommandHandler("plan", self._plan))
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_question)
        )

        console.print(
            "[green]✅ Taxpy Telegram MVP iniciado[/green]\n"
            f"[dim]DB: {config.TELEGRAM_DB_PATH}[/dim]\n"
            f"[dim]FREE: {config.TELEGRAM_FREE_QUERIES_PER_MONTH}/mes | "
            f"Top juris: {self.top_juris}[/dim]"
        )
        app.run_polling(allowed_updates=Update.ALL_TYPES)

