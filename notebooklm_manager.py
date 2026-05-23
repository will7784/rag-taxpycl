"""
Integración con NotebookLM vía notebooklm-py (unofficial Python API).

Requiere autenticación OAuth one-time vía browser (notebooklm-login).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from rich.console import Console

console = Console()

# Intentar importar notebooklm-py; si no está instalado, dar instrucciones
try:
    from notebooklm import NotebookLMClient
    _NOTEBOOKLM_AVAILABLE = True
except Exception:
    _NOTEBOOKLM_AVAILABLE = False
    NotebookLMClient = None  # type: ignore[misc,assignment]


class NotebookLMManager:
    def __init__(
        self,
        notebook_name: str = "Taxpy Jurisprudencia",
        state_path: Path | None = None,
    ) -> None:
        self.notebook_name = notebook_name
        self.state_path = state_path or Path("notebooklm_state.json")
        self._state = self._load_state()

        if not _NOTEBOOKLM_AVAILABLE:
            raise RuntimeError(
                "notebooklm-py no está instalado. "
                "Ejecuta: pip install notebooklm-py"
            )

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"version": 1, "notebooks": {}, "sources": {}}

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def create_or_get_notebook(self) -> str:
        """Crea o recupera el notebook de Taxpy."""
        # Revisar si ya tenemos uno guardado
        existing = self._state.get("notebooks", {}).get(self.notebook_name)
        if existing and existing.get("id"):
            try:
                async with await NotebookLMClient.from_storage() as client:
                    nb = await client.notebooks.get(existing["id"])
                    if nb:
                        console.print(f"[green]✅ Notebook existente: {nb.id}[/green]")
                        return str(nb.id)
            except Exception:
                pass  # Caer a crear nuevo

        # Crear nuevo
        console.print(f"[blue]📓 Creando notebook '{self.notebook_name}'...[/blue]")
        async with await NotebookLMClient.from_storage() as client:
            nb = await client.notebooks.create(self.notebook_name)
            self._state.setdefault("notebooks", {})[self.notebook_name] = {
                "id": str(nb.id),
                "created_at": __import__("datetime").datetime.now().isoformat(),
            }
            self._save_state()
            console.print(f"[green]✅ Notebook creado: {nb.id}[/green]")
            return str(nb.id)

    async def upload_sources(
        self,
        notebook_id: str,
        pdf_paths: list[Path],
    ) -> dict[str, int]:
        """Sube PDFs como fuentes al notebook. Evita duplicados por hash."""
        stats = {"uploaded": 0, "skipped": 0, "errors": 0}

        async with await NotebookLMClient.from_storage() as client:
            for pdf_path in pdf_paths:
                if not pdf_path.exists():
                    console.print(f"[yellow]⚠️ No existe: {pdf_path}[/yellow]")
                    stats["errors"] += 1
                    continue

                file_hash = self._file_hash(pdf_path)
                source_key = f"{notebook_id}::{file_hash}"

                if source_key in self._state.get("sources", {}):
                    console.print(f"  [dim]⏭️ Ya subido: {pdf_path.name}[/dim]")
                    stats["skipped"] += 1
                    continue

                console.print(f"  [blue]⬆️ Subiendo {pdf_path.name}...[/blue]")
                try:
                    source = await client.sources.add_file(
                        notebook_id,
                        str(pdf_path),
                        wait=True,
                    )
                    self._state.setdefault("sources", {})[source_key] = {
                        "filename": pdf_path.name,
                        "path": str(pdf_path),
                        "source_id": str(source.id) if hasattr(source, "id") else "",
                        "uploaded_at": __import__("datetime").datetime.now().isoformat(),
                    }
                    self._save_state()
                    stats["uploaded"] += 1
                except Exception as e:
                    console.print(f"    [red]❌ Error subiendo {pdf_path.name}: {e}[/red]")
                    stats["errors"] += 1

        return stats

    async def ask_question(
        self,
        notebook_id: str,
        question: str,
    ) -> dict[str, Any]:
        """Hace una pregunta al notebook y retorna respuesta con citas."""
        console.print(f"[dim]🔍 Preguntando a NotebookLM...[/dim]")
        async with await NotebookLMClient.from_storage() as client:
            result = await client.chat.ask(notebook_id, question)
            return {
                "answer": result.answer if hasattr(result, "answer") else str(result),
                "citations": [],  # TODO: extraer citas si la API las expone
            }

    async def generate_audio_overview(
        self,
        notebook_id: str,
        instructions: str = "",
    ) -> str:
        """Genera un Audio Overview (podcast) del notebook."""
        console.print(f"[blue]🎙️ Generando Audio Overview...[/blue]")
        async with await NotebookLMClient.from_storage() as client:
            status = await client.artifacts.generate_audio(
                notebook_id,
                instructions=instructions or "Genera un resumen en español",
            )
            await client.artifacts.wait_for_completion(notebook_id, status.task_id)
            return str(status.task_id)

    @staticmethod
    def _file_hash(path: Path) -> str:
        import hashlib

        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]

    def list_local_sources(self) -> list[dict[str, Any]]:
        return list(self._state.get("sources", {}).values())
