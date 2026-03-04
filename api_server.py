from __future__ import annotations

import asyncio
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from rich.console import Console

import config
from rag_graph import RAGGraph

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - fallback en entornos sin psycopg
    psycopg = None
    dict_row = None

console = Console()
app = FastAPI(title="Taxpy API MVP", version="0.1.0")
rag = RAGGraph()


def ensure_utf8_console() -> None:
    """Forzar UTF-8 en consola para ejecuciones locales en Windows."""
    try:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


ensure_utf8_console()


@dataclass
class ApiUser:
    user_id: str
    month_key: str
    queries_used: int
    free_quota: int
    plan: str

    @property
    def remaining(self) -> int:
        return max(0, self.free_quota - self.queries_used)


class ApiUsageStore:
    def __init__(self, db_path: Path, database_url: str = ""):
        self.db_path = db_path
        self.database_url = (database_url or "").strip()
        self.use_postgres = self.database_url.startswith("postgresql://") or self.database_url.startswith("postgres://")
        if self.use_postgres and psycopg is None:
            raise RuntimeError("DATABASE_URL definido pero psycopg no está instalado")
        if not self.use_postgres:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _connect_pg(self):
        assert psycopg is not None
        return psycopg.connect(self.database_url, row_factory=dict_row)

    @staticmethod
    def _month_key(now: Optional[datetime] = None) -> str:
        dt = now or datetime.utcnow()
        return dt.strftime("%Y-%m")

    def _init_db(self) -> None:
        if self.use_postgres:
            with self._connect_pg() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS api_users (
                            user_id TEXT PRIMARY KEY,
                            plan TEXT NOT NULL DEFAULT 'free',
                            month_key TEXT NOT NULL,
                            queries_used INTEGER NOT NULL DEFAULT 0,
                            free_quota INTEGER NOT NULL DEFAULT 10,
                            created_at TEXT NOT NULL,
                            last_seen_at TEXT NOT NULL
                        );
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS api_usage_events (
                            id BIGSERIAL PRIMARY KEY,
                            user_id TEXT NOT NULL,
                            month_key TEXT NOT NULL,
                            mode TEXT NOT NULL,
                            question TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY(user_id) REFERENCES api_users(user_id)
                        );
                        """
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_api_usage_user_month "
                        "ON api_usage_events(user_id, month_key)"
                    )
                conn.commit()
            return

        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS api_users (
                    user_id TEXT PRIMARY KEY,
                    plan TEXT NOT NULL DEFAULT 'free',
                    month_key TEXT NOT NULL,
                    queries_used INTEGER NOT NULL DEFAULT 0,
                    free_quota INTEGER NOT NULL DEFAULT 10,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS api_usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    month_key TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    question TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES api_users(user_id)
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_user_month "
                "ON api_usage_events(user_id, month_key)"
            )

    def ensure_user(self, user_id: str) -> ApiUser:
        now_iso = datetime.utcnow().isoformat(timespec="seconds")
        current_month = self._month_key()
        if self.use_postgres:
            with self._connect_pg() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM api_users WHERE user_id=%s", (user_id,))
                    row = cur.fetchone()
                    if row is None:
                        cur.execute(
                            """
                            INSERT INTO api_users (
                                user_id, plan, month_key, queries_used, free_quota,
                                created_at, last_seen_at
                            ) VALUES (%s, 'free', %s, 0, %s, %s, %s)
                            """,
                            (
                                user_id,
                                current_month,
                                config.TELEGRAM_FREE_QUERIES_PER_MONTH,
                                now_iso,
                                now_iso,
                            ),
                        )
                        cur.execute("SELECT * FROM api_users WHERE user_id=%s", (user_id,))
                        row = cur.fetchone()
                    else:
                        updates = {"last_seen_at": now_iso}
                        if row["month_key"] != current_month:
                            updates["month_key"] = current_month
                            updates["queries_used"] = 0
                            updates["free_quota"] = config.TELEGRAM_FREE_QUERIES_PER_MONTH
                        set_sql = ", ".join(f"{k}=%s" for k in updates.keys())
                        cur.execute(
                            f"UPDATE api_users SET {set_sql} WHERE user_id=%s",
                            (*updates.values(), user_id),
                        )
                        cur.execute("SELECT * FROM api_users WHERE user_id=%s", (user_id,))
                        row = cur.fetchone()
                conn.commit()
            assert row is not None
            return ApiUser(
                user_id=row["user_id"],
                month_key=row["month_key"],
                queries_used=row["queries_used"],
                free_quota=row["free_quota"],
                plan=row["plan"],
            )

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM api_users WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO api_users (
                        user_id, plan, month_key, queries_used, free_quota,
                        created_at, last_seen_at
                    ) VALUES (?, 'free', ?, 0, ?, ?, ?)
                    """,
                    (
                        user_id,
                        current_month,
                        config.TELEGRAM_FREE_QUERIES_PER_MONTH,
                        now_iso,
                        now_iso,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM api_users WHERE user_id=?",
                    (user_id,),
                ).fetchone()
            else:
                updates = {"last_seen_at": now_iso}
                if row["month_key"] != current_month:
                    updates["month_key"] = current_month
                    updates["queries_used"] = 0
                    updates["free_quota"] = config.TELEGRAM_FREE_QUERIES_PER_MONTH
                set_sql = ", ".join(f"{k}=?" for k in updates.keys())
                conn.execute(
                    f"UPDATE api_users SET {set_sql} WHERE user_id=?",
                    (*updates.values(), user_id),
                )
                row = conn.execute(
                    "SELECT * FROM api_users WHERE user_id=?",
                    (user_id,),
                ).fetchone()

        return ApiUser(
            user_id=row["user_id"],
            month_key=row["month_key"],
            queries_used=row["queries_used"],
            free_quota=row["free_quota"],
            plan=row["plan"],
        )

    def register_usage(self, user_id: str, mode: str, question: str) -> ApiUser:
        now_iso = datetime.utcnow().isoformat(timespec="seconds")
        month = self._month_key()
        if self.use_postgres:
            with self._connect_pg() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE api_users SET queries_used=queries_used+1, last_seen_at=%s "
                        "WHERE user_id=%s",
                        (now_iso, user_id),
                    )
                    cur.execute(
                        """
                        INSERT INTO api_usage_events (user_id, month_key, mode, question, created_at)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (user_id, month, mode, question[:2000], now_iso),
                    )
                    cur.execute("SELECT * FROM api_users WHERE user_id=%s", (user_id,))
                    row = cur.fetchone()
                conn.commit()
            assert row is not None
            return ApiUser(
                user_id=row["user_id"],
                month_key=row["month_key"],
                queries_used=row["queries_used"],
                free_quota=row["free_quota"],
                plan=row["plan"],
            )

        with self._connect() as conn:
            conn.execute(
                "UPDATE api_users SET queries_used=queries_used+1, last_seen_at=? "
                "WHERE user_id=?",
                (now_iso, user_id),
            )
            conn.execute(
                """
                INSERT INTO api_usage_events (user_id, month_key, mode, question, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, month, mode, question[:2000], now_iso),
            )
            row = conn.execute(
                "SELECT * FROM api_users WHERE user_id=?",
                (user_id,),
            ).fetchone()

        return ApiUser(
            user_id=row["user_id"],
            month_key=row["month_key"],
            queries_used=row["queries_used"],
            free_quota=row["free_quota"],
            plan=row["plan"],
        )


store = ApiUsageStore(config.API_DB_PATH, getattr(config, "DATABASE_URL", ""))


class AskRequest(BaseModel):
    user_id: str = Field(..., min_length=2, max_length=120)
    question: str = Field(..., min_length=3, max_length=8000)
    mode: Literal["tax", "writer"] = "tax"
    top_juris: int = Field(default=8, ge=1, le=20)
    include_derogadas: bool = False


class AskResponse(BaseModel):
    answer: str
    remaining: int
    quota: int
    month_key: str
    mode: Literal["tax", "writer"]


def _is_token_valid(authorization: str | None, x_api_key: str | None) -> bool:
    token = config.API_ACCESS_TOKEN.strip()
    if not token:
        return True
    if x_api_key and x_api_key.strip() == token:
        return True
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()
        return bearer == token
    return False


def _writer_question(base_question: str) -> str:
    return (
        "MODO REDACTOR TECNICO: redacta en tono editorial claro, didactico, "
        "con estructura de capitulo, manteniendo rigor tributario y citando "
        "norma base. Pregunta base: "
        f"{base_question}"
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "taxpy-api", "time": datetime.utcnow().isoformat()}


@app.get("/")
async def root() -> dict:
    return {
        "service": "taxpy-api",
        "status": "ok",
        "endpoints": ["/health", "/usage/{user_id}", "/ask", "/docs"],
    }


@app.get("/usage/{user_id}")
async def usage(
    user_id: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict:
    if not _is_token_valid(authorization, x_api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = store.ensure_user(user_id.strip())
    return {
        "user_id": user.user_id,
        "plan": user.plan,
        "month_key": user.month_key,
        "queries_used": user.queries_used,
        "free_quota": user.free_quota,
        "remaining": user.remaining,
    }


@app.post("/ask", response_model=AskResponse)
async def ask(
    payload: AskRequest,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> AskResponse:
    if not _is_token_valid(authorization, x_api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")

    user = store.ensure_user(payload.user_id.strip())
    if user.plan == "free" and user.remaining <= 0:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Llegaste al limite FREE ({user.free_quota}/mes). "
                f"Plan PRO USD {config.TELEGRAM_PRO_PLAN_PRICE_USD} proximamente."
            ),
        )

    question = payload.question.strip()
    if payload.mode == "writer":
        question = _writer_question(question)

    try:
        result = await asyncio.to_thread(
            rag.query,
            question,
            payload.include_derogadas,
            payload.top_juris,
        )
        answer = (result.get("answer") or "").strip() or "No se pudo generar respuesta."
    except Exception as e:
        console.print(f"[red]API /ask error[/red] user={payload.user_id} err={e}")
        raise HTTPException(status_code=500, detail="Error interno procesando consulta")

    updated = store.register_usage(
        user_id=payload.user_id.strip(),
        mode=payload.mode,
        question=payload.question.strip(),
    )
    return AskResponse(
        answer=answer,
        remaining=updated.remaining,
        quota=updated.free_quota,
        month_key=updated.month_key,
        mode=payload.mode,
    )
