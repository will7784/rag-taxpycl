"""
RAG Documentos - Sistema de Retrieval Augmented Generation
=========================================================

Interfaz principal para ingestar documentos y hacer consultas.
Chunking inteligente por estructura para leyes tributarias.

Uso:
    python main.py ingest                           # Ingestar SOLO lo nuevo/modificado
    python main.py ingest --dir ./mis_docs           # Ingestar desde directorio específico
    python main.py ingest --force                    # Re-ingestar TODO desde cero
    python main.py query "¿Qué dice el Art. 21?"    # Hacer una consulta
    python main.py chat                              # Modo chat interactivo
    python main.py telegram-mvp                      # Bot Telegram MVP (Taxpy)
    python main.py api-server                        # API HTTP (Hostinger/Railway)
    python main.py stats                             # Ver estadísticas del vector store
    python main.py clear --yes                       # Limpiar el vector store
    python main.py migrate                           # Migrar a Pinecone
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import config

app = typer.Typer(
    name="rag-documentos",
    help="🔍 Sistema RAG para consultar documentos legales/tributarios",
    add_completion=False,
)
console = Console()


def _ensure_utf8_console() -> None:
    """
    Fuerza UTF-8 en consola para evitar caracteres raros (Windows/PowerShell).
    Safe no-op en otros sistemas.
    """
    try:
        if sys.platform == "win32":
            import ctypes

            # Cambiar code page de entrada/salida a UTF-8 en consola Win32.
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)

        # Python 3.7+: reconfigurar streams estándar.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        # Nunca romper la app por temas de encoding de terminal.
        pass


class _SimpleHTMLStripper(HTMLParser):
    """Convierte HTML a texto plano para archivos markdown curados."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return " ".join(self.parts).strip()


def _html_to_text(value: str | None) -> str:
    if not value:
        return ""
    parser = _SimpleHTMLStripper()
    parser.feed(value)
    return parser.text()


def _flatten_text_values(payload: object) -> list[str]:
    """Extrae recursivamente valores string desde dict/list."""
    out: list[str] = []
    if isinstance(payload, dict):
        for value in payload.values():
            out.extend(_flatten_text_values(value))
    elif isinstance(payload, list):
        for value in payload:
            out.extend(_flatten_text_values(value))
    elif isinstance(payload, str):
        clean = _html_to_text(payload).strip()
        if clean:
            out.append(clean)
    return out


def _sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = re.sub(r"[^a-zA-Z0-9\-_\.]+", "_", name).strip("_.")
    return name or "sin_nombre"


def _load_sync_state(state_path: Path) -> dict:
    """Carga estado incremental de sync-sii (si no existe, retorna base vacía)."""
    if not state_path.exists():
        return {"version": 1, "cuerpos": {}}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "cuerpos": {}}
        if "cuerpos" not in data or not isinstance(data.get("cuerpos"), dict):
            data["cuerpos"] = {}
        if "version" not in data:
            data["version"] = 1
        return data
    except Exception:
        return {"version": 1, "cuerpos": {}}


def _save_sync_state(state_path: Path, state: dict) -> None:
    """Guarda estado incremental de sync-sii de forma atómica."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(state_path)


def _pron_signature_from_list_item(pron: dict) -> str:
    """
    Firma liviana para detectar cambios entre corridas incrementales sin pedir
    el detalle completo de cada pronunciamiento.
    """
    parts = [
        str(pron.get("id", "")),
        str(pron.get("codigo", "")),
        str(pron.get("fecha", "")),
        str(pron.get("instancia", "")),
        str(pron.get("tipoPronunciamiento", "")),
        str(pron.get("tipoInstancia", "")),
        str(pron.get("tipoCodigo", "")),
    ]
    return "|".join(parts)


def show_banner():
    """Muestra el banner del sistema."""
    console.print(
        Panel(
            "[bold cyan]RAG Documentos[/bold cyan]\n"
            "[dim]Sistema de Retrieval Augmented Generation[/dim]\n"
            "[dim]Chunking estructural inteligente para leyes tributarias[/dim]\n"
            "[dim]PDF • DOCX • TXT • OCR • LangChain • LangGraph • ChromaDB[/dim]",
            title="🔍",
            border_style="cyan",
        )
    )


@app.command()
def ingest(
    directory: str = typer.Option(
        None,
        "--dir", "-d",
        help="Directorio con documentos a ingestar (default: ./documents)",
    ),
    max_chunk: int = typer.Option(
        None,
        "--max-chunk", "-mc",
        help="Tamaño máximo de un chunk (chars). Default: 4000",
    ),
    min_chunk: int = typer.Option(
        None,
        "--min-chunk", "-mn",
        help="Tamaño mínimo de un chunk (chars). Default: 100",
    ),
    force: bool = typer.Option(
        False,
        "--force", "-f",
        help="Forzar re-ingesta de TODOS los archivos (ignora registro)",
    ),
):
    """📥 Ingesta documentos al vector store con chunking estructural.

    Solo procesa archivos NUEVOS o MODIFICADOS (ingesta incremental).
    Usa --force para re-ingestar todo.
    """
    show_banner()

    from document_loader import DocumentLoader
    from text_processor import TextProcessor
    from vector_store import VectorStoreManager
    import ingest_registry

    docs_dir = Path(directory) if directory else config.DOCUMENTS_DIR

    if not docs_dir.exists() or not any(docs_dir.iterdir()):
        console.print(
            f"[yellow]⚠️ No hay archivos en {docs_dir}[/yellow]\n"
            f"[dim]Coloca tus documentos (PDF, DOCX, TXT, etc.) en "
            f"'{docs_dir}' y vuelve a ejecutar.[/dim]"
        )
        raise typer.Exit()

    # ── Detección incremental ──
    store = VectorStoreManager()

    docs_prefix = "docs::"
    notes_prefix = "notes::"

    if force:
        console.print("[yellow]⚠️ Modo --force: re-ingestando TODO[/yellow]")
        store.clear_collection()
        # Limpiar registro
        ingest_registry.save_registry({})
        files_to_process = [
            f for f in sorted(docs_dir.rglob("*"))
            if f.is_file() and f.suffix.lower() in config.SUPPORTED_EXTENSIONS
        ]
        modified_files: list[Path] = []
    else:
        new_files, modified_files, deleted = ingest_registry.get_pending_files(
            docs_dir,
            key_prefix=docs_prefix,
        )

        # Limpiar chunks de archivos eliminados del disco
        for key in deleted:
            fname = Path(key.split("::", 1)[-1]).name
            console.print(
                f"[yellow]🗑️ '{fname}' ya no existe en disco, "
                f"eliminando sus chunks...[/yellow]"
            )
            store.delete_by_filename(fname)
            ingest_registry.unregister_file(key)

        # Limpiar chunks de archivos modificados (se re-ingestan)
        for fpath in modified_files:
            console.print(
                f"[yellow]🔄 '{fpath.name}' fue modificado, "
                f"re-ingestando...[/yellow]"
            )
            store.delete_by_filename(fpath.name)
            doc_key = (
                f"{docs_prefix}{fpath.relative_to(docs_dir).as_posix()}"
            )
            ingest_registry.unregister_file(doc_key)

        files_to_process = new_files + modified_files

        if not files_to_process:
            console.print(
                "[green]✅ No hay archivos nuevos ni modificados.[/green]\n"
                "[dim]Todos los documentos ya están ingestados. "
                "Usa --force para re-ingestar todo.[/dim]"
            )
            raise typer.Exit()

        # Mostrar resumen de lo detectado
        if new_files:
            console.print(
                f"[blue]📄 Archivos nuevos: {len(new_files)}[/blue]"
            )
            for f in new_files:
                console.print(f"  [dim]+ {f.name}[/dim]")
        if modified_files:
            console.print(
                f"[yellow]🔄 Archivos modificados: {len(modified_files)}[/yellow]"
            )
            for f in modified_files:
                console.print(f"  [dim]~ {f.name}[/dim]")

    # ── También detectar notas curadas en notes/ ──
    notes_dir = config.NOTES_DIR
    if notes_dir.exists() and any(notes_dir.iterdir()):
        if force:
            note_files = [
                f for f in sorted(notes_dir.iterdir())
                if f.is_file() and f.suffix.lower() in config.SUPPORTED_EXTENSIONS
            ]
        else:
            new_notes, mod_notes, del_notes = ingest_registry.get_pending_files(
                notes_dir,
                key_prefix=notes_prefix,
            )
            for key in del_notes:
                fname = Path(key.split("::", 1)[-1]).name
                console.print(
                    f"[yellow]🗑️ Nota '{fname}' eliminada, "
                    f"borrando chunks...[/yellow]"
                )
                store.delete_by_filename(fname)
                ingest_registry.unregister_file(key)
            for fpath in mod_notes:
                console.print(
                    f"[yellow]🔄 Nota '{fpath.name}' modificada, "
                    f"re-ingestando...[/yellow]"
                )
                store.delete_by_filename(fpath.name)
                note_key = (
                    f"{notes_prefix}{fpath.relative_to(notes_dir).as_posix()}"
                )
                ingest_registry.unregister_file(note_key)
            note_files = new_notes + mod_notes

        if note_files:
            console.print(
                f"[magenta]📝 Notas curadas detectadas: "
                f"{len(note_files)}[/magenta]"
            )
            for f in note_files:
                console.print(f"  [dim]📝 {f.name}[/dim]")
            files_to_process.extend(note_files)

    if not files_to_process:
        console.print(
            "[green]✅ No hay archivos nuevos ni modificados.[/green]\n"
            "[dim]Todos los documentos y notas ya están ingestados. "
            "Usa --force para re-ingestar todo.[/dim]"
        )
        raise typer.Exit()

    # ── Cargar solo los archivos pendientes ──
    loader = DocumentLoader()
    documents = []
    for file_path in files_to_process:
        docs = loader.load_file(file_path)
        # Marcar source_type según directorio de origen
        source_lower = str(file_path).lower()
        if str(notes_dir).lower() in source_lower:
            source_type = "nota_curada"
        elif "jurisprudencia_sii" in source_lower:
            source_type = "jurisprudencia_sii"
        else:
            source_type = "ley_oficial"
        for doc in docs:
            doc.metadata["source_type"] = source_type
        documents.extend(docs)

    if not documents:
        console.print("[red]❌ No se pudieron cargar documentos.[/red]")
        raise typer.Exit(code=1)

    console.print(
        f"[green]✅ Se cargaron {len(documents)} documentos "
        f"nuevos/modificados[/green]"
    )

    # ── Chunking estructural ──
    processor = TextProcessor(
        max_chunk_size=max_chunk,
        min_chunk_size=min_chunk,
    )
    chunks = processor.process_documents(documents)

    if not chunks:
        console.print("[red]❌ No se generaron chunks.[/red]")
        raise typer.Exit(code=1)

    # Vista previa
    console.print(
        Panel(
            "[bold]Vista previa de los primeros 3 chunks:[/bold]",
            border_style="dim",
        )
    )
    for i, ch in enumerate(chunks[:3]):
        preview = ch.page_content[:200].replace("\n", " ↵ ")
        console.print(
            f"  [dim]#{i}[/dim]  "
            f"[cyan]{ch.metadata.get('hierarchy_path', '')}[/cyan]\n"
            f"  [dim]{preview}...[/dim]\n"
        )

    # ── Almacenar en vector store ──
    ids = store.add_documents(chunks)

    # ── Registrar cada archivo ingestado ──
    for file_path in files_to_process:
        file_hash = ingest_registry._file_hash(file_path)
        # Obtener IDs de chunks de este archivo
        file_chunk_ids = [
            cid for cid, ch in zip(ids, chunks)
            if ch.metadata.get("filename") == file_path.name
        ]
        if notes_dir in file_path.parents:
            reg_key = f"{notes_prefix}{file_path.relative_to(notes_dir).as_posix()}"
        else:
            reg_key = f"{docs_prefix}{file_path.relative_to(docs_dir).as_posix()}"

        ingest_registry.register_file(
            filename=reg_key,
            file_hash=file_hash,
            chunk_count=len(file_chunk_ids),
            chunk_ids=file_chunk_ids,
        )

    # ── Resumen ──
    level_names: dict[str, int] = {}
    for ch in chunks:
        ln = ch.metadata.get("section_level_name", "?")
        level_names[ln] = level_names.get(ln, 0) + 1

    summary_lines = "\n".join(
        f"  {name}: {count} chunks"
        for name, count in sorted(level_names.items())
    )

    total_stats = store.get_collection_stats()

    console.print(
        Panel(
            f"[green]✅ Ingesta completada[/green]\n\n"
            f"Archivos procesados: {len(files_to_process)}\n"
            f"Chunks nuevos: {len(chunks)}\n"
            f"Total chunks en DB: {total_stats['total_documents']}\n\n"
            f"[bold]Distribución por nivel:[/bold]\n{summary_lines}",
            title="📊 Resumen",
            border_style="green",
        )
    )


@app.command("sync-sii")
def sync_sii(
    cuerpo_id: int = typer.Option(
        2,
        "--cuerpo-id",
        help="ID de cuerpo normativo en ACJ (default: 2 = Ley de Renta)",
    ),
    articulo: str = typer.Option(
        None,
        "--articulo",
        "-a",
        help="Filtra por nombre de articulo exacto (ej: '10', '14 bis')",
    ),
    max_articulos: int = typer.Option(
        3,
        "--max-articulos",
        help="Maximo de articulos a sincronizar cuando no se especifica --articulo",
    ),
    max_pronunciamientos: int = typer.Option(
        25,
        "--max-pronunciamientos",
        help="Maximo de pronunciamientos por articulo",
    ),
    tipo_instancia_id: int = typer.Option(
        1,
        "--tipo-instancia-id",
        help=(
            "Filtro ACJ por tipo de instancia. Default=1 (actual). "
            "Permite probar otros valores si SII expone instancias adicionales."
        ),
    ),
    download_pdf: bool = typer.Option(
        False,
        "--download-pdf",
        help="Descarga PDF cuando exista urlDocumento",
    ),
    incremental: bool = typer.Option(
        False,
        "--incremental",
        help=(
            "Solo procesa pronunciamientos nuevos/cambiados segun estado local "
            "(recomendado para sync diario)."
        ),
    ),
    state_file: str = typer.Option(
        None,
        "--state-file",
        help=(
            "Ruta del archivo JSON de estado incremental. "
            "Default: documents/jurisprudencia_sii/_sync_state.json"
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Sobrescribe archivos markdown ya existentes",
    ),
):
    """📚 Sincroniza jurisprudencia SII (ACJ) y la guarda en documents/jurisprudencia_sii."""
    show_banner()
    from sii_acj_client import SIIACJClient

    target_root = config.DOCUMENTS_DIR / "jurisprudencia_sii"
    target_root.mkdir(parents=True, exist_ok=True)
    pdf_root = target_root / "_pdf"
    if download_pdf:
        pdf_root.mkdir(parents=True, exist_ok=True)
    sync_state_path = Path(state_file) if state_file else (target_root / "_sync_state.json")
    sync_state = _load_sync_state(sync_state_path)

    client = SIIACJClient()
    console.print("[blue]🌐 Consultando ACJ del SII...[/blue]")
    if incremental:
        console.print(f"[dim]🧠 Modo incremental activo (estado: {sync_state_path})[/dim]")

    articulos = client.find_articulos(cuerpo_id)
    if not articulos:
        console.print(
            f"[yellow]⚠️ No se encontraron articulos para cuerpo_id={cuerpo_id}[/yellow]"
        )
        raise typer.Exit(code=1)

    selected_articulos = articulos
    if articulo:
        needle = re.sub(r"\s+", " ", articulo.strip().lower())
        selected_articulos = [
            a for a in articulos
            if re.sub(r"\s+", " ", str(a.get("nombre", "")).strip().lower()) == needle
        ]
        if not selected_articulos:
            console.print(
                f"[yellow]⚠️ No existe el articulo '{articulo}' en cuerpo {cuerpo_id}.[/yellow]"
            )
            raise typer.Exit(code=1)
    else:
        selected_articulos = selected_articulos[:max_articulos]

    total_files = 0
    total_skipped = 0
    total_pdfs = 0
    total_pron = 0
    total_errors = 0
    total_incremental_skips = 0

    cuerpos_state = sync_state.setdefault("cuerpos", {})
    cuerpo_key = str(cuerpo_id)
    cuerpo_state = cuerpos_state.setdefault(cuerpo_key, {})
    articulos_state = cuerpo_state.setdefault("articulos", {})

    for art in selected_articulos:
        art_id = int(art["id"])
        art_name = str(art.get("nombre", art_id))
        art_slug = _sanitize_filename(art_name)
        art_dir = target_root / f"art_{art_slug}"
        art_dir.mkdir(parents=True, exist_ok=True)

        console.print(
            f"[cyan]🔎 Articulo {art_name} (id={art_id})[/cyan] "
            f"[dim]-> buscando pronunciamientos...[/dim]"
        )
        pronunciamientos = client.find_pronunciamientos(
            articulo_id=art_id,
            tipo_instancia_id=tipo_instancia_id,
            max_items=max_pronunciamientos,
        )
        art_state = articulos_state.setdefault(str(art_id), {})
        art_state["nombre"] = art_name
        art_state["slug"] = art_slug
        pron_signatures = art_state.setdefault("pron_signatures", {})
        current_pron_ids: set[str] = set()

        total_pron += len(pronunciamientos)
        if not pronunciamientos:
            console.print("  [dim]Sin resultados para este articulo.[/dim]")
            continue

        for pron in pronunciamientos:
            pron_id = int(pron.get("id"))
            pron_id_key = str(pron_id)
            current_pron_ids.add(pron_id_key)
            out_file = art_dir / f"sii_pron_{pron_id}.md"
            pron_sig = _pron_signature_from_list_item(pron)
            known_sig = str(pron_signatures.get(pron_id_key, ""))
            if (
                incremental
                and not force
                and out_file.exists()
                and known_sig
                and known_sig == pron_sig
            ):
                total_skipped += 1
                total_incremental_skips += 1
                continue
            if out_file.exists() and not force:
                total_skipped += 1
                if incremental:
                    pron_signatures[pron_id_key] = pron_sig
                continue

            try:
                full = client.get_full_pronunciamiento(pron_id)
            except Exception as e:
                total_errors += 1
                console.print(
                    f"  [yellow]⚠️ Pronunciamiento {pron_id} omitido por error:[/yellow] {e}"
                )
                continue
            resumen = (
                full.get("resumenInternet")
                or full.get("resumenIntranet")
                or pron.get("resumenInternet")
                or pron.get("resumenIntranet")
                or ""
            )
            contenido = _flatten_text_values(full.get("contenido", {}))
            contenido_text = "\n\n".join(dict.fromkeys(contenido))
            if len(contenido_text) > 30000:
                contenido_text = contenido_text[:30000] + "\n\n[...truncado...]"

            codigo = full.get("codigoPronunciamiento") or pron.get("codigo") or f"ID-{pron_id}"
            fecha = full.get("fecha") or pron.get("fecha") or "N/A"
            instancia = ""
            inst_data = full.get("instancia")
            if isinstance(inst_data, dict):
                instancia = inst_data.get("nombre", "")
            if not instancia:
                instancia = str(pron.get("instancia", "N/A"))
            tipo_pron = ""
            tipo_data = full.get("tipoPronunciamiento")
            if isinstance(tipo_data, dict):
                tipo_pron = tipo_data.get("nombre", "")

            pdf_url = full.get("urlDocumento")
            pdf_status = "no"
            if download_pdf and pdf_url:
                pdf_file = pdf_root / f"sii_pron_{pron_id}.pdf"
                if client.download_pdf(str(pdf_url), pdf_file):
                    total_pdfs += 1
                    pdf_status = "si"

            md_lines = [
                f"# Jurisprudencia SII - {codigo}",
                "",
                "## Metadata",
                f"- source_type: jurisprudencia_sii",
                f"- jurisprudencia_id: {pron_id}",
                f"- cuerpo_normativo_id: {cuerpo_id}",
                f"- articulo_id: {art_id}",
                f"- articulo_nombre: {art_name}",
                f"- fecha: {fecha}",
                f"- tipo_pronunciamiento: {tipo_pron or 'N/A'}",
                f"- instancia: {instancia}",
                f"- codigo_pronunciamiento: {codigo}",
                f"- pdf_url: {pdf_url or 'N/A'}",
                f"- pdf_descargado: {pdf_status}",
                "",
                "## Resumen",
                _html_to_text(str(resumen)) or "Sin resumen disponible.",
                "",
                "## Contenido",
                contenido_text or "Sin contenido detallado disponible.",
                "",
                "## Fuente",
                "- Servicio de Impuestos Internos (SII) - ACJ",
                f"- Pronunciamiento ID: {pron_id}",
            ]
            out_file.write_text("\n".join(md_lines), encoding="utf-8")
            pron_signatures[pron_id_key] = pron_sig
            total_files += 1

        # Mantener limpio el estado incremental eliminando pronunciamientos
        # que ya no aparecen en el listado actual del artículo.
        stale_ids = [pid for pid in list(pron_signatures.keys()) if pid not in current_pron_ids]
        for stale_id in stale_ids:
            pron_signatures.pop(stale_id, None)
        art_state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        art_state["last_count"] = len(current_pron_ids)

    _save_sync_state(sync_state_path, sync_state)
    console.print(
        Panel(
            f"[green]✅ Sync SII completada[/green]\n\n"
            f"Articulos procesados: {len(selected_articulos)}\n"
            f"Pronunciamientos detectados: {total_pron}\n"
            f"tipo_instancia_id usado: {tipo_instancia_id}\n"
            f"Archivos markdown creados/actualizados: {total_files}\n"
            f"Archivos omitidos (ya existian): {total_skipped}\n"
            f"Omitidos por incremental (sin cambios): {total_incremental_skips}\n"
            f"Pronunciamientos omitidos por error: {total_errors}\n"
            f"PDF descargados: {total_pdfs}\n\n"
            f"[dim]Estado incremental: {sync_state_path}[/dim]\n"
            f"[dim]Salida: {target_root}[/dim]\n"
            f"[dim]Siguiente paso: python main.py ingest[/dim]",
            title="📥 Jurisprudencia SII",
            border_style="green",
        )
    )


@app.command("sync-sii-admin")
def sync_sii_admin(
    cuerpo_id: int = typer.Option(
        2,
        "--cuerpo-id",
        help="ID cuerpo normativo para filtrar (default: 2 = LIR)",
    ),
    articulo: str = typer.Option(
        None,
        "--articulo",
        "-a",
        help="Articulo objetivo (ej: '10', '33', '14 bis') para filtrar resultados",
    ),
    text: str = typer.Option(
        None,
        "--text",
        "-t",
        help="Texto libre de busqueda para el ACJ administrativo",
    ),
    tipo_instancia_id: int = typer.Option(
        2,
        "--tipo-instancia-id",
        help="Tipo de instancia administrativa (default: 2 = Criterios Juridicos)",
    ),
    grupo_instancia_id: int = typer.Option(
        None,
        "--grupo-instancia-id",
        help="Grupo de instancia (default: primero disponible para tipo_instancia_id)",
    ),
    tipos_pron_ids: str = typer.Option(
        None,
        "--tipos-pron-ids",
        help="Lista CSV de tipoPronunciamientoId (ej: '4,3' para oficio,resolucion)",
    ),
    max_items_por_tipo: int = typer.Option(
        100,
        "--max-items-por-tipo",
        help="Maximo de items a pedir por tipoPronunciamientoId",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Sobrescribe archivos markdown existentes",
    ),
):
    """📚 Sincroniza jurisprudencia administrativa SII desde ACJ (ruta alternativa)."""
    show_banner()
    from sii_acj_client import SIIACJClient

    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip().lower())

    def _extract_art_refs(pron: dict) -> list[str]:
        refs: list[str] = []
        for block in (pron.get("articulos") or []):
            for a in (block.get("articulos") or []):
                if a:
                    refs.append(str(a).strip())
        return refs

    def _matches_art_filter(pron: dict, art_filter: str | None) -> bool:
        if not art_filter:
            return True
        needle = _norm(art_filter)
        refs = _extract_art_refs(pron)
        for r in refs:
            rr = _norm(r)
            if rr == needle or rr.startswith(f"{needle} "):
                return True
            if re.search(rf"\b{re.escape(needle)}\b", rr):
                return True
        return False

    target_root = config.DOCUMENTS_DIR / "jurisprudencia_sii_admin"
    target_root.mkdir(parents=True, exist_ok=True)

    client = SIIACJClient()
    console.print("[blue]🌐 Consultando ACJ administrativo del SII...[/blue]")

    tipo_info = client.get_tipo_instancia(tipo_instancia_id)
    if not tipo_info:
        console.print(
            f"[yellow]⚠️ No existe tipo_instancia_id={tipo_instancia_id} en ACJ.[/yellow]"
        )
        raise typer.Exit(code=1)
    console.print(
        f"[dim]Tipo instancia: {tipo_info.get('nombre')} | administrativa="
        f"{tipo_info.get('administrativa')}[/dim]"
    )

    grupos = client.find_grupos_instancia(tipo_instancia_id)
    if grupo_instancia_id is None:
        grupo_instancia_id = grupos[0]["id"] if grupos else None

    tipos = client.find_tipos_pronunciamiento(tipo_instancia_id)
    if tipos_pron_ids:
        ids = []
        for raw in tipos_pron_ids.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                ids.append(int(raw))
            except ValueError:
                pass
        tipo_ids = ids
    else:
        tipo_ids = [int(t["id"]) for t in tipos if t.get("id") is not None]
        if not tipo_ids:
            tipo_ids = [None]

    search_text = text or (f"articulo {articulo}" if articulo else None)

    all_items: dict[int, dict] = {}
    total_api_errors = 0
    for tipo_id in tipo_ids:
        search_form = {
            "text": search_text,
            "tipoInstanciaId": tipo_instancia_id,
            "grupoInstanciaId": grupo_instancia_id,
            "tipoCodigoId": None,
            "codigo": None,
            "ruc": None,
            "instanciaId": None,
            "tipoPronunciamientoId": tipo_id,
            "cuerpoNormativoId": cuerpo_id if cuerpo_id else None,
            "articulosIds": [],
            "reemplazos": [],
            "fechaDesde": None,
            "fechaHasta": None,
        }
        try:
            items = client.find_pronunciamientos_search_form(
                search_form=search_form,
                max_items=max_items_por_tipo,
            )
        except Exception as e:
            total_api_errors += 1
            console.print(
                f"[yellow]⚠️ Error tipoPron={tipo_id}:[/yellow] {e}"
            )
            continue

        for it in items:
            try:
                pid = int(it.get("id"))
            except Exception:
                continue
            all_items[pid] = it

    filtered = [it for it in all_items.values() if _matches_art_filter(it, articulo)]
    if not filtered:
        console.print(
            Panel(
                "[yellow]No se encontraron resultados administrativos con los "
                "filtros actuales.[/yellow]\n\n"
                f"tipo_instancia_id: {tipo_instancia_id}\n"
                f"grupo_instancia_id: {grupo_instancia_id}\n"
                f"tipos_pron_ids: {tipo_ids}\n"
                f"text: {search_text or 'N/A'}\n"
                f"cuerpo_id: {cuerpo_id}\n"
                f"articulo: {articulo or 'N/A'}",
                title="📭 Sync SII Admin",
                border_style="yellow",
            )
        )
        return

    created = 0
    skipped = 0
    full_errors = 0
    for pron in filtered:
        pron_id = int(pron.get("id"))
        out_file = target_root / f"sii_admin_pron_{pron_id}.md"
        if out_file.exists() and not force:
            skipped += 1
            continue

        try:
            full = client.get_full_pronunciamiento(pron_id)
        except Exception as e:
            full_errors += 1
            console.print(
                f"  [yellow]⚠️ get-full {pron_id} omitido:[/yellow] {e}"
            )
            continue

        resumen = (
            full.get("resumenInternet")
            or full.get("resumenIntranet")
            or pron.get("resumenInternet")
            or pron.get("resumenIntranet")
            or ""
        )
        contenido = _flatten_text_values(full.get("contenido", {}))
        contenido_text = "\n\n".join(dict.fromkeys(contenido))
        if len(contenido_text) > 30000:
            contenido_text = contenido_text[:30000] + "\n\n[...truncado...]"

        tipo_pron = ""
        tipo_data = full.get("tipoPronunciamiento")
        if isinstance(tipo_data, dict):
            tipo_pron = tipo_data.get("nombre", "")
        codigo = (
            full.get("codigoPronunciamiento")
            or pron.get("codigo")
            or f"ID-{pron_id}"
        )
        fecha = full.get("fecha") or pron.get("fecha") or "N/A"
        instancia = ""
        inst_data = full.get("instancia")
        if isinstance(inst_data, dict):
            instancia = inst_data.get("nombre", "")
        if not instancia:
            instancia = str(pron.get("instancia", "N/A"))
        pdf_url = full.get("urlDocumento")

        art_refs = _extract_art_refs(pron)
        md_lines = [
            f"# Jurisprudencia Administrativa SII - {codigo}",
            "",
            "## Metadata",
            "- source_type: jurisprudencia_sii",
            "- jurisprudencia_subtype: administrativa_acj",
            f"- jurisprudencia_id: {pron_id}",
            f"- cuerpo_normativo_id_filter: {cuerpo_id}",
            f"- articulo_nombre: {articulo or 'N/A'}",
            f"- articulo_filter: {articulo or 'N/A'}",
            f"- articulos_relacionados: {', '.join(art_refs) if art_refs else 'N/A'}",
            f"- fecha: {fecha}",
            f"- tipo_pronunciamiento: {tipo_pron or 'N/A'}",
            f"- instancia: {instancia}",
            f"- codigo_pronunciamiento: {codigo}",
            f"- pdf_url: {pdf_url or 'N/A'}",
            f"- tipo_instancia_id: {tipo_instancia_id}",
            f"- grupo_instancia_id: {grupo_instancia_id}",
            "",
            "## Resumen",
            _html_to_text(str(resumen)) or "Sin resumen disponible.",
            "",
            "## Contenido",
            contenido_text or "Sin contenido detallado disponible.",
            "",
            "## Fuente",
            "- Servicio de Impuestos Internos (SII) - ACJ",
            f"- Pronunciamiento ID: {pron_id}",
        ]
        out_file.write_text("\n".join(md_lines), encoding="utf-8")
        created += 1

    console.print(
        Panel(
            f"[green]✅ Sync SII Admin completada[/green]\n\n"
            f"Items encontrados (tras filtro): {len(filtered)}\n"
            f"Archivos creados/actualizados: {created}\n"
            f"Archivos omitidos (existentes): {skipped}\n"
            f"Errores get-full: {full_errors}\n"
            f"Errores API por tipo: {total_api_errors}\n\n"
            f"[dim]Salida: {target_root}[/dim]\n"
            f"[dim]Siguiente paso: python main.py ingest[/dim]",
            title="📥 Sync SII Administrativo",
            border_style="green",
        )
    )


@app.command("scan-instancias")
def scan_instancias(
    cuerpo_id: int = typer.Option(
        2,
        "--cuerpo-id",
        help="ID de cuerpo normativo en ACJ (default: 2 = Ley de Renta)",
    ),
    articulo: str = typer.Option(
        None,
        "--articulo",
        "-a",
        help="Filtra por nombre de articulo exacto (ej: '31', '14 bis')",
    ),
    max_articulos: int = typer.Option(
        3,
        "--max-articulos",
        help="Maximo de articulos a escanear cuando no se especifica --articulo",
    ),
    instancia_desde: int = typer.Option(
        1,
        "--instancia-desde",
        help="ID inicial de tipo_instancia_id a probar",
    ),
    instancia_hasta: int = typer.Option(
        8,
        "--instancia-hasta",
        help="ID final de tipo_instancia_id a probar (inclusive)",
    ),
    max_pronunciamientos: int = typer.Option(
        30,
        "--max-pronunciamientos",
        help="Maximo de pronunciamientos a traer por prueba",
    ),
    sample_get_full: int = typer.Option(
        10,
        "--sample-get-full",
        help="Cantidad maxima de pronunciamientos a muestrear con get-full por instancia",
    ),
    output_file: str = typer.Option(
        None,
        "--output-file",
        help=(
            "Ruta JSON de salida del escaneo. "
            "Default: documents/jurisprudencia_sii/_scan_instancias.json"
        ),
    ),
):
    """🧪 Escanea tipo_instancia_id del ACJ y genera reporte JSON por articulo."""
    show_banner()
    from sii_acj_client import SIIACJClient

    if instancia_desde < 1 or instancia_hasta < instancia_desde:
        console.print("[red]❌ Rango de instancia invalido.[/red]")
        raise typer.Exit(code=1)

    target_root = config.DOCUMENTS_DIR / "jurisprudencia_sii"
    target_root.mkdir(parents=True, exist_ok=True)
    report_path = (
        Path(output_file)
        if output_file
        else (target_root / "_scan_instancias.json")
    )

    client = SIIACJClient()
    console.print("[blue]🌐 Escaneando ACJ del SII por tipo_instancia_id...[/blue]")

    articulos = client.find_articulos(cuerpo_id)
    if not articulos:
        console.print(
            f"[yellow]⚠️ No se encontraron articulos para cuerpo_id={cuerpo_id}[/yellow]"
        )
        raise typer.Exit(code=1)

    selected_articulos = articulos
    if articulo:
        needle = re.sub(r"\s+", " ", articulo.strip().lower())
        selected_articulos = [
            a for a in articulos
            if re.sub(r"\s+", " ", str(a.get("nombre", "")).strip().lower()) == needle
        ]
        if not selected_articulos:
            console.print(
                f"[yellow]⚠️ No existe el articulo '{articulo}' en cuerpo {cuerpo_id}.[/yellow]"
            )
            raise typer.Exit(code=1)
    else:
        selected_articulos = selected_articulos[:max_articulos]

    report: dict = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cuerpo_id": cuerpo_id,
        "articulo_filter": articulo,
        "instancia_range": [instancia_desde, instancia_hasta],
        "max_pronunciamientos": max_pronunciamientos,
        "sample_get_full": sample_get_full,
        "results": [],
    }

    total_checks = 0
    total_ok = 0
    total_errors = 0
    for art in selected_articulos:
        art_id = int(art["id"])
        art_name = str(art.get("nombre", art_id))
        console.print(
            f"[cyan]🔎 Articulo {art_name} (id={art_id})[/cyan] "
            f"[dim]-> escaneando instancias {instancia_desde}-{instancia_hasta}...[/dim]"
        )

        art_result: dict = {
            "articulo_id": art_id,
            "articulo_nombre": art_name,
            "instancias": [],
        }

        table = Table(
            title=f"Scan Articulo {art_name} (id={art_id})",
            show_lines=False,
        )
        table.add_column("instancia_id", style="cyan", justify="right")
        table.add_column("estado", style="white")
        table.add_column("items", style="green", justify="right")
        table.add_column("tipos(get-full sample)", style="magenta")

        for instancia_id in range(instancia_desde, instancia_hasta + 1):
            total_checks += 1
            inst_result: dict = {
                "tipo_instancia_id": instancia_id,
            }
            try:
                items = client.find_pronunciamientos(
                    articulo_id=art_id,
                    tipo_instancia_id=instancia_id,
                    max_items=max_pronunciamientos,
                )
                inst_result["status"] = "ok"
                inst_result["items"] = len(items)
                total_ok += 1
            except Exception as e:
                inst_result["status"] = "error"
                inst_result["error"] = str(e)
                inst_result["items"] = 0
                inst_result["sampled_get_full"] = 0
                inst_result["get_full_errors"] = 0
                inst_result["tipo_pronunciamiento_sample"] = {}
                art_result["instancias"].append(inst_result)
                total_errors += 1
                table.add_row(
                    str(instancia_id),
                    "[red]error[/red]",
                    "0",
                    "-",
                )
                continue

            sampled = 0
            get_full_errors = 0
            tipos_count: dict[str, int] = {}
            for pron in items[:sample_get_full]:
                pron_id = int(pron.get("id"))
                try:
                    full = client.get_full_pronunciamiento(pron_id)
                except Exception:
                    get_full_errors += 1
                    continue
                tipo_data = full.get("tipoPronunciamiento")
                tipo_name = ""
                if isinstance(tipo_data, dict):
                    tipo_name = str(tipo_data.get("nombre", "")).strip()
                elif tipo_data is not None:
                    tipo_name = str(tipo_data).strip()
                tipo_name = tipo_name or "N/A"
                tipos_count[tipo_name] = tipos_count.get(tipo_name, 0) + 1
                sampled += 1

            inst_result["sampled_get_full"] = sampled
            inst_result["get_full_errors"] = get_full_errors
            inst_result["tipo_pronunciamiento_sample"] = tipos_count
            art_result["instancias"].append(inst_result)

            tipos_preview = ", ".join(
                f"{k}:{v}" for k, v in sorted(tipos_count.items())
            ) or "-"
            status_color = "green" if inst_result["status"] == "ok" else "red"
            table.add_row(
                str(instancia_id),
                f"[{status_color}]{inst_result['status']}[/{status_color}]",
                str(inst_result["items"]),
                tipos_preview,
            )

        report["results"].append(art_result)
        console.print(table)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    console.print(
        Panel(
            f"[green]✅ Scan completado[/green]\n\n"
            f"Articulos escaneados: {len(selected_articulos)}\n"
            f"Checks realizados: {total_checks}\n"
            f"Checks OK: {total_ok}\n"
            f"Checks con error: {total_errors}\n\n"
            f"[dim]Reporte JSON: {report_path}[/dim]",
            title="🧪 Scan Instancias ACJ",
            border_style="green",
        )
    )


@app.command("import-sii-admin")
def import_sii_admin(
    input_file: str = typer.Argument(
        ...,
        help="Ruta a archivo CSV o JSON con jurisprudencia administrativa",
    ),
    source_name: str = typer.Option(
        "fuente_externa",
        "--source-name",
        help="Nombre de la fuente (ej: respaldo_manual, partner_api)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Sobrescribe archivos existentes",
    ),
):
    """📦 Importa jurisprudencia administrativa desde CSV/JSON al corpus local."""
    show_banner()

    def _norm_key(s: str) -> str:
        s = unicodedata.normalize("NFKD", str(s))
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        s = s.lower().strip()
        s = re.sub(r"[^a-z0-9]+", "_", s)
        return s.strip("_")

    def _pick(d: dict, *keys: str, default: str = "") -> str:
        for k in keys:
            if k in d and d[k] not in (None, ""):
                return str(d[k]).strip()
        return default

    def _stable_id(codigo: str, fecha: str, articulo_ref: str) -> str:
        raw = f"{codigo}|{fecha}|{articulo_ref}"
        slug = _sanitize_filename(raw)
        return slug[:80] if slug else "sin_id"

    in_path = Path(input_file)
    if not in_path.exists():
        console.print(f"[red]❌ No existe archivo: {in_path}[/red]")
        raise typer.Exit(code=1)

    ext = in_path.suffix.lower()
    rows: list[dict] = []
    try:
        if ext == ".csv":
            with in_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(dict(row))
        elif ext == ".json":
            payload = json.loads(in_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                rows = [x for x in payload if isinstance(x, dict)]
            elif isinstance(payload, dict):
                if isinstance(payload.get("items"), list):
                    rows = [x for x in payload["items"] if isinstance(x, dict)]
                else:
                    rows = [payload]
            else:
                rows = []
        else:
            console.print("[red]❌ Formato no soportado. Usa CSV o JSON.[/red]")
            raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]❌ Error leyendo {in_path}: {e}[/red]")
        raise typer.Exit(code=1)

    if not rows:
        console.print("[yellow]⚠️ No hay registros para importar.[/yellow]")
        raise typer.Exit(code=1)

    out_root = config.DOCUMENTS_DIR / "jurisprudencia_sii_admin_import"
    out_root.mkdir(parents=True, exist_ok=True)

    created = 0
    skipped = 0
    for raw in rows:
        row = {_norm_key(k): v for k, v in raw.items()}

        codigo = _pick(
            row,
            "codigo_pronunciamiento",
            "codigo",
            "numero",
            "nro",
            default="N/A",
        )
        fecha = _pick(row, "fecha", "fecha_emision", default="N/A")
        tipo = _pick(
            row,
            "tipo_pronunciamiento",
            "tipo",
            "tipo_documento",
            default="Administrativo",
        )
        instancia = _pick(
            row,
            "instancia",
            "entidad_emisora",
            "emisor",
            default="Servicio de Impuestos Internos",
        )
        articulo_ref = _pick(
            row,
            "articulo_nombre",
            "articulo",
            "articulos_relacionados",
            "articulo_ref",
            default="N/A",
        )
        resumen = _pick(row, "resumen", "extracto", "descripcion", default="")
        contenido = _pick(row, "contenido", "texto", default="")
        pdf_url = _pick(row, "pdf_url", "url", "link", default="N/A")
        jur_id = _pick(row, "jurisprudencia_id", "id", default="")
        if not jur_id:
            jur_id = _stable_id(codigo, fecha, articulo_ref)

        art_slug = _sanitize_filename(articulo_ref if articulo_ref != "N/A" else "sin_articulo")
        out_dir = out_root / f"art_{art_slug}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"sii_admin_import_{_sanitize_filename(str(jur_id))}.md"
        if out_file.exists() and not force:
            skipped += 1
            continue

        md_lines = [
            f"# Jurisprudencia Administrativa SII - {codigo}",
            "",
            "## Metadata",
            "- source_type: jurisprudencia_sii",
            "- jurisprudencia_subtype: administrativa_import",
            f"- source_name: {source_name}",
            f"- jurisprudencia_id: {jur_id}",
            "- cuerpo_normativo_id_filter: 2",
            f"- articulo_nombre: {articulo_ref}",
            f"- articulo_filter: {articulo_ref}",
            f"- articulos_relacionados: {articulo_ref}",
            f"- fecha: {fecha}",
            f"- tipo_pronunciamiento: {tipo}",
            f"- instancia: {instancia}",
            f"- codigo_pronunciamiento: {codigo}",
            f"- pdf_url: {pdf_url}",
            "",
            "## Resumen",
            _html_to_text(resumen) if resumen else "Sin resumen disponible.",
            "",
            "## Contenido",
            _html_to_text(contenido) if contenido else "Sin contenido detallado disponible.",
            "",
            "## Fuente",
            "- Importacion administrativa externa",
            f"- Archivo origen: {in_path.name}",
        ]
        out_file.write_text("\n".join(md_lines), encoding="utf-8")
        created += 1

    console.print(
        Panel(
            f"[green]✅ Importación administrativa completada[/green]\n\n"
            f"Registros leidos: {len(rows)}\n"
            f"Archivos creados/actualizados: {created}\n"
            f"Archivos omitidos (existentes): {skipped}\n\n"
            f"[dim]Salida: {out_root}[/dim]\n"
            f"[dim]Siguiente paso: python main.py ingest[/dim]",
            title="📦 Import SII Admin",
            border_style="green",
        )
    )


@app.command("sync-sii-circulares")
def sync_sii_circulares(
    years: str = typer.Option(
        "2025,2024",
        "--years",
        help="Años CSV a sincronizar (ej: 2025,2024,2023)",
    ),
    max_items_per_year: int = typer.Option(
        0,
        "--max-items-per-year",
        help="Limita cantidad por año (0 = sin límite)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Sobrescribe archivos markdown existentes",
    ),
    scan_pdf_vigencia: bool = typer.Option(
        False,
        "--scan-pdf-vigencia",
        help="Escanea primera pagina del PDF para detectar si la circular fue dejada sin efecto",
    ),
):
    """📚 Sincroniza circulares SII por año (fuente pública normativa)."""
    show_banner()
    from sii_circulares_client import SIICircularesClient

    def _extract_article_refs(text: str) -> list[str]:
        # Captura robusta:
        # - artículo 17
        # - artículo 17 N° 8 / artículo 17 número 8
        # - artículos 17, 18 y 41
        refs_raw = re.findall(
            r"art[íi]culos?\s+([0-9][^.;:\n]{0,60})",
            text,
            re.IGNORECASE,
        )
        out: list[str] = []
        seen: set[str] = set()

        for chunk in refs_raw:
            chunk = chunk.replace("º", "").replace("°", "")
            chunk = re.sub(r"\s+", " ", chunk).strip()

            # 1) subnumeral explícito: 17 N° 8 / 17 número 8
            m_sub = re.findall(
                r"\b(\d{1,3})\s*(?:N[°º]?|n[úu]mero)\s*(\d{1,3})\b",
                chunk,
                re.IGNORECASE,
            )
            for art, num in m_sub:
                val = f"{int(art)} N° {int(num)}"
                if val not in seen:
                    seen.add(val)
                    out.append(val)

            # 2) artículos base (evitar tokens sueltos como letras)
            for art in re.findall(r"\b(\d{1,3})\b", chunk):
                val = str(int(art))
                if val not in seen:
                    seen.add(val)
                    out.append(val)

        return out

    def _scan_pdf_vigencia(pdf_url: str) -> dict[str, str]:
        """
        Lee la primera pagina del PDF para detectar textos del tipo:
        'Dejada sin efecto por Circular N° XX, del dd de mes de aaaa'.
        """
        out = {
            "estado_vigencia": "vigente",
            "dejada_sin_efecto_por": "N/A",
            "vigencia_fuente": "N/A",
        }
        if not pdf_url.lower().startswith(("http://", "https://")):
            return out

        headers = {
            "User-Agent": "rag-documentos/1.0 (+scan-pdf-vigencia)",
            "Accept": "application/pdf,*/*;q=0.8",
        }
        req = Request(pdf_url, headers=headers, method="GET")
        try:
            with urlopen(req, timeout=25) as resp:
                pdf_bytes = resp.read()
        except URLError:
            return out
        except Exception:
            return out

        try:
            import fitz  # pymupdf

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            first_page = doc[0].get_text("text") if len(doc) > 0 else ""
            doc.close()
        except Exception:
            return out

        text = (first_page or "").strip()
        if not text:
            return out

        m = re.search(
            r"dejad[ao]?\s+sin\s+efecto\s+por\s+(.+?)(?:\r?\n|$)",
            text,
            re.IGNORECASE,
        )
        if m:
            ref = re.sub(r"\s+", " ", m.group(1)).strip(" .;-")
            out["estado_vigencia"] = "dejada_sin_efecto"
            out["dejada_sin_efecto_por"] = ref or "N/A"
            out["vigencia_fuente"] = "pdf_primera_pagina"
        return out

    years_list: list[int] = []
    for raw in years.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            years_list.append(int(raw))
        except ValueError:
            pass
    if not years_list:
        console.print("[red]❌ Debes indicar al menos un año válido.[/red]")
        raise typer.Exit(code=1)

    client = SIICircularesClient()
    out_root = config.DOCUMENTS_DIR / "jurisprudencia_sii_circulares"
    out_root.mkdir(parents=True, exist_ok=True)

    created = 0
    skipped = 0
    total = 0
    for year in years_list:
        console.print(f"[cyan]🔎 Descargando índice de circulares {year}...[/cyan]")
        try:
            items = client.list_circulares_by_year(year)
        except Exception as e:
            console.print(f"[yellow]⚠️ Año {year} omitido por error:[/yellow] {e}")
            continue
        if max_items_per_year > 0:
            items = items[:max_items_per_year]
        total += len(items)
        year_dir = out_root / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)

        for it in items:
            numero = str(it.get("numero") or "s/n")
            fecha = str(it.get("fecha") or "N/A")
            titulo = str(it.get("titulo") or f"Circular {numero}-{year}")
            resumen = str(it.get("resumen") or "")
            fuente = str(it.get("fuente") or "Servicio de Impuestos Internos")
            pdf_url = str(it.get("pdf_url") or "N/A")
            index_url = str(it.get("index_url") or "N/A")

            codigo = f"CIRCULAR {numero}-{year}"
            art_refs = _extract_article_refs(f"{titulo}. {resumen}")
            articulo_nombre = art_refs[0] if art_refs else "N/A"
            jur_id = f"circular-{year}-{numero}"
            vigencia = {
                "estado_vigencia": "vigente",
                "dejada_sin_efecto_por": "N/A",
                "vigencia_fuente": "N/A",
            }
            if scan_pdf_vigencia:
                vigencia = _scan_pdf_vigencia(pdf_url)

            out_file = year_dir / f"sii_circular_{year}_{_sanitize_filename(numero)}.md"
            if out_file.exists() and not force:
                skipped += 1
                continue

            md_lines = [
                f"# Jurisprudencia Administrativa SII - {codigo}",
                "",
                "## Metadata",
                "- source_type: jurisprudencia_sii",
                "- jurisprudencia_subtype: circular_sii_web",
                "- source_name: sii_normativa_circulares",
                f"- jurisprudencia_id: {jur_id}",
                "- cuerpo_normativo_id_filter: N/A",
                f"- articulo_nombre: {articulo_nombre}",
                f"- articulo_filter: {articulo_nombre}",
                f"- articulos_relacionados: {', '.join(art_refs) if art_refs else 'N/A'}",
                f"- fecha: {fecha}",
                "- tipo_pronunciamiento: Circular",
                f"- instancia: {fuente}",
                f"- codigo_pronunciamiento: {codigo}",
                f"- pdf_url: {pdf_url}",
                f"- estado_vigencia: {vigencia['estado_vigencia']}",
                f"- dejada_sin_efecto_por: {vigencia['dejada_sin_efecto_por']}",
                f"- vigencia_fuente: {vigencia['vigencia_fuente']}",
                "",
                "## Resumen",
                resumen or "Sin resumen disponible.",
                "",
                "## Contenido",
                f"Título: {titulo}\n\n"
                f"Resumen: {resumen or 'N/A'}\n\n"
                f"Fuente índice: {index_url}\n"
                f"Documento: {pdf_url}",
                "",
                "## Fuente",
                "- Servicio de Impuestos Internos (SII) - Normativa y Legislación",
                f"- Índice anual: {index_url}",
            ]
            out_file.write_text("\n".join(md_lines), encoding="utf-8")
            created += 1

    console.print(
        Panel(
            f"[green]✅ Sync de circulares completada[/green]\n\n"
            f"Años procesados: {', '.join(str(y) for y in years_list)}\n"
            f"Circulares detectadas: {total}\n"
            f"Archivos creados/actualizados: {created}\n"
            f"Archivos omitidos (existentes): {skipped}\n\n"
            f"[dim]Salida: {out_root}[/dim]\n"
            f"[dim]Siguiente paso: python main.py ingest[/dim]",
            title="📘 SII Circulares",
            border_style="green",
        )
    )


@app.command("validate-sii-admin")
def validate_sii_admin(
    input_file: str = typer.Argument(
        ...,
        help="Ruta a archivo CSV o JSON con jurisprudencia administrativa",
    ),
    report_file: str = typer.Option(
        None,
        "--report-file",
        help="Ruta opcional para guardar reporte JSON de validación",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Falla con código 1 si hay warnings (además de errores)",
    ),
):
    """✅ Valida archivo CSV/JSON antes de importarlo al corpus administrativo."""
    show_banner()

    def _norm_key(s: str) -> str:
        s = unicodedata.normalize("NFKD", str(s))
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        s = s.lower().strip()
        s = re.sub(r"[^a-z0-9]+", "_", s)
        return s.strip("_")

    def _pick(d: dict, *keys: str, default: str = "") -> str:
        for k in keys:
            if k in d and d[k] not in (None, ""):
                return str(d[k]).strip()
        return default

    def _is_valid_date(s: str) -> bool:
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return True
        except Exception:
            return False

    in_path = Path(input_file)
    if not in_path.exists():
        console.print(f"[red]❌ No existe archivo: {in_path}[/red]")
        raise typer.Exit(code=1)

    ext = in_path.suffix.lower()
    rows: list[dict] = []
    try:
        if ext == ".csv":
            with in_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(dict(row))
        elif ext == ".json":
            payload = json.loads(in_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                rows = [x for x in payload if isinstance(x, dict)]
            elif isinstance(payload, dict):
                if isinstance(payload.get("items"), list):
                    rows = [x for x in payload["items"] if isinstance(x, dict)]
                else:
                    rows = [payload]
            else:
                rows = []
        else:
            console.print("[red]❌ Formato no soportado. Usa CSV o JSON.[/red]")
            raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]❌ Error leyendo {in_path}: {e}[/red]")
        raise typer.Exit(code=1)

    if not rows:
        console.print("[red]❌ No hay registros para validar.[/red]")
        raise typer.Exit(code=1)

    allowed_types = {
        "oficio",
        "circular",
        "resolucion",
        "resolución",
        "memo",
        "sentencia",
        "administrativo",
    }
    errors: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    seen_compound: set[str] = set()

    for idx, raw in enumerate(rows, start=1):
        row = {_norm_key(k): v for k, v in raw.items()}
        row_tag = f"fila {idx}"

        jur_id = _pick(row, "jurisprudencia_id", "id", default="")
        codigo = _pick(
            row,
            "codigo_pronunciamiento",
            "codigo",
            "numero",
            "nro",
            default="",
        )
        fecha = _pick(row, "fecha", "fecha_emision", default="")
        tipo = _pick(
            row,
            "tipo_pronunciamiento",
            "tipo",
            "tipo_documento",
            default="",
        )
        instancia = _pick(
            row,
            "instancia",
            "entidad_emisora",
            "emisor",
            default="",
        )
        articulo = _pick(
            row,
            "articulo_nombre",
            "articulo",
            "articulos_relacionados",
            "articulo_ref",
            default="",
        )
        resumen = _pick(row, "resumen", "extracto", "descripcion", default="")
        contenido = _pick(row, "contenido", "texto", default="")
        pdf_url = _pick(row, "pdf_url", "url", "link", default="")

        if not codigo:
            errors.append(f"{row_tag}: falta codigo_pronunciamiento/codigo")
        if not fecha:
            errors.append(f"{row_tag}: falta fecha")
        elif not _is_valid_date(fecha):
            errors.append(f"{row_tag}: fecha inválida '{fecha}' (usa YYYY-MM-DD)")
        if not tipo:
            errors.append(f"{row_tag}: falta tipo_pronunciamiento/tipo")
        elif tipo.lower() not in allowed_types:
            warnings.append(
                f"{row_tag}: tipo '{tipo}' no está en catálogo sugerido "
                f"(oficio/circular/resolucion/memo/sentencia)"
            )
        if not instancia:
            warnings.append(f"{row_tag}: instancia vacía")
        if not articulo:
            errors.append(f"{row_tag}: falta articulo_nombre/articulo")
        elif not re.match(r"^\d+", articulo.strip()):
            warnings.append(
                f"{row_tag}: articulo '{articulo}' no inicia en número "
                "(revisar formato)"
            )
        if not resumen and not contenido:
            errors.append(f"{row_tag}: faltan resumen y contenido (al menos uno requerido)")
        if not pdf_url:
            warnings.append(f"{row_tag}: pdf_url vacío")

        if jur_id:
            if jur_id in seen_ids:
                warnings.append(f"{row_tag}: jurisprudencia_id duplicado '{jur_id}'")
            seen_ids.add(jur_id)
        compound = f"{codigo}|{fecha}|{articulo}"
        if compound in seen_compound:
            warnings.append(f"{row_tag}: posible duplicado por clave compuesta {compound}")
        seen_compound.add(compound)

    status_ok = not errors and (not strict or not warnings)
    status_label = "OK" if status_ok else "REVISAR"

    table = Table(title="Validación SII Admin")
    table.add_column("Métrica", style="cyan")
    table.add_column("Valor", style="white")
    table.add_row("Archivo", str(in_path))
    table.add_row("Registros", str(len(rows)))
    table.add_row("Errores", str(len(errors)))
    table.add_row("Warnings", str(len(warnings)))
    table.add_row("Modo strict", str(strict))
    table.add_row("Estado", status_label)
    console.print(table)

    if errors:
        console.print("[red]Errores detectados:[/red]")
        for line in errors[:30]:
            console.print(f"  - {line}")
        if len(errors) > 30:
            console.print(f"  [dim]... y {len(errors) - 30} más[/dim]")
    if warnings:
        console.print("[yellow]Warnings detectados:[/yellow]")
        for line in warnings[:30]:
            console.print(f"  - {line}")
        if len(warnings) > 30:
            console.print(f"  [dim]... y {len(warnings) - 30} más[/dim]")

    if report_file:
        report = {
            "file": str(in_path),
            "rows": len(rows),
            "errors_count": len(errors),
            "warnings_count": len(warnings),
            "errors": errors,
            "warnings": warnings,
            "strict": strict,
            "status": status_label,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        rp = Path(report_file)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[dim]Reporte guardado: {rp}[/dim]")

    if not status_ok:
        raise typer.Exit(code=1)


@app.command()
def query(
    question: str = typer.Argument(..., help="Pregunta a consultar"),
    include_derogadas: bool = typer.Option(
        False,
        "--include-derogadas",
        help="Incluye jurisprudencia/circulares dejadas sin efecto (modo histórico).",
    ),
    top_juris: int = typer.Option(
        8,
        "--top-juris",
        help="Cantidad maxima de pronunciamientos jurisprudenciales a mostrar (1-20).",
    ),
):
    """❓ Hace una consulta al sistema RAG."""
    show_banner()

    if not config.OPENAI_API_KEY:
        console.print(
            "[red]❌ Configura OPENAI_API_KEY en el archivo .env[/red]"
        )
        raise typer.Exit(code=1)

    from rag_graph import RAGGraph

    rag = RAGGraph()
    top_juris = max(1, min(20, int(top_juris)))
    rag.query(
        question,
        include_derogadas=include_derogadas,
        top_juris=top_juris,
    )


def _slugify(text: str, max_words: int = 6) -> str:
    """Convierte texto a slug para nombre de archivo."""
    # Remover acentos
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Solo alfanuméricos y espacios
    text = re.sub(r"[^a-zA-Z0-9\s]", "", text.lower())
    # Stopwords en español
    stopwords = {
        "el", "la", "los", "las", "un", "una", "unos", "unas",
        "de", "del", "al", "en", "y", "o", "que", "es", "son",
        "se", "por", "con", "para", "como", "su", "sus", "me",
        "mi", "a", "e", "no", "si", "haz", "explicame", "cuales",
        "dime", "cual", "quiero", "necesito", "puedes", "podrias",
        "mira", "ver", "esto", "eso", "ese", "esa", "hay",
    }
    words = [w for w in text.split() if w not in stopwords and len(w) > 2]
    return "_".join(words[:max_words]) or "nota"


def _extract_articles_from_text(text: str) -> list[str]:
    """Extrae referencias a artículos mencionados en el texto."""
    pattern = re.compile(
        r"Art(?:[íi]culo|\.)\s*(\d+[°º]?\s*(?:bis|ter|qu[áa]ter)?"
        r"(?:\s+[A-Z])?\s*(?:N[°º]\s*\d+)?)",
        re.IGNORECASE,
    )
    matches = pattern.findall(text)
    # Limpiar y deduplicar
    seen: set[str] = set()
    articles: list[str] = []
    for m in matches:
        clean = re.sub(r"\s+", " ", m.strip().rstrip("°º.- "))
        if clean and clean not in seen:
            seen.add(clean)
            articles.append(f"Art. {clean}")
    return articles[:15]  # máximo razonable


def _copy_to_clipboard(text: str) -> bool:
    """
    Copia texto al portapapeles del sistema.
    Windows: API nativa Win32 (maneja Unicode perfectamente).
    macOS: pbcopy.  Linux: xclip.
    """
    try:
        if sys.platform == "win32":
            return _copy_to_clipboard_win32(text)
        elif sys.platform == "darwin":
            subprocess.run(
                ["pbcopy"],
                input=text.encode("utf-8"),
                check=True,
            )
            return True
        else:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text.encode("utf-8"),
                check=True,
            )
            return True
    except Exception:
        return False


def _copy_to_clipboard_win32(text: str) -> bool:
    """Copia al portapapeles usando la API Win32 nativa (Unicode)."""
    import ctypes
    import ctypes.wintypes as wt

    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32

    # ── Declarar tipos (crítico en 64-bit) ──
    user32.OpenClipboard.argtypes = [wt.HWND]
    user32.OpenClipboard.restype = wt.BOOL
    user32.CloseClipboard.restype = wt.BOOL
    user32.EmptyClipboard.restype = wt.BOOL
    user32.SetClipboardData.argtypes = [wt.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p

    kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = wt.BOOL

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    # Windows usa UTF-16-LE internamente + null terminator
    encoded = text.encode("utf-16-le") + b"\x00\x00"

    if not user32.OpenClipboard(None):
        return False

    try:
        user32.EmptyClipboard()

        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if not h_mem:
            return False

        ptr = kernel32.GlobalLock(h_mem)
        if not ptr:
            return False

        ctypes.memmove(ptr, encoded, len(encoded))
        kernel32.GlobalUnlock(h_mem)

        result = user32.SetClipboardData(CF_UNICODETEXT, h_mem)
        return bool(result)
    finally:
        user32.CloseClipboard()


def _save_note(question: str, answer: str) -> Path | None:
    """
    Guarda la última respuesta como nota .md en la carpeta notes/.

    Formato del archivo:
        # <Título extraído o pregunta>
        > **Pregunta original:** <question>
        <answer>

    Returns:
        Path del archivo guardado, o None si hubo error.
    """
    notes_dir = config.NOTES_DIR
    notes_dir.mkdir(exist_ok=True)

    # Generar título: buscar un heading H1/H2 que no sea un numeral de sección
    # (ej: "### 1. Explicación General" NO es un buen título)
    titulo = None
    for m in re.finditer(r"^(#{1,2})\s+(.+)$", answer, re.MULTILINE):
        candidate = m.group(2).strip()
        # Saltar headings que son numerales de sección ("1. Explicación...")
        if re.match(r"^\d+\.\s+", candidate):
            continue
        titulo = candidate[:120]
        break
    if not titulo:
        # Fallback: limpiar la pregunta
        titulo = question.strip().rstrip("?.,;:").strip().capitalize()[:120]

    # Extraer artículos para metadata textual
    articles = _extract_articles_from_text(answer)
    articles_line = ", ".join(articles) if articles else "N/A"

    # Generar nombre de archivo
    slug = _slugify(question)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{slug}.md"
    filepath = notes_dir / filename

    # Construir contenido
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# {titulo}",
        "",
        f"> **Pregunta original:** {question}",
        f"> **Fecha:** {fecha}",
        f"> **Artículos base:** {articles_line}",
        "",
        answer,
    ]
    content = "\n".join(lines)

    try:
        filepath.write_text(content, encoding="utf-8")
        return filepath
    except Exception as e:
        console.print(f"[red]❌ Error al guardar nota: {e}[/red]")
        return None


@app.command()
def chat(
    include_derogadas: bool = typer.Option(
        False,
        "--include-derogadas",
        help="Incluye jurisprudencia/circulares dejadas sin efecto (modo histórico).",
    ),
    top_juris: int = typer.Option(
        8,
        "--top-juris",
        help="Cantidad maxima de pronunciamientos jurisprudenciales a mostrar (1-20).",
    ),
):
    """💬 Inicia un chat interactivo con los documentos."""
    show_banner()

    if not config.OPENAI_API_KEY:
        console.print(
            "[red]❌ Configura OPENAI_API_KEY en el archivo .env[/red]"
        )
        raise typer.Exit(code=1)

    from rag_graph import RAGGraph

    rag = RAGGraph()
    top_juris = max(1, min(20, int(top_juris)))

    console.print(
        "[cyan]💬 Modo chat activado.[/cyan]\n"
        f"[dim]Modo histórico derogadas: {'ON' if include_derogadas else 'OFF'}[/dim]\n"
        f"[dim]Top jurisprudencia: {top_juris}[/dim]\n"
        "[dim]Comandos: 'copiar' → copiar al portapapeles | "
        "'guardar' → guardar como nota | 'salir' → terminar[/dim]\n"
    )

    last_question: str | None = None
    last_answer: str | None = None

    while True:
        try:
            question = console.input("[bold cyan]Tú > [/bold cyan]")
            cmd = question.lower().strip()

            if cmd in ("salir", "exit", "quit", "q"):
                console.print("[dim]👋 ¡Hasta luego![/dim]")
                break

            if not question.strip():
                continue

            # ── Comando: copiar al portapapeles ──
            if cmd in ("copiar", "copy", "cp"):
                if last_answer:
                    if _copy_to_clipboard(last_answer):
                        console.print(
                            "[green]📋 Respuesta copiada al "
                            "portapapeles.[/green]"
                        )
                    else:
                        console.print(
                            "[yellow]⚠️ No se pudo copiar. "
                            "Selecciona el texto manualmente.[/yellow]"
                        )
                else:
                    console.print(
                        "[yellow]⚠️ No hay respuesta para copiar. "
                        "Haz una pregunta primero.[/yellow]"
                    )
                continue

            # ── Comando: guardar nota ──
            if cmd in ("guardar", "save", "guardar nota"):
                if last_answer and last_question:
                    filepath = _save_note(last_question, last_answer)
                    if filepath:
                        console.print(
                            f"[green]✅ Nota guardada: "
                            f"[bold]{filepath.name}[/bold][/green]"
                        )
                        console.print(
                            f"[dim]📁 {filepath}[/dim]\n"
                            f"[dim]💡 Ejecuta 'python main.py ingest' "
                            f"para indexar las notas al RAG.[/dim]"
                        )
                else:
                    console.print(
                        "[yellow]⚠️ No hay respuesta para guardar. "
                        "Haz una pregunta primero.[/yellow]"
                    )
                continue

            # ── Consulta normal ──
            result = rag.query(
                question,
                include_derogadas=include_derogadas,
                top_juris=top_juris,
            )
            last_question = question
            last_answer = result["answer"]

            # Hint post-respuesta
            console.print(
                "\n[dim]📋 'copiar' → portapapeles | "
                "⭐ 'guardar' → nota | 'salir' → terminar[/dim]\n"
            )

        except KeyboardInterrupt:
            console.print("\n[dim]👋 ¡Hasta luego![/dim]")
            break


@app.command("telegram-mvp")
def telegram_mvp(
    include_derogadas: bool = typer.Option(
        False,
        "--include-derogadas",
        help="Incluye jurisprudencia/circulares dejadas sin efecto (modo histórico).",
    ),
    top_juris: int = typer.Option(
        6,
        "--top-juris",
        help="Cantidad maxima de pronunciamientos jurisprudenciales por respuesta (1-20).",
    ),
):
    """🤖 Levanta el bot Telegram MVP de Taxpy (long polling)."""
    show_banner()

    if not config.OPENAI_API_KEY:
        console.print("[red]❌ Configura OPENAI_API_KEY en el archivo .env[/red]")
        raise typer.Exit(code=1)

    if not config.TELEGRAM_BOT_TOKEN:
        console.print("[red]❌ Configura TELEGRAM_BOT_TOKEN en el archivo .env[/red]")
        raise typer.Exit(code=1)

    from telegram_mvp_bot import TaxpyTelegramBot

    top_juris = max(1, min(20, int(top_juris)))
    bot = TaxpyTelegramBot(
        token=config.TELEGRAM_BOT_TOKEN,
        include_derogadas=include_derogadas,
        top_juris=top_juris,
    )
    bot.run()


@app.command("api-server")
def api_server():
    """🌐 Levanta API HTTP para integrar web/app con el RAG (FastAPI)."""
    show_banner()

    if not config.OPENAI_API_KEY:
        console.print("[red]❌ Configura OPENAI_API_KEY en el archivo .env[/red]")
        raise typer.Exit(code=1)

    try:
        import uvicorn
    except Exception:
        console.print(
            "[red]❌ Falta dependencia 'uvicorn'. Ejecuta: "
            "pip install -r requirements.txt[/red]"
        )
        raise typer.Exit(code=1)

    console.print(
        "[green]✅ Iniciando Taxpy API[/green]\n"
        f"[dim]http://{config.API_SERVER_HOST}:{config.API_SERVER_PORT}[/dim]\n"
        "[dim]Endpoints: /health, /usage/{user_id}, /ask[/dim]"
    )
    uvicorn.run(
        "api_server:app",
        host=config.API_SERVER_HOST,
        port=config.API_SERVER_PORT,
        reload=False,
    )


@app.command()
def notes():
    """📝 Lista las notas curadas guardadas."""
    show_banner()

    notes_dir = config.NOTES_DIR
    if not notes_dir.exists() or not any(notes_dir.iterdir()):
        console.print(
            "[yellow]⚠️ No hay notas guardadas.[/yellow]\n"
            "[dim]Usa 'guardar' en el chat para guardar respuestas "
            "como notas.[/dim]"
        )
        raise typer.Exit()

    note_files = sorted(
        (f for f in notes_dir.iterdir()
         if f.is_file() and f.suffix.lower() == ".md"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    if not note_files:
        console.print("[yellow]⚠️ No hay notas .md en la carpeta.[/yellow]")
        raise typer.Exit()

    table = Table(title="📝 Notas Curadas")
    table.add_column("#", style="dim", width=4)
    table.add_column("Archivo", style="cyan")
    table.add_column("Tamaño", style="green", justify="right")
    table.add_column("Título", style="white", max_width=50)

    for i, f in enumerate(note_files, 1):
        # Extraer título de la primera línea
        try:
            first_line = f.read_text(encoding="utf-8").split("\n")[0]
            titulo = first_line.lstrip("# ").strip()[:50]
        except Exception:
            titulo = "?"
        size_kb = f.stat().st_size / 1024
        table.add_row(str(i), f.name, f"{size_kb:.1f} KB", titulo)

    console.print(table)
    console.print(
        f"\n[dim]📁 Directorio: {notes_dir}[/dim]\n"
        f"[dim]💡 Ejecuta 'python main.py ingest' para indexar "
        f"notas nuevas al RAG.[/dim]"
    )


@app.command()
def stats():
    """📊 Muestra estadísticas del vector store."""
    show_banner()

    from vector_store import VectorStoreManager

    store = VectorStoreManager()
    info = store.get_collection_stats()

    table = Table(title="📊 Estadísticas del Vector Store")
    table.add_column("Propiedad", style="cyan")
    table.add_column("Valor", style="green")

    table.add_row("Colección", info["collection_name"])
    table.add_row("Total chunks almacenados", str(info["total_documents"]))
    table.add_row("Directorio", info["persist_directory"])
    table.add_row("Modelo embeddings", config.EMBEDDING_MODEL)
    table.add_row("Max chunk size", str(config.MAX_CHUNK_SIZE))
    table.add_row("Min chunk size", str(config.MIN_CHUNK_SIZE))

    console.print(table)


@app.command()
def clear(
    confirm: bool = typer.Option(
        False, "--yes", "-y", help="Confirmar eliminación sin preguntar",
    ),
):
    """🗑️  Limpia todos los documentos del vector store."""
    show_banner()

    if not confirm:
        confirm = typer.confirm(
            "¿Estás seguro de que quieres eliminar todos los documentos?"
        )

    if confirm:
        from vector_store import VectorStoreManager

        store = VectorStoreManager()
        store.clear_collection()
        console.print("[green]✅ Vector store limpiado.[/green]")
    else:
        console.print("[dim]Operación cancelada.[/dim]")


@app.command()
def migrate():
    """🚀 Migra los datos de ChromaDB a Pinecone."""
    show_banner()

    from vector_store import VectorStoreManager

    store = VectorStoreManager()
    store.migrate_to_pinecone()


if __name__ == "__main__":
    _ensure_utf8_console()
    app()
