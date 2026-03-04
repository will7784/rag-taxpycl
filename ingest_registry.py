"""
Registro de Ingesta.
====================

Trackea qué archivos ya fueron ingestados usando un hash MD5.
Así al ejecutar `ingest` solo se procesan archivos nuevos o modificados.

El registro se guarda en `ingest_registry.json` junto al proyecto.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from rich.console import Console

import config

console = Console()

REGISTRY_PATH = config.BASE_DIR / "ingest_registry.json"


def _file_hash(file_path: Path) -> str:
    """Calcula el hash MD5 de un archivo."""
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_registry() -> dict:
    """Carga el registro desde disco."""
    if REGISTRY_PATH.exists():
        try:
            return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            return {}
    return {}


def save_registry(registry: dict) -> None:
    """Guarda el registro a disco."""
    REGISTRY_PATH.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def register_file(
    filename: str,
    file_hash: str,
    chunk_count: int,
    chunk_ids: list[str],
) -> None:
    """Registra un archivo como ingestado."""
    registry = load_registry()
    registry[filename] = {
        "hash": file_hash,
        "chunk_count": chunk_count,
        "chunk_ids": chunk_ids,
        "ingested_at": datetime.now().isoformat(),
    }
    save_registry(registry)


def unregister_file(filename: str) -> list[str]:
    """
    Elimina un archivo del registro.
    Retorna los chunk_ids que tenía para poder borrarlos del vector store.
    """
    registry = load_registry()
    entry = registry.pop(filename, None)
    save_registry(registry)
    if entry:
        return entry.get("chunk_ids", [])
    return []


def _build_registry_key(
    directory: Path,
    file_path: Path,
    key_prefix: str = "",
) -> str:
    """Construye key estable para registro incremental."""
    rel = file_path.relative_to(directory).as_posix()
    return f"{key_prefix}{rel}"


def get_pending_files(
    directory: Path,
    key_prefix: str = "",
) -> tuple[list[Path], list[Path], list[str]]:
    """
    Compara los archivos en el directorio contra el registro.

    Returns:
        (new_files, modified_files, deleted_filenames)
        - new_files: archivos que nunca se han ingestado
        - modified_files: archivos cuyo hash cambió (se re-ingestan)
        - deleted_filenames: archivos en el registro que ya no existen en disco
    """
    registry = load_registry()

    new_files: list[Path] = []
    modified_files: list[Path] = []

    # Revisar archivos actuales en el directorio
    current_keys: set[str] = set()

    for file_path in sorted(directory.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in config.SUPPORTED_EXTENSIONS:
            continue

        key = _build_registry_key(directory, file_path, key_prefix=key_prefix)
        current_keys.add(key)
        current_hash = _file_hash(file_path)

        if key not in registry:
            # Archivo nuevo
            new_files.append(file_path)
        elif registry[key]["hash"] != current_hash:
            # Archivo modificado
            modified_files.append(file_path)
        # else: ya ingestado y sin cambios → skip

    # Archivos que estaban en el registro del scope y ya no existen
    deleted_filenames = []
    for fname in registry:
        if key_prefix and not fname.startswith(key_prefix):
            continue
        if fname not in current_keys:
            deleted_filenames.append(fname)

    return new_files, modified_files, deleted_filenames
