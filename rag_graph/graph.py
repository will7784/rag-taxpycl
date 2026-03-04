"""
Grafo RAG Agéntico con LangGraph.
==================================

Flujo inteligente con:
- Detección de artículos específicos → búsqueda por metadata
- Multi-query: expande la pregunta en múltiples búsquedas con vocabulario legal
- Resolución de referencias cruzadas entre leyes
- Grading con fallback (nunca devuelve 0 resultados)

    START
      │
      ▼
    expand_query (LLM genera 3-4 reformulaciones legales)
      │
      ▼
    retrieve (busca con TODAS las queries + metadata si hay artículo)
      │
      ▼
    grade_documents (filtrar irrelevantes, CON FALLBACK)
      │
      ▼
    resolve_cross_references (detectar citas a otras leyes/artículos)
      │
      ├─ ¿refs? ── SÍ ──▶ retrieve_references ──┐
      │                                          │
      ◄──────── NO ──────────────────────────────┘
      │
      ▼
    generate (respuesta con contexto completo)
      │
      ▼
     END
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlparse

from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from rich.console import Console

import config
from vector_store.store import VectorStoreManager

console = Console()

# Limites defensivos para evitar prompts excesivos en artículos con
# jurisprudencia masiva (ej. Art. 31 LIR).
_MAX_RETRIEVED_DOCS = 120
_MAX_CONTEXT_DOCS_FOR_GENERATE = 60
_MAX_CONTEXT_CHARS_FOR_GENERATE = 65000

# ============================================================
# Contexto del agente (se carga desde agente.md)
# ============================================================

_agent_context: str | None = None


def _get_agent_context() -> str:
    """
    Carga el archivo agente.md que contiene el contexto del sistema:
    mapeo de archivos a cuerpos legales, reglas de respuesta, etc.
    """
    global _agent_context
    if _agent_context is None:
        ctx_path = config.AGENT_CONTEXT_FILE
        if ctx_path.exists():
            _agent_context = ctx_path.read_text(encoding="utf-8")
            console.print(
                f"[dim]📋 Contexto del agente cargado ({len(_agent_context)} chars)[/dim]"
            )
        else:
            _agent_context = ""
            console.print(
                "[yellow]⚠️ No se encontró agente.md — sin contexto extra[/yellow]"
            )
    return _agent_context


# ============================================================
# LLM compartido
# ============================================================


def _get_llm(
    max_tokens: int | None = None,
    temperature: float = 0.2,
) -> ChatOpenAI:
    return ChatOpenAI(
        model=config.OPENAI_MODEL,
        api_key=config.OPENAI_API_KEY,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ============================================================
# Estado del grafo
# ============================================================


class RAGState(TypedDict):
    """Estado que fluye a través del grafo RAG."""

    question: str
    expanded_queries: list[str]   # queries reformuladas por el LLM
    context: list[Document]
    cross_ref_queries: list[dict]  # [{"query": str, "filename": str|None}]
    detected_refs_summary: str     # lista legible de TODAS las refs detectadas
    answer: str
    sources: list[str]
    include_derogadas: bool
    top_juris: int


# ============================================================
# Utilidades para detectar artículos en la pregunta
# ============================================================

# Mapeo de nombres comunes de ley → fragmento de filename
_LAW_NAME_TO_FILE = {
    "renta": "DL-824",
    "lir": "DL-824",
    "dl-824": "DL-824",
    "dl 824": "DL-824",
    "decreto ley 824": "DL-824",
    "impuesto a la renta": "DL-824",

    "iva": "DL-825",
    "dl-825": "DL-825",
    "dl 825": "DL-825",
    "decreto ley 825": "DL-825",
    "valor agregado": "DL-825",

    "codigo tributario": "DL-830",
    "código tributario": "DL-830",
    "dl-830": "DL-830",
    "dl 830": "DL-830",
    "decreto ley 830": "DL-830",
}


def _detect_article_in_question(question: str) -> tuple[str | None, str | None]:
    """
    Detecta si la pregunta menciona un artículo específico y opcionalmente
    una ley específica.

    Returns:
        (article_number, filename_filter) o (None, None)
        article_number: ej "10", "31", "14 bis"
        filename_filter: ej "DL-824" o None
    """
    # Buscar "artículo X" o "art. X" o "art X"
    art_match = re.search(
        r"art[íi]culo\s+(\d+[°º]?\s*(?:bis|ter|qu[áa]ter)?)"
        r"|art\.?\s+(\d+[°º]?\s*(?:bis|ter|qu[áa]ter)?)",
        question,
        re.IGNORECASE,
    )

    article_num = None
    if art_match:
        article_num = (art_match.group(1) or art_match.group(2) or "").strip()
        # Limpiar: "10°" → "10"
        article_num = re.sub(r"[°º]", "", article_num).strip()
    else:
        # Fallback para consultas telegráficas tipo "17 numero 8" o "17 nro 8":
        # evita exigir el prefijo "artículo/art.".
        bare_match = re.search(
            r"\b(\d{1,3})\s*(?:n[°º]?|nro\.?|n[úu]mero)\s*\d+\b",
            question,
            re.IGNORECASE,
        )
        if bare_match:
            article_num = bare_match.group(1).strip()

    # Detectar ley mencionada
    filename_filter = None
    question_lower = question.lower()
    for law_name, file_prefix in _LAW_NAME_TO_FILE.items():
        if law_name in question_lower:
            filename_filter = file_prefix
            break

    return article_num, filename_filter


def _detect_subnumber_in_question(question: str, article_num: str | None) -> str | None:
    """
    Detecta subnumeral en consultas tipo:
    - "artículo 33 número 4"
    - "art. 33 N° 4"
    - "33 nro 4"
    """
    if not article_num:
        return None

    art = re.escape(article_num)
    patterns = [
        rf"(?:art[íi]culo|art\.?)\s*{art}[°º]?\s*(?:,|\s)*(?:n[°º]|nro\.?|n[úu]mero)\s*(\d+)\b",
        rf"\b{art}\s*(?:n[°º]|nro\.?|n[úu]mero)\s*(\d+)\b",
    ]
    for p in patterns:
        m = re.search(p, question, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def _doc_matches_juris_scope(
    doc: Document,
    article_num: str | None,
    sub_num: str | None,
    law_filter: str | None = None,
    include_derogadas: bool = False,
) -> bool:
    """
    Verifica si un chunk de jurisprudencia pertenece al alcance solicitado:
    artículo exacto y, opcionalmente, subnumeral exacto.
    """
    if doc.metadata.get("source_type") != "jurisprudencia_sii":
        return True

    text = (doc.page_content or "").lower()
    if (not include_derogadas) and ("estado_vigencia: dejada_sin_efecto" in text):
        return False

    if law_filter:
        law = law_filter.upper().strip()
        def _infer_laws(raw_text: str) -> set[str]:
            inferred: set[str] = set()
            if re.search(
                r"(ley\s+sobre\s+impuesto\s+a\s+la\s+renta|decreto\s+ley\s*n[°º]?\s*824|dl-?824|\blir\b)",
                raw_text,
                re.IGNORECASE,
            ):
                inferred.add("DL-824")
            if re.search(
                r"(c[oó]digo\s+tributario|decreto\s+ley\s*n[°º]?\s*830|dl-?830)",
                raw_text,
                re.IGNORECASE,
            ):
                inferred.add("DL-830")
            if re.search(
                r"(impuesto\s+a\s+las\s+ventas\s+y\s+servicios|decreto\s+ley\s*n[°º]?\s*825|dl-?825|\biva\b)",
                raw_text,
                re.IGNORECASE,
            ):
                inferred.add("DL-825")
            return inferred

        # Intentar leer metadata explícita si existe (futuro enrich).
        m_laws = re.search(r"cuerpos_normativos_relacionados:\s*(.+)", text, re.IGNORECASE)
        if m_laws:
            declared = m_laws.group(1).upper()
            if law not in declared:
                return False
        else:
            # Heurística por contenido para no mezclar artículos homónimos entre leyes.
            mentioned = _infer_laws(text)

            # Si logramos inferir ley(es), exigir match con la ley objetivo.
            # Si no se puede inferir, en circulares exigimos evidencia explícita
            # para evitar mezclar artículos homónimos de leyes distintas.
            if mentioned and law not in mentioned:
                return False
            if not mentioned:
                is_circular_like = (
                    "jurisprudencia_subtype: circular_sii_web" in text
                    or "tipo_pronunciamiento: circular" in text
                )
                if is_circular_like:
                    # Fallback: inferir ley desde el markdown completo de la circular.
                    # Evita falsos negativos por chunks de metadata muy cortos.
                    filename = (doc.metadata.get("filename") or "").strip()
                    if filename:
                        root = config.DOCUMENTS_DIR / "jurisprudencia_sii_circulares"
                        matches = list(root.rglob(filename))
                        if matches:
                            try:
                                full_text = matches[0].read_text(encoding="utf-8", errors="ignore")
                            except Exception:
                                full_text = ""
                            full_laws = _infer_laws(full_text)
                            if full_laws and law not in full_laws:
                                return False
                            if full_laws and law in full_laws:
                                # Ley consistente con la consulta.
                                pass
                            else:
                                # Si no se logra inferir ni desde archivo completo,
                                # preferimos excluir para no contaminar resultados.
                                return False
                        else:
                            return False
                    else:
                        return False

    if article_num:
        art_ok = f"articulo_nombre: {article_num}".lower() in text
        if not art_ok:
            # Fallback para jurisprudencia administrativa multirreferencia:
            # si el documento declara "articulos_relacionados", aceptamos match
            # cuando el articulo consultado aparece explícitamente allí.
            rel_pat = rf"articulos_relacionados:\s*.*\b{re.escape(article_num)}\b"
            if not re.search(rel_pat, text, re.IGNORECASE):
                return False

    if sub_num:
        art = re.escape(article_num or "")
        sub = re.escape(sub_num)
        sub_patterns = [
            rf"\b{art}\s*(?:n[°º]?|nro\.?|n[úu]mero)\s*{sub}\b",
            rf"art[íi]culo\s*{art}[°º]?\s*(?:n[°º]?|nro\.?|n[úu]mero)\s*{sub}\b",
            # Variante frecuente en circulares: "N°s 5, 6 y 8 del artículo 17"
            rf"(?:n[°º]s?|n[úu]meros?)\s*[0-9,\sy]+?\b{sub}\b\s*del\s*art[íi]culo\s*{art}\b",
        ]
        if not any(re.search(p, text, re.IGNORECASE) for p in sub_patterns):
            # Fallback pragmático para metadata resumida en circulares:
            # si "articulos_relacionados" declara ambos números, aceptamos.
            rel_line = re.search(r"articulos_relacionados:\s*(.+)", text, re.IGNORECASE)
            if rel_line:
                rel_text = rel_line.group(1)
                has_art = re.search(rf"\b{art}\b", rel_text, re.IGNORECASE) is not None
                has_sub = re.search(rf"\b{sub}\b", rel_text, re.IGNORECASE) is not None
                if has_art and has_sub:
                    return True
            return False

    return True


def _is_admin_juris_question(question: str) -> bool:
    q = question.lower()
    return any(
        term in q
        for term in (
            "solo jurisprudencia administrativa",
            "solo administrativa",
            "solo sii",
            "sin sentencias",
            "no judicial",
        )
    )


def _doc_is_admin_juris(doc: Document) -> bool:
    """
    Determina si un chunk de jurisprudencia corresponde a doctrina administrativa
    del SII (oficio/circular/resolución) y no a sentencias judiciales.
    """
    if doc.metadata.get("source_type") != "jurisprudencia_sii":
        return True
    text = (doc.page_content or "")
    m = re.search(r"tipo_pronunciamiento:\s*(.+)", text, re.IGNORECASE)
    tipo = (m.group(1).strip().upper() if m else "")
    return (
        "OFICIO" in tipo
        or "CIRCULAR" in tipo
        or "RESOLU" in tipo
    )


# ============================================================
# Patrones para detectar referencias cruzadas entre leyes
# ============================================================

# Sub-patrón reutilizable para capturar un número de artículo completo.
# Captura: "41", "41°", "41 E", "41 H", "41 bis", "74 N° 4", "58 número 3"
_ART_NUM = (
    r"\d+[°º]?"                          # número base: 41, 41°
    r"(?:"
    r"\s+(?:bis|ter|qu[áa]ter|quinquies|sexies)"  # sufijo latino: 41 bis
    r"|\s+[A-Z](?=[\s,\.\)\-])"          # sufijo letra: 41 E, 41 H
    r"|\s+(?:N[°º]?\s*\d+|n[úu]mero\s+\d+)"  # sub-número: 74 N° 4
    r")?"
)
# Un artículo o lista: "41 E", "41 E y 74 N° 4", "200, 201 y 202"
_ART_LIST = rf"{_ART_NUM}(?:\s*(?:y|,|al)\s*{_ART_NUM})*"

_CROSS_REF_PATTERNS = [
    # --- Patrón 1: "artículo X del Código Tributario / Ley..." (inter-ley)
    re.compile(
        rf"art[íi]culos?\s+({_ART_LIST})"
        r"\s+del?\s+"
        r"(C[oó]digo\s+Tributario|"
        r"Ley\s+(?:de\s+(?:la\s+)?)?(?:Renta|IVA|Impuesto[^,\.]*)|"
        r"D\.?L\.?\s*\d+|"
        r"Decreto\s+Ley\s+\d+|"
        r"D\.?F\.?L\.?\s*\d+)",
        re.IGNORECASE,
    ),
    # --- Patrón 2: "Art. X del ..." (variante abreviada, inter-ley)
    re.compile(
        rf"Art\.?\s*({_ART_LIST})"
        r"\s+del?\s+"
        r"(C[oó]digo\s+Tributario|"
        r"Ley\s+(?:de\s+(?:la\s+)?)?(?:Renta|IVA|Impuesto[^,\.]*)|"
        r"D\.?L\.?\s*\d+|"
        r"Decreto\s+Ley\s+\d+|"
        r"D\.?F\.?L\.?\s*\d+)",
        re.IGNORECASE,
    ),
    # --- Patrón 3: "artículos X del mismo código/ley" (misma ley)
    re.compile(
        rf"art[íi]culos?\s+({_ART_LIST})"
        r"\s+(?:del\s+mismo|de\s+(?:esta|la\s+presente))\s+"
        r"(c[oó]digo|ley|decreto)",
        re.IGNORECASE,
    ),
    # --- Patrón 4: "inciso/numeral/letra X del artículo Y"
    re.compile(
        r"(?:inciso|numeral|número|letra)\s+\w+\s+del\s+"
        rf"art[íi]culo\s+({_ART_NUM})"
        r"(?:\s+del?\s+(C[oó]digo\s+Tributario|"
        r"Ley\s+(?:de\s+(?:la\s+)?)?(?:Renta|IVA|Impuesto[^,\.]*)|"
        r"D\.?L\.?\s*\d+))?",
        re.IGNORECASE,
    ),
    # --- Patrón 5: INTRA-LEY — "en el/conforme al/según el artículo X"
    #     Captura referencias dentro de la misma ley que NO mencionan el nombre
    #     Tolerante a acentos faltantes (ej: "segun" vs "según")
    re.compile(
        r"(?:en\s+el\s+|conforme\s+(?:a\s+(?:lo\s+)?)?(?:dispuesto\s+en\s+el\s+)?"
        r"|seg[úu]n\s+(?:el\s+)?|establecid[oa]s?\s+en\s+el\s+"
        r"|se[ñn]alad[oa]s?\s+en\s+el\s+|referid[oa]s?\s+en\s+el\s+"
        r"|dispuesto\s+en\s+el\s+|indicad[oa]s?\s+en\s+el\s+)"
        rf"art[íi]culos?\s+({_ART_LIST})",
        re.IGNORECASE,
    ),
    # --- Patrón 6: "artículo X de la Ley N° XXXXX" (leyes externas con número)
    re.compile(
        rf"art[íi]culos?\s+({_ART_NUM})"
        r"\s+de\s+la\s+"
        r"(Ley\s+N[°º]?\s*[\d\.]+(?:\s+sobre\s+[^,\.]{5,50})?)",
        re.IGNORECASE,
    ),
    # --- Patrón 7: "impuesto/tasa/crédito/facultades del artículo X"
    re.compile(
        r"(?:impuesto|tasa|crédito|retención|obligación|facultades|"
        r"disposiciones|normas|reglas|efectos|términos)"
        r"(?:\s+\w+){0,3}\s+del\s+"
        rf"art[íi]culos?\s+({_ART_NUM})",
        re.IGNORECASE,
    ),
    # --- Patrón 8: "del artículo X" (genérico con contexto)
    # Captura refs como "número 7 del artículo 41 A"
    re.compile(
        r"(?:inciso|numeral|número|letra|literal|párrafo|"
        r"regla|parte|requisito)\s+\S+\s*"
        r"(?:,\s*)?(?:,\s*del\s+|del\s+)"
        rf"art[íi]culos?\s+({_ART_NUM})",
        re.IGNORECASE,
    ),
]


# Mapeo de nombre de ley mencionado en referencia cruzada → prefijo de filename
_REF_LAW_TO_FILE = {
    "código tributario": "DL-830",
    "codigo tributario": "DL-830",
    "ley de renta": "DL-824",
    "ley de la renta": "DL-824",
    "ley renta": "DL-824",
    "ley sobre impuesto a la renta": "DL-824",
    "ley de iva": "DL-825",
    "ley del iva": "DL-825",
    "ley sobre impuesto a las ventas": "DL-825",
    "impuesto a la renta": "DL-824",
    "impuesto al valor agregado": "DL-825",
}


def _resolve_ref_law_to_file(law_name: str) -> str | None:
    """Convierte nombre de ley en referencia cruzada al prefijo de filename."""
    if not law_name:
        return None
    law_lower = law_name.lower().strip()
    # Búsqueda directa
    for key, prefix in _REF_LAW_TO_FILE.items():
        if key in law_lower:
            return prefix
    # Si menciona DL-XXX directamente
    dl_match = re.search(r"d\.?l\.?\s*(\d+)", law_lower)
    if dl_match:
        return f"DL-{dl_match.group(1)}"
    return None


# Regex para extraer artículos individuales de una lista capturada.
# Captura "41 E", "74 N° 4", "41 bis", "58 número 3", "200°", etc.
_INDIVIDUAL_ART_RE = re.compile(
    r"(\d+[°º]?"
    r"(?:\s+(?:bis|ter|qu[áa]ter|quinquies|sexies)"
    r"|\s+[A-Z](?=[\s,\.\)\-]|$)"
    r"|\s+(?:N[°º]?\s*\d+|n[úu]mero\s+\d+))?)",
    re.IGNORECASE,
)


def _extract_cross_references(
    text: str,
    source_filename: str | None = None,
) -> list[dict]:
    """
    Extrae referencias cruzadas y genera queries de búsqueda.

    Retorna lista de dicts: {"query": str, "filename": str|None}
    - Si la referencia menciona otra ley → filename de esa ley
    - Si NO menciona ley (intra-ley) → filename del documento fuente
    """
    results: dict[str, dict] = {}  # key = query text, value = dict

    for pattern in _CROSS_REF_PATTERNS:
        for match in pattern.finditer(text):
            groups = [g for g in match.groups() if g is not None]
            if not groups:
                continue
            article_nums_raw = groups[0]
            law_name = groups[1].strip() if len(groups) > 1 else ""

            # Extraer cada artículo individual (soporta "41 E", "74 N° 4", etc.)
            individual_arts = _INDIVIDUAL_ART_RE.findall(article_nums_raw)
            if not individual_arts:
                # Fallback: buscar solo números planos
                individual_arts = re.findall(r"\d+[°º]?", article_nums_raw)

            # Determinar el prefijo de ley UNA vez por match
            # Si law_name es genérico ("ley", "código", "decreto") sin
            # nombre específico, tratar como intra-ley (= misma ley fuente)
            _GENERIC_LAW_NAMES = {"ley", "código", "codigo", "decreto"}
            is_intra_law = (
                not law_name
                or law_name.lower().strip() in _GENERIC_LAW_NAMES
            )

            if not is_intra_law:
                file_prefix = _resolve_ref_law_to_file(law_name)
            else:
                file_prefix = None
                if source_filename:
                    pfx_match = re.match(r"(DL-\d+)", source_filename)
                    if pfx_match:
                        file_prefix = pfx_match.group(1)

            for art_str in individual_arts:
                art_str = art_str.strip()
                if not art_str:
                    continue
                try:
                    n = int(re.match(r"\d+", art_str).group())
                except (AttributeError, ValueError):
                    continue
                if n < 1 or n > 300:
                    continue

                art_label = art_str

                if not is_intra_law and law_name:
                    query = f"Artículo {art_label} {law_name}"
                else:
                    query = f"Artículo {art_label}"

                if query not in results:
                    results[query] = {
                        "query": query,
                        "filename": file_prefix,
                        "_art_num": n,
                        "_is_intra": is_intra_law,
                    }

    # ── Deduplicación: si un artículo N tiene ref con ley específica,
    #    eliminar la ref intra-ley del mismo artículo N ──
    # Ej: "Art. 64 Código Tributario" (DL-830) + "Art. 64" (DL-824)
    #     → quitar "Art. 64" (DL-824) porque es falso positivo
    specific_art_nums: set[int] = set()
    for ref in results.values():
        if not ref.get("_is_intra"):
            specific_art_nums.add(ref["_art_num"])

    deduped: list[dict] = []
    for ref in results.values():
        if ref.get("_is_intra") and ref["_art_num"] in specific_art_nums:
            continue  # descartar intra-ley si ya tenemos ref específica
        deduped.append({
            "query": ref["query"],
            "filename": ref["filename"],
        })
    return deduped


# ============================================================
# Nodos del grafo
# ============================================================

_vector_store: VectorStoreManager | None = None


def _get_vector_store() -> VectorStoreManager:
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStoreManager()
    return _vector_store


# ── Nodo 1: Expansión Multi-Query ──────────────────────────


def expand_query_node(state: RAGState) -> dict:
    """
    Usa el LLM para reformular la pregunta del usuario en múltiples
    queries optimizadas para buscar en leyes tributarias chilenas.

    Genera 2 tipos de queries:
    - Conceptuales: sinónimos y vocabulario legal técnico
    - Específicas: artículos y cuerpos legales concretos
    """
    console.print("[blue]🧠 Expandiendo consulta con vocabulario legal...[/blue]")

    agent_ctx = _get_agent_context()

    # Detectar si la pregunta pide un artículo específico
    article_num, law_filter = _detect_article_in_question(state["question"])

    if article_num:
        console.print(
            f"  [cyan]🎯 Artículo detectado: Art. {article_num}"
            f"{f' ({law_filter})' if law_filter else ''}[/cyan]"
        )

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "Eres un experto en derecho tributario chileno. Tu tarea es "
                "generar queries de búsqueda optimizadas para encontrar "
                "artículos relevantes en leyes tributarias.\n\n"
                "CONTEXTO DEL SISTEMA (mapeo de archivos y temas clave):\n"
                f"{agent_ctx}\n\n"
                "Genera EXACTAMENTE 4 queries, una por línea:\n"
                "1. Query con el número de artículo EXACTO como aparece en "
                "la ley (formato: 'ARTICULO X°.-' seguido de palabras clave "
                "del tema)\n"
                "2. Query con vocabulario técnico legal (sinónimos formales "
                "del tema)\n"
                "3. Query con el contexto temático del artículo (qué regula, "
                "qué define)\n"
                "4. Query enfocada en el cuerpo legal más relevante y su "
                "estructura (título, párrafo)\n\n"
                "IMPORTANTE:\n"
                "- Usa palabras que aparecerían LITERALMENTE en el texto legal\n"
                "- Incluye el formato 'ARTICULO X°.-' cuando conozcas el número\n"
                "- NO uses lenguaje coloquial, usa terminología de la ley\n"
                "- Cada query en una línea, SIN numeración ni viñetas",
            ),
            (
                "human",
                "Pregunta: {question}\n\nGenera 4 queries de búsqueda:",
            ),
        ]
    )

    chain = prompt | _get_llm() | StrOutputParser()
    raw = chain.invoke({"question": state["question"]})

    expanded = [
        line.strip().lstrip("0123456789.-) ")
        for line in raw.strip().split("\n")
        if line.strip() and len(line.strip()) > 10
    ]

    # Construir lista de queries: original + expansiones
    all_queries = [state["question"]] + expanded[:4]

    # Si detectamos un artículo específico, agregar queries de búsqueda directa
    if article_num:
        # Query exacta con formato de ley
        art_clean = article_num.upper().replace(" ", " ")
        all_queries.append(f"ARTICULO {art_clean}°.-")
        all_queries.append(f"Se considerarán artículo {article_num}")

    console.print(f"  [dim]📝 Queries ({len(all_queries)}):[/dim]")
    for i, q in enumerate(all_queries):
        tag = "original" if i == 0 else f"exp-{i}"
        console.print(f"    [dim]{tag}: {q}[/dim]")

    return {"expanded_queries": all_queries}


# ── Nodo 2: Retrieval multi-query + metadata ──────────────


def _detect_articles_in_queries(queries: list[str]) -> list[tuple[str, str | None]]:
    """
    Escanea TODAS las queries (incluidas las expandidas por el LLM)
    buscando menciones de artículos específicos.

    Retorna lista de (article_num, law_file_prefix) únicos.
    Ej: [("17", "DL-824"), ("58", None)]
    """
    found: dict[str, str | None] = {}  # art_num → file_prefix
    art_pattern = re.compile(
        r"ARTICULO\s+(\d+)[°º]?"
        r"|Art[íi]culo\s+(\d+)[°º]?"
        r"|art\.?\s+(\d+)[°º]?",
        re.IGNORECASE,
    )
    for q in queries:
        for m in art_pattern.finditer(q):
            num = (m.group(1) or m.group(2) or m.group(3) or "").strip()
            if not num:
                continue
            try:
                n = int(num)
            except ValueError:
                continue
            if n < 1 or n > 300:
                continue
            if num not in found:
                # Intentar detectar ley en la misma query
                _, law = _detect_article_in_question(q)
                found[num] = law
    return list(found.items())


def retrieve_node(state: RAGState) -> dict:
    """
    Busca con TODAS las queries expandidas y une los resultados.
    Detecta artículos mencionados tanto en la pregunta original como
    en las queries expandidas por el LLM, y hace búsqueda directa
    por contenido para traer TODOS los chunks de esos artículos.
    """
    queries = state.get("expanded_queries", [state["question"]])
    console.print(
        f"[blue]🔍 Buscando con {len(queries)} queries...[/blue]"
    )

    store = _get_vector_store()
    include_derogadas = bool(state.get("include_derogadas", False))
    all_docs: list[Document] = []
    all_sources: list[str] = []
    seen: set[str] = set()  # evitar duplicados por contenido

    _FILE_MAP = {
        "DL-824": "DL-824_31-DIC-1974.pdf",
        "DL-825": "DL-825_31-DIC-1974.pdf",
        "DL-830": "DL-830_31-DIC-1974_codigo tributario.pdf",
    }

    # === Detectar artículos en pregunta original Y en queries expandidas ===
    article_num, law_filter = _detect_article_in_question(state["question"])
    sub_num = _detect_subnumber_in_question(state["question"], article_num)

    # También buscar artículos en las queries del LLM
    articles_from_queries = _detect_articles_in_queries(queries)

    # Unir: primero el de la pregunta (prioridad), luego los de queries
    articles_to_search: list[tuple[str, str | None]] = []
    seen_arts: set[str] = set()

    if article_num:
        articles_to_search.append((article_num, law_filter))
        seen_arts.add(article_num)

    for art_num, art_law in articles_from_queries:
        if art_num not in seen_arts:
            articles_to_search.append((art_num, art_law or law_filter))
            seen_arts.add(art_num)

    # === Búsqueda directa por contenido para cada artículo detectado ===
    for art_num, art_law in articles_to_search:
        exact_filename = _FILE_MAP.get(art_law) if art_law else None

        console.print(
            f"  [cyan]📂 Buscando Art. {art_num}° en contenido"
            f"{f' ({exact_filename})' if exact_filename else ''}...[/cyan]"
        )

        search_texts = [
            f"ARTICULO {art_num}°",
            f"Artículo {art_num}°",
            f"Artículo {art_num}.",
            f"ARTICULO {art_num}.",
        ]
        for search_text in search_texts:
            meta_docs = store.search_by_document_content(
                contains=search_text,
                filename_filter=exact_filename,
                limit=50,
            )
            for doc in meta_docs:
                fingerprint = doc.page_content[:150]
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    all_docs.append(doc)
                    source_info = (
                        f"{doc.metadata.get('filename', '?')} | "
                        f"{doc.metadata.get('hierarchy_path', '')} "
                        f"(contenido directo)"
                    )
                    all_sources.append(source_info)
                    console.print(f"  [dim]📂 {source_info}[/dim]")

        # === Búsqueda por hierarchy_path para obtener TODOS los sub-chunks ===
        # Si el artículo tiene sub-secciones (letras, incisos), están en
        # chunks con hierarchy_path que CONTIENE "ARTICULO X°" o "Artículo X.-"
        # Variantes necesarias:
        #   DL-824: "ARTICULO 31°"      (ALL-CAPS, con °)
        #   DL-830: "Artículo 4°"       (Title Case, con °)
        #   DL-830: "Artículo 10.-"     (Title Case, sin °)
        #   DL-825: "Artículo 4°"       (Title Case, con °)
        for variant in [
            f"ARTICULO {art_num}°",
            f"Artículo {art_num}°",
            f"Artículo {art_num}.-",
            f"Artículo {art_num} ",
        ]:
            hier_docs = store.search_by_hierarchy(
                hierarchy_contains=variant,
                filename_filter=exact_filename,
                limit=150,
            )
            for doc in hier_docs:
                fingerprint = doc.page_content[:150]
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    all_docs.append(doc)
                    source_info = (
                        f"{doc.metadata.get('filename', '?')} | "
                        f"{doc.metadata.get('hierarchy_path', '')} "
                        f"(hierarchy)"
                    )
                    all_sources.append(source_info)
                    console.print(f"  [dim]📂 {source_info}[/dim]")

    # === Traer jurisprudencia del artículo detectado (si existe) ===
    # Esto aplica incluso si la pregunta NO menciona explícitamente "jurisprudencia"
    # para permitir respuestas tipo: "explícame el artículo 10" + jurisprudencia asociada.
    if article_num:
        # Priorizar jurisprudencia administrativa del SII (oficios/circulares/resoluciones),
        # porque puede venir como documento multirreferencia y quedar fuera por límites.
        admin_candidates: list[Document] = []
        for admin_tipo in ("Circular", "Oficio", "Resolución", "Resolucion"):
            admin_candidates.extend(
                store.search_by_source_type(
                    source_type="jurisprudencia_sii",
                    contains=f"tipo_pronunciamiento: {admin_tipo}",
                    limit=300,
                )
            )

        jur_docs = store.search_by_source_type(
            source_type="jurisprudencia_sii",
            contains=f"articulo_nombre: {article_num}",
            limit=200,
        )
        # También incluir corpus administrativo "multirreferencia"
        # (ej.: circular cuyo articulo principal es otro, pero referencia
        # el artículo consultado en "articulos_relacionados").
        jur_docs_rel = store.search_by_source_type(
            source_type="jurisprudencia_sii",
            contains="articulos_relacionados:",
            limit=300,
        )
        jur_docs.extend(jur_docs_rel)
        jur_docs.extend(admin_candidates)
        for doc in jur_docs:
            if not _doc_matches_juris_scope(
                doc,
                article_num,
                sub_num,
                law_filter,
                include_derogadas=include_derogadas,
            ):
                continue
            fingerprint = doc.page_content[:150]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            all_docs.append(doc)
            source_info = (
                f"{doc.metadata.get('filename', '?')} | "
                f"{doc.metadata.get('hierarchy_path', '')} "
                f"(jurisprudencia-artículo)"
            )
            all_sources.append(source_info)
            console.print(f"  [dim]⚖️ {source_info}[/dim]")

    # === Si la pregunta pide jurisprudencia, priorizar corpus jurisprudencial ===
    q_lower = state["question"].lower()
    asks_jurisprudencia = any(
        term in q_lower
        for term in (
            "jurisprudencia",
            "administrativa",
            "oficio",
            "circular",
            "resolución",
            "resolucion",
        )
    )
    asks_admin_only = _is_admin_juris_question(state["question"])
    if asks_jurisprudencia:
        art_for_juris, law_for_juris = _detect_article_in_question(state["question"])
        sub_for_juris = _detect_subnumber_in_question(state["question"], art_for_juris)
        jur_docs: list[Document] = []
        admin_candidates: list[Document] = []
        for admin_tipo in ("Circular", "Oficio", "Resolución", "Resolucion"):
            admin_candidates.extend(
                store.search_by_source_type(
                    source_type="jurisprudencia_sii",
                    contains=f"tipo_pronunciamiento: {admin_tipo}",
                    limit=300,
                )
            )
        if art_for_juris:
            jur_docs = store.search_by_source_type(
                source_type="jurisprudencia_sii",
                contains=f"articulo_nombre: {art_for_juris}",
                limit=200,
            )
            jur_docs_rel = store.search_by_source_type(
                source_type="jurisprudencia_sii",
                contains="articulos_relacionados:",
                limit=300,
            )
            jur_docs.extend(jur_docs_rel)
            jur_docs.extend(admin_candidates)
        if not jur_docs and not art_for_juris:
            thematic_cands: list[str] = []
            q_low = state["question"].lower()
            if re.search(r"\bacci[oó]n(?:es)?\b", q_low):
                thematic_cands = ["17", "107", "18"]

            thematic_jur: list[Document] = []
            thematic_seen: set[str] = set()
            for art_cand in thematic_cands:
                docs_a = store.search_by_source_type(
                    source_type="jurisprudencia_sii",
                    contains=f"articulo_nombre: {art_cand}",
                    limit=180,
                )
                docs_r = store.search_by_source_type(
                    source_type="jurisprudencia_sii",
                    contains="articulos_relacionados:",
                    limit=220,
                )
                for d in docs_a + docs_r:
                    fp = d.page_content[:150]
                    if fp in thematic_seen:
                        continue
                    thematic_seen.add(fp)
                    thematic_jur.append(d)

            # Para consultas temáticas sin artículo explícito, priorizar
            # jurisprudencia por similitud semántica (más idónea al intent).
            sem_jur: list[Document] = []
            sem_seen: set[str] = set()
            for q in queries[:5]:
                results = store.similarity_search_with_score(q, k=24)
                for doc, _score in results:
                    if doc.metadata.get("source_type") != "jurisprudencia_sii":
                        continue
                    fp = doc.page_content[:150]
                    if fp in sem_seen:
                        continue
                    sem_seen.add(fp)
                    sem_jur.append(doc)
            # Complementar con búsqueda léxica por términos temáticos de la pregunta.
            q_terms = {
                t for t in re.findall(r"[a-záéíóúñ]{4,}", state["question"].lower())
                if t not in {
                    "como", "cuales", "cual", "sobre", "norma", "normas",
                    "tributacion", "tributaria", "tributarias", "impuesto",
                    "jurisprudencia", "articulo", "ley", "codigo", "chile",
                }
            }
            kw_jur: list[Document] = []
            for term in list(q_terms)[:6]:
                docs_kw = store.search_by_source_type(
                    source_type="jurisprudencia_sii",
                    contains=term,
                    limit=120,
                )
                for d in docs_kw:
                    fp = d.page_content[:150]
                    if fp in sem_seen:
                        continue
                    sem_seen.add(fp)
                    kw_jur.append(d)

            jur_docs = thematic_jur + sem_jur + kw_jur
            if not jur_docs:
                jur_docs = store.search_by_source_type(
                    source_type="jurisprudencia_sii",
                    contains=None,
                    limit=200,
                )
            jur_docs.extend(admin_candidates)

        for doc in jur_docs:
            if not _doc_matches_juris_scope(
                doc,
                art_for_juris,
                sub_for_juris,
                law_for_juris or law_filter,
                include_derogadas=include_derogadas,
            ):
                continue
            if asks_admin_only and not _doc_is_admin_juris(doc):
                continue
            fingerprint = doc.page_content[:150]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            all_docs.append(doc)
            source_info = (
                f"{doc.metadata.get('filename', '?')} | "
                f"{doc.metadata.get('hierarchy_path', '')} "
                f"(jurisprudencia)"
            )
            all_sources.append(source_info)
            console.print(f"  [dim]⚖️ {source_info}[/dim]")

    if all_docs:
        console.print(
            f"  [green]✅ {len(all_docs)} chunks encontrados por "
            f"contenido + jerarquía[/green]"
        )

    # === Búsqueda semántica con cada query ===
    for query in queries:
        results = store.similarity_search_with_score(query, k=8)
        for doc, score in results:
            # Evitar ruido de jurisprudencia fuera del artículo/subnumeral pedido.
            if doc.metadata.get("source_type") == "jurisprudencia_sii":
                if not _doc_matches_juris_scope(
                    doc,
                    article_num,
                    sub_num,
                    law_filter,
                    include_derogadas=include_derogadas,
                ):
                    continue
                if asks_admin_only and not _doc_is_admin_juris(doc):
                    continue

            # Deduplicar por los primeros 150 chars del contenido
            fingerprint = doc.page_content[:150]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)

            all_docs.append(doc)
            source_info = (
                f"{doc.metadata.get('filename', '?')} | "
                f"{doc.metadata.get('hierarchy_path', '')} "
                f"(sim: {1 - score:.2f})"
            )
            all_sources.append(source_info)
            console.print(f"  [dim]📎 {source_info}[/dim]")

    if len(all_docs) > _MAX_RETRIEVED_DOCS:
        console.print(
            f"  [yellow]⚠️ Limite defensivo: recortando contexto de "
            f"{len(all_docs)} a {_MAX_RETRIEVED_DOCS} chunks[/yellow]"
        )
        all_docs = all_docs[:_MAX_RETRIEVED_DOCS]
        all_sources = all_sources[:_MAX_RETRIEVED_DOCS]

    console.print(
        f"  [green]{len(all_docs)} chunks únicos recuperados[/green]"
    )
    return {"context": all_docs, "sources": all_sources}


# ── Nodo 3: Filtrar irrelevantes con LLM ───────────────────


def grade_documents_node(state: RAGState) -> dict:
    """
    Filtra documentos irrelevantes usando el LLM como juez.
    Evalúa cada chunk contra la pregunta y descarta los que no aportan.

    FALLBACK: Si el LLM rechaza TODO, mantiene los mejores chunks
    (los que vinieron de búsqueda por metadata o los primeros N).
    """
    console.print("[blue]📊 Evaluando relevancia con LLM...[/blue]")

    documents = state["context"]
    if not documents:
        return {"context": []}

    question = state["question"]
    q_lower = question.lower()
    asks_jurisprudencia = any(
        term in q_lower
        for term in (
            "jurisprudencia",
            "administrativa",
            "oficio",
            "circular",
            "resolución",
            "resolucion",
        )
    )
    asks_admin_only = _is_admin_juris_question(question)
    article_num, law_filter = _detect_article_in_question(question)
    sub_num = _detect_subnumber_in_question(question, article_num)
    include_derogadas = bool(state.get("include_derogadas", False))

    # Construir batch de evaluación
    grading_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "Eres un evaluador de relevancia para un sistema de búsqueda "
                "en legislación tributaria chilena.\n\n"
                "Referencia de cuerpos legales:\n"
                "- DL-824 = Ley de Renta (LIR)\n"
                "- DL-825 = Ley de IVA\n"
                "- DL-830 = Código Tributario\n\n"
                "Se te dará una PREGUNTA y un FRAGMENTO de ley. Debes "
                "determinar si el fragmento contiene información ÚTIL para "
                "responder la pregunta.\n\n"
                "Responde SOLO con 'SI' o 'NO'.\n"
                "- SI: El fragmento contiene artículos, definiciones, "
                "procedimientos o reglas directamente relevantes para la pregunta. "
                "También SI si el fragmento es PARTE del artículo o tema "
                "que se pregunta (ej: incisos, letras, numerales del artículo).\n"
                "- NO: El fragmento claramente trata otro tema sin relación.\n\n"
                "IMPORTANTE: Sé GENEROSO con la relevancia. Si hay CUALQUIER "
                "conexión temática con la pregunta, responde SI. Es mejor "
                "incluir un fragmento dudoso que perder información útil.",
            ),
            (
                "human",
                "PREGUNTA: {question}\n\n"
                "FRAGMENTO (metadata: {metadata}):\n{document}\n\n"
                "¿Es relevante? (SI/NO):",
            ),
        ]
    )

    llm = _get_llm()
    chain = grading_prompt | llm | StrOutputParser()

    relevant: list[Document] = []
    for doc in documents:
        # Filtro duro para jurisprudencia cuando se consulta un artículo específico:
        # no permitimos jurisprudencia de otros artículos/subnumerales.
        if doc.metadata.get("source_type") == "jurisprudencia_sii" and article_num:
            if not _doc_matches_juris_scope(
                doc,
                article_num,
                sub_num,
                law_filter,
                include_derogadas=include_derogadas,
            ):
                continue
            if asks_admin_only and not _doc_is_admin_juris(doc):
                continue

        # Si la pregunta pide jurisprudencia, conservar siempre
        # los chunks etiquetados como jurisprudencia_sii.
        if asks_jurisprudencia and doc.metadata.get("source_type") == "jurisprudencia_sii":
            relevant.append(doc)
            continue

        # Si se detectó artículo en la pregunta, conservar jurisprudencia
        # asociada al mismo artículo aunque no se pida explícitamente.
        if article_num and doc.metadata.get("source_type") == "jurisprudencia_sii":
            if _doc_matches_juris_scope(
                doc,
                article_num,
                sub_num,
                law_filter,
                include_derogadas=include_derogadas,
            ):
                relevant.append(doc)
                continue

        # Truncar contenido largo para el grading
        content_preview = doc.page_content[:2000]
        meta_info = (
            f"Archivo: {doc.metadata.get('filename', '?')}, "
            f"Sección: {doc.metadata.get('hierarchy_path', '?')}"
        )
        try:
            result = chain.invoke({
                "question": question,
                "document": content_preview,
                "metadata": meta_info,
            })
            is_relevant = "SI" in result.upper() or "SÍ" in result.upper()
        except Exception:
            is_relevant = True  # En caso de error, mantener el chunk

        if is_relevant:
            relevant.append(doc)

    console.print(
        f"  [dim]{len(relevant)}/{len(documents)} relevantes "
        f"({len(documents) - len(relevant)} descartados)[/dim]"
    )

    # === FALLBACK: Si todo fue descartado, mantener los mejores ===
    if not relevant and documents:
        # Priorizar chunks que vinieron de búsqueda por metadata
        # (los primeros en la lista si se encontró un artículo específico)
        fallback_count = min(8, len(documents))
        relevant = documents[:fallback_count]
        console.print(
            f"  [yellow]⚠️ Fallback: manteniendo {fallback_count} "
            f"chunks (el grader rechazó todo)[/yellow]"
        )

    return {"context": relevant}


# ── Nodo 4: Detectar referencias cruzadas ──────────────────


def resolve_cross_references_node(state: RAGState) -> dict:
    """
    Detecta referencias cruzadas en los chunks recuperados.
    Usa DOS estrategias:
    1. REGEX: detecta menciones explícitas ("artículo 58", "Art. 64 CT")
    2. LLM: identifica artículos temáticamente relacionados que no se
       mencionan explícitamente pero complementan la norma analizada.
    """
    console.print("[blue]🔗 Detectando referencias cruzadas...[/blue]")

    # ── Estrategia 1: REGEX (referencias explícitas en el texto) ──
    all_refs: dict[str, dict] = {}  # dedup por query text
    for doc in state["context"]:
        source_file = doc.metadata.get("filename", "")
        refs = _extract_cross_references(doc.page_content, source_file)
        for ref in refs:
            key = ref["query"]
            if key not in all_refs:
                all_refs[key] = ref

    console.print(
        f"  [dim]Regex: {len(all_refs)} refs explícitas[/dim]"
    )

    # ── Estrategia 2: LLM EXTRACTOR (lee el texto real y encuentra refs) ──
    # En vez de "imaginar" artículos relacionados, el LLM lee los chunks
    # recuperados y extrae TODAS las referencias que el regex no detectó.
    agent_ctx = _get_agent_context()

    # Detectar ley principal del análisis
    source_files = set()
    for doc in state["context"]:
        source_files.add(doc.metadata.get("filename", "?"))

    primary_law_prefix = None
    for fname in source_files:
        pfx = re.match(r"(DL-\d+)", fname)
        if pfx:
            primary_law_prefix = pfx.group(1)
            break

    # Normalizar refs regex: si quedaron sin ley, asumir ley principal del
    # contexto para evitar "None" en salida y búsquedas sin filtro.
    if primary_law_prefix:
        for ref in all_refs.values():
            if not ref.get("filename"):
                ref["filename"] = primary_law_prefix

    # Concatenar el texto de los chunks (limitado para no exceder tokens)
    chunks_text = "\n---\n".join(
        doc.page_content[:2000] for doc in state["context"][:6]
    )

    llm_extract_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                f"{agent_ctx}\n\n"
                "Eres un analista legal experto. Tienes DOS tareas:\n\n"
                "TAREA 1 - EXTRACCIÓN: Lee el texto y extrae TODAS las "
                "menciones de artículos que aparezcan, incluyendo:\n"
                "- 'artículo X', 'Art. X', 'artículos X e Y'\n"
                "- Con número completo: '41 E' no solo '41'\n"
                "- Con sub-número: '74 N° 4' no solo '74'\n"
                "- Busca en TODO el texto, no solo al inicio\n\n"
                "TAREA 2 - COMPLEMENTARIOS (máx 3): Identifica artículos "
                "que NO están en el texto pero son ESENCIALES:\n"
                "- Art. que DEFINE conceptos usados (ej: Art. 11 define "
                "'bienes situados en Chile' que Art. 10 usa)\n"
                "- Art. de RETENCIÓN del impuesto generado (ej: Art. 74 "
                "N° 4 para retención de Impuesto Adicional)\n"
                "- SOLO artículos con conexión DIRECTA, no genéricos\n\n"
                "Formato (un artículo por línea):\n"
                "ART_NUM | LEY | TIPO | RAZÓN\n\n"
                "Donde TIPO es: EXTRAIDO (del texto) o COMPLEMENTARIO\n\n"
                "Ejemplo:\n"
                "41 E | DL-824 | EXTRAIDO | 'facultades del artículo 41 E'\n"
                "11 | DL-824 | COMPLEMENTARIO | Define 'bienes situados "
                "en Chile' usado en Art. 10\n\n"
                "Si no hay adicionales, responde: NINGUNA",
            ),
            (
                "human",
                "Texto legal a analizar:\n{text}\n\n"
                "Referencias ya detectadas (NO repetir):\n"
                "{existing_refs}\n\n"
                "Artículos extraídos y complementarios:",
            ),
        ]
    )

    existing_refs_str = "\n".join(
        f"- {ref['query']} ({ref.get('filename', 'misma ley')})"
        for ref in all_refs.values()
    )

    try:
        chain = llm_extract_prompt | _get_llm(temperature=0.1) | StrOutputParser()
        llm_result = chain.invoke({
            "text": chunks_text,
            "existing_refs": existing_refs_str or "(ninguna)",
        })

        # Parsear respuesta del LLM
        if "NINGUNA" not in llm_result.upper():
            for line in llm_result.strip().split("\n"):
                line = line.strip().lstrip("- •")
                if "|" not in line:
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 2:
                    continue
                art_label = parts[0].strip()
                law_info = parts[1].strip() if len(parts) > 1 else ""

                # Validar que es un número de artículo razonable
                num_match = re.match(r"\d+", art_label)
                if not num_match:
                    continue
                n = int(num_match.group())
                if n < 1 or n > 300:
                    continue

                query = f"Artículo {art_label}"

                # Resolver ley
                file_prefix = _resolve_ref_law_to_file(law_info)
                if not file_prefix:
                    dl_match = re.match(r"DL-\d+", law_info)
                    if dl_match:
                        file_prefix = dl_match.group()
                # Si dice "misma ley" o no especifica → ley principal
                if not file_prefix and primary_law_prefix:
                    if "misma" in law_info.lower() or not law_info:
                        file_prefix = primary_law_prefix

                if query not in all_refs:
                    all_refs[query] = {
                        "query": query,
                        "filename": file_prefix,
                    }
                    console.print(
                        f"  [cyan]🔍 LLM extrajo: {query} "
                        f"({file_prefix or '?'})[/cyan]"
                    )
    except Exception as e:
        console.print(f"  [yellow]⚠️ LLM extractor falló: {e}[/yellow]")

    # ── Filtrar: no buscar artículos que ya tenemos ──
    existing_titles = {
        doc.metadata.get("section_title", "").lower()
        for doc in state["context"]
    }

    new_queries: list[dict] = []
    for ref in all_refs.values():
        art_num = re.search(r"\d+", ref["query"])
        if art_num:
            art_pattern = f"artículo {art_num.group()}"
            already_have = any(
                art_pattern.lower() in title for title in existing_titles
            )
            if not already_have:
                new_queries.append(ref)

    if new_queries:
        console.print(
            f"  [cyan]📌 {len(new_queries)} refs cruzadas totales "
            f"(regex + LLM):[/cyan]"
        )
        for ref in new_queries[:15]:
            law_info = ref.get("filename") or "misma ley"
            console.print(
                f"    [dim]→ {ref['query']} (buscar en: {law_info})[/dim]"
            )
    else:
        console.print("  [dim]Sin referencias cruzadas adicionales[/dim]")

    # Generar resumen legible de TODAS las refs (regex + LLM) para el generate
    refs_summary_lines = []
    for ref in all_refs.values():
        fname = ref.get("filename") or "misma ley"
        refs_summary_lines.append(f"- {ref['query']} ({fname})")
    refs_summary = "\n".join(refs_summary_lines) if refs_summary_lines else ""

    return {
        "cross_ref_queries": new_queries,
        "detected_refs_summary": refs_summary,
    }


# ── Nodo 5: Buscar artículos referenciados ─────────────────


def retrieve_references_node(state: RAGState) -> dict:
    """
    Busca los artículos referenciados por las referencias cruzadas.
    Usa el filename_filter para buscar en la ley correcta:
    - Refs con ley explícita → busca en esa ley
    - Refs intra-ley (sin nombre) → busca en la misma ley fuente
    """
    ref_items = state.get("cross_ref_queries", [])
    if not ref_items:
        return {}

    console.print(
        f"[blue]🔍 Buscando {len(ref_items)} artículos referenciados...[/blue]"
    )

    # Mapa de prefijo DL → filename exacto
    _FILE_MAP = {
        "DL-824": "DL-824_31-DIC-1974.pdf",
        "DL-825": "DL-825_31-DIC-1974.pdf",
        "DL-830": "DL-830_31-DIC-1974_codigo tributario.pdf",
    }

    store = _get_vector_store()
    new_docs: list[Document] = []
    new_sources: list[str] = []
    seen: set[str] = {doc.page_content[:150] for doc in state["context"]}

    for ref in ref_items[:15]:
        query = ref["query"]
        file_prefix = ref.get("filename")  # ej: "DL-824" o None
        exact_filename = _FILE_MAP.get(file_prefix) if file_prefix else None

        # Intentar búsqueda por contenido primero (filtrada por ley)
        # Limitamos a 2 chunks por ref para no saturar el contexto
        art_match = re.search(r"(\d+)", query)
        ref_docs_count = 0
        max_per_ref = 2  # máx chunks por referencia cruzada

        if art_match:
            art_num = art_match.group(1)
            for variant in [
                f"ARTICULO {art_num}°",
                f"Artículo {art_num}°",
                f"Artículo {art_num}.-",
                f"Artículo {art_num} ",
            ]:
                if ref_docs_count >= max_per_ref:
                    break
                meta_docs = store.search_by_document_content(
                    contains=variant,
                    filename_filter=exact_filename,
                    limit=2,
                )
                for doc in meta_docs:
                    if ref_docs_count >= max_per_ref:
                        break
                    fp = doc.page_content[:150]
                    if fp not in seen:
                        seen.add(fp)
                        new_docs.append(doc)
                        ref_docs_count += 1
                        law_tag = f" [{file_prefix}]" if file_prefix else ""
                        source_info = (
                            f"{doc.metadata.get('filename', '?')} | "
                            f"{doc.metadata.get('hierarchy_path', '')} "
                            f"(ref cruzada{law_tag}, contenido)"
                        )
                        new_sources.append(source_info)
                        console.print(f"  [dim]🔗 {source_info}[/dim]")

        # Búsqueda semántica solo si no encontramos por contenido
        if ref_docs_count == 0:
            results = store.similarity_search_with_score(query, k=2)
            for doc, score in results:
                if ref_docs_count >= max_per_ref:
                    break
                if exact_filename:
                    doc_file = doc.metadata.get("filename", "")
                    if doc_file != exact_filename:
                        continue
                fp = doc.page_content[:150]
                if fp not in seen:
                    seen.add(fp)
                    new_docs.append(doc)
                    ref_docs_count += 1
                    law_tag = f" [{file_prefix}]" if file_prefix else ""
                    source_info = (
                        f"{doc.metadata.get('filename', '?')} | "
                        f"{doc.metadata.get('hierarchy_path', '')} "
                        f"(ref cruzada{law_tag}, sim: {1 - score:.2f})"
                    )
                    new_sources.append(source_info)
                    console.print(f"  [dim]🔗 {source_info}[/dim]")

    console.print(
        f"  [green]+ {len(new_docs)} chunks por refs cruzadas[/green]"
    )

    return {
        "context": state["context"] + new_docs,
        "sources": state["sources"] + new_sources,
    }


# ── Nodo 6: Generar respuesta ─────────────────────────────


def generate_node(state: RAGState) -> dict:
    """Genera la respuesta con todo el contexto reunido."""
    console.print("[blue]🤖 Generando respuesta...[/blue]")

    question = state["question"]
    context = state["context"]
    q_lower = question.lower()
    asks_jurisprudencia = any(
        term in q_lower
        for term in (
            "jurisprudencia",
            "administrativa",
            "oficio",
            "circular",
            "resolución",
            "resolucion",
        )
    )
    asks_admin_only = _is_admin_juris_question(question)
    question_article_num, question_law_filter = _detect_article_in_question(question)
    question_sub_num = _detect_subnumber_in_question(question, question_article_num)
    include_derogadas = bool(state.get("include_derogadas", False))
    top_juris = max(1, min(20, int(state.get("top_juris", 8) or 8)))

    if not context:
        return {
            "answer": (
                "No encontré información relevante en los documentos cargados "
                "para responder tu pregunta. Intenta reformular la consulta o "
                "asegúrate de haber cargado los documentos correctos."
            )
        }

    agent_ctx = _get_agent_context()
    detected_refs = state.get("detected_refs_summary", "")

    def _format_source(doc: Document) -> str:
        source_type = doc.metadata.get("source_type", "ley_oficial")
        if source_type == "nota_curada":
            tag = "📝 NOTA CURADA"
        elif source_type == "jurisprudencia_sii":
            tag = "⚖️ JURISPRUDENCIA SII"
        else:
            tag = "Fuente"
        return (
            f"[{tag}: {doc.metadata.get('filename', 'N/A')} | "
            f"{doc.metadata.get('hierarchy_path', 'N/A')}]\n{doc.page_content}"
        )

    # Presupuesto de contexto para no exceder limites de tokens del modelo.
    context_blocks: list[str] = []
    context_chars = 0
    for doc in context[:_MAX_CONTEXT_DOCS_FOR_GENERATE]:
        block = _format_source(doc)
        block_len = len(block)
        if context_chars and (context_chars + block_len) > _MAX_CONTEXT_CHARS_FOR_GENERATE:
            break
        context_blocks.append(block)
        context_chars += block_len
    context_text = "\n\n---\n\n".join(context_blocks)

    def _normalize_pron_code(raw_code: str) -> str:
        """
        Convierte códigos como GR-15-00061-2019 a formato legible 61-2019.
        Si no matchea patrón esperado, retorna el código original.
        """
        m = re.search(r"(?:[A-Z]{1,4}-)?\d{1,4}-(\d+)-(\d{4})$", raw_code.strip(), re.IGNORECASE)
        if not m:
            return raw_code.strip()
        num = str(int(m.group(1)))  # quita ceros a la izquierda
        year = m.group(2)
        return f"{num}-{year}"

    def _build_pron_label(tipo: str, codigo: str) -> str:
        tipo_norm = (tipo or "").strip().upper()
        codigo_norm = _normalize_pron_code(codigo or "N/A")

        if codigo_norm.upper().startswith("OFICIO "):
            return codigo_norm
        if codigo_norm.upper().startswith("CIRCULAR "):
            return codigo_norm
        if codigo_norm.upper().startswith("RESOLUCION "):
            return codigo_norm
        if codigo_norm.upper().startswith("SENTENCIA "):
            return codigo_norm

        if "OFICIO" in tipo_norm:
            return f"OFICIO {codigo_norm}"
        if "CIRCULAR" in tipo_norm:
            return f"CIRCULAR {codigo_norm}"
        if "RESOLU" in tipo_norm:
            return f"RESOLUCION {codigo_norm}"
        if "SENTENCIA" in tipo_norm:
            return f"SENTENCIA {codigo_norm}"
        return f"{tipo_norm or 'PRONUNCIAMIENTO'} {codigo_norm}"

    def _tipo_priority(tipo: str) -> int:
        t = (tipo or "").strip().upper()
        if "OFICIO" in t:
            return 0
        if "CIRCULAR" in t:
            return 1
        if "RESOLU" in t:
            return 2
        if "SENTENCIA" in t:
            return 3
        return 4

    def _semantic_overlap_score(question_text: str, item: dict[str, str]) -> int:
        q_tokens = {
            t for t in re.findall(r"[a-záéíóúñ0-9]{3,}", question_text.lower())
            if t not in {"articulo", "jurisprudencia", "impuesto", "ley"}
        }
        hay = " ".join(
            [
                item.get("label", ""),
                item.get("codigo", ""),
                item.get("tipo", ""),
                item.get("resumen", ""),
            ]
        ).lower()
        return sum(1 for t in q_tokens if t in hay)

    def _sanitize_juris_pdf_url(raw_url: str) -> str:
        """
        Sanitiza links de jurisprudencia para evitar URLs inventadas/no oficiales.
        Si no pasa validación, retorna 'N/A' y el render final mostrará
        'No disponible'.
        """
        value = (raw_url or "").strip()
        if not value or value.upper() in {"N/A", "NO DISPONIBLE"}:
            return "N/A"

        try:
            parsed = urlparse(value)
        except Exception:
            return "N/A"

        if parsed.scheme not in {"http", "https"}:
            return "N/A"

        host = (parsed.hostname or "").lower().strip()
        if not host:
            return "N/A"

        # Bloqueo explícito de placeholders/locales.
        if host in set(config.BLOCKED_JURIS_PDF_HOSTS):
            return "N/A"

        # Whitelist de dominios oficiales/esperados para fuentes legales chilenas.
        allowed_domains = config.ALLOWED_JURIS_PDF_DOMAINS
        if not any(host == d or host.endswith(f".{d}") for d in allowed_domains):
            return "N/A"

        return value

    # Extraer evidencia de jurisprudencia disponible en contexto
    jur_items: list[dict[str, str]] = []
    jur_seen: set[str] = set()
    full_juris_cache: dict[str, str] = {}

    def _load_full_juris_text(filename: str) -> str:
        if not filename:
            return ""
        if filename in full_juris_cache:
            return full_juris_cache[filename]
        candidates = []
        for folder in ("jurisprudencia_sii", "jurisprudencia_sii_circulares"):
            root = config.DOCUMENTS_DIR / folder
            candidates.extend(root.rglob(filename))
        if not candidates:
            full_juris_cache[filename] = ""
            return ""
        try:
            txt = candidates[0].read_text(encoding="utf-8", errors="ignore")
        except Exception:
            txt = ""
        full_juris_cache[filename] = txt
        return txt

    def _append_jur_item(doc: Document, *, relaxed_scope: bool = False) -> None:
        if doc.metadata.get("source_type") != "jurisprudencia_sii":
            return
        if not _doc_matches_juris_scope(
            doc,
            None if relaxed_scope else question_article_num,
            None if relaxed_scope else question_sub_num,
            question_law_filter,
            include_derogadas=include_derogadas,
        ):
            return
        text = doc.page_content
        filename = str(doc.metadata.get("filename", "") or "")
        full_text = _load_full_juris_text(filename)
        parse_text = full_text or text
        codigo = re.search(r"codigo_pronunciamiento:\s*(.+)", parse_text, re.IGNORECASE)
        fecha = re.search(r"fecha:\s*(.+)", parse_text, re.IGNORECASE)
        instancia = re.search(r"instancia:\s*(.+)", parse_text, re.IGNORECASE)
        tipo = re.search(r"tipo_pronunciamiento:\s*(.+)", parse_text, re.IGNORECASE)
        pdf_url = re.search(r"pdf_url:\s*(.+)", parse_text, re.IGNORECASE)
        resumen = re.search(r"## Resumen\s*(.+?)(?:\n##|\Z)", parse_text, re.IGNORECASE | re.DOTALL)

        c = (codigo.group(1).strip() if codigo else "N/A")
        f = (fecha.group(1).strip() if fecha else "N/A")
        i = (instancia.group(1).strip() if instancia else "N/A")
        t = (tipo.group(1).strip() if tipo else "N/A")
        p_raw = (pdf_url.group(1).strip() if pdf_url else "N/A")
        p = _sanitize_juris_pdf_url(p_raw)
        r = (resumen.group(1).strip().replace("\n", " ")[:220] if resumen else "")

        # Fallback mínimo basado en filename cuando no hay metadata explícita
        # en el chunk recuperado.
        if c == "N/A" and filename.startswith("sii_circular_"):
            m = re.match(r"sii_circular_(\d{4})_(\d+)\.md", filename)
            if m:
                c = f"CIRCULAR {int(m.group(2))}-{m.group(1)}"
        if t == "N/A" and filename.startswith("sii_circular_"):
            t = "Circular"
        key = f"{c}|{f}|{i}"
        if key in jur_seen:
            continue
        jur_seen.add(key)
        label = _build_pron_label(t, c)
        if (not label) or label.strip().upper() in {"N/A", "N/A N/A"}:
            continue
        if c.upper() == "N/A" and t.upper() == "N/A" and not r:
            return
        jur_items.append(
            {
                "label": label,
                "codigo": c,
                "tipo": t,
                "fecha": f,
                "instancia": i,
                "pdf_url": p,
                "resumen": r,
            }
        )

    for doc in context:
        _append_jur_item(doc, relaxed_scope=False)
        if len(jur_items) >= 40:
            break

    admin_types = ("OFICIO", "CIRCULAR", "RESOLU")

    def _is_admin_item(item: dict[str, str]) -> bool:
        return any(t in (item.get("tipo", "").upper()) for t in admin_types)

    def _is_judicial_item(item: dict[str, str]) -> bool:
        return "SENTENCIA" in (item.get("tipo", "").upper())

    # Fallback: si el filtro estricto no trajo ambas familias (administrativa/judicial),
    # relajar alcance por artículo dentro del mismo contexto recuperado para no perder
    # cobertura, manteniendo filtro de ley cuando exista.
    has_admin = any(_is_admin_item(it) for it in jur_items)
    has_judicial = any(_is_judicial_item(it) for it in jur_items)
    if (not has_admin) or (not has_judicial):
        for doc in context:
            if len(jur_items) >= 60:
                break
            if doc.metadata.get("source_type") != "jurisprudencia_sii":
                continue
            if (not has_admin) and _doc_is_admin_juris(doc):
                _append_jur_item(doc, relaxed_scope=True)
            if (not has_judicial) and (not _doc_is_admin_juris(doc)):
                _append_jur_item(doc, relaxed_scope=True)

    # Regla de orden para presentación:
    # OFICIO → CIRCULAR → RESOLUCION → SENTENCIA → otros (y luego fecha desc).
    jur_items.sort(
        key=lambda x: (
            _tipo_priority(x.get("tipo", "")),
            x.get("fecha", ""),
        ),
        reverse=False,
    )

    # Si la pregunta solicita doctrina administrativa, aplicar filtro estricto:
    # solo oficios/circulares/resoluciones; si no hay, no mostrar sentencias.
    # En modo normal, mantener ambas (administrativa + judicial), quedando
    # primero administrativa gracias al orden por prioridad de tipo.
    jur_admin = [
        it for it in jur_items
        if _is_admin_item(it)
    ]
    if asks_admin_only:
        jur_items = jur_admin

    # En consultas temáticas (sin artículo explícito), ordenar por idoneidad
    # semántica y luego priorizar administrativa por tipo.
    if not question_article_num:
        if re.search(r"\bacci[oó]n(?:es)?\b", question, re.IGNORECASE):
            focused = []
            for it in jur_items:
                hay = " ".join(
                    [
                        it.get("label", ""),
                        it.get("codigo", ""),
                        it.get("tipo", ""),
                        it.get("resumen", ""),
                    ]
                ).lower()
                if re.search(r"acci[oó]n|art[íi]culo\s*17|art[íi]culo\s*107|art[íi]culo\s*18", hay):
                    focused.append(it)
            if focused:
                jur_items = focused

        scored = [
            (it, _semantic_overlap_score(question, it))
            for it in jur_items
        ]
        positive = [it for it, sc in scored if sc > 0]
        if positive:
            jur_items = positive
        jur_items.sort(
            key=lambda x: (
                -_semantic_overlap_score(question, x),
                _tipo_priority(x.get("tipo", "")),
                x.get("fecha", ""),
            )
        )

    # Orden final obligatorio de presentación:
    # 1) Jurisprudencia administrativa (SII), 2) Jurisprudencia judicial (sentencias), 3) otros.
    jur_admin_ordered = [it for it in jur_items if _is_admin_item(it)]
    jur_judicial_ordered = [it for it in jur_items if _is_judicial_item(it)]
    jur_other_ordered = [
        it for it in jur_items
        if (not _is_admin_item(it)) and (not _is_judicial_item(it))
    ]
    jur_items = jur_admin_ordered + jur_judicial_ordered + jur_other_ordered

    jur_lines = [
        "- etiqueta: {label} | codigo: {codigo} | tipo: {tipo} | fecha: {fecha} | "
        "instancia: {instancia} | pdf_url: {pdf_url} | resumen: {resumen}".format(
            label=item["label"],
            codigo=item["codigo"],
            tipo=item["tipo"],
            fecha=item["fecha"],
            instancia=item["instancia"],
            pdf_url=item["pdf_url"],
            resumen=item["resumen"],
        )
        for item in jur_items[:top_juris]
    ]
    juris_context = "\n".join(jur_lines) if jur_lines else "(sin jurisprudencia en contexto)"
    has_juris_evidence = bool(jur_lines)
    # Si hay evidencia jurisprudencial recuperada, exigir su sección también en
    # consultas telegráficas (ej: "17 numero 8") aunque no digan "jurisprudencia".
    juris_required = has_juris_evidence and (
        asks_jurisprudencia or bool(question_article_num) or bool(jur_items)
    )
    juris_mode_instructions = (
        "MODO ESPECIAL (pregunta de jurisprudencia):\n"
        "- Prioriza la siguiente estructura y puedes omitir secciones no críticas:\n"
        "  ### 1. Explicación breve del artículo consultado\n"
        "  ### 2. Jurisprudencia completa del artículo\n"
        "  ### 3. Cierre breve\n"
        "- En la sección 2 debes incluir TODOS los pronunciamientos presentes en "
        "la evidencia de jurisprudencia (sin omitir).\n"
        "- Para cada pronunciamiento usa este formato OBLIGATORIO:\n"
        "  - Referencia jurisprudencial: <etiqueta>\n"
        "  - Tipo: <tipo>\n"
        "  - Código interno: <codigo>\n"
        "  - Fecha: <fecha>\n"
        "  - Instancia: <instancia>\n"
        "  - Link PDF: <link o 'No disponible'>\n"
        "  - Resumen del criterio: <resumen>\n"
        "- La 'Referencia jurisprudencial' debe usar SIEMPRE la etiqueta "
        "normalizada (OFICIO/CIRCULAR/RESOLUCION/SENTENCIA + numero-anio).\n"
        "- PROHIBIDO usar solo 'GR-xx-xxxxx-aaaa' como título principal.\n"
        "- Si no hay link PDF en evidencia, indica explícitamente: 'No disponible'.\n"
        "- Si la pregunta incluye artículo/subnumeral específico y no hay evidencia "
        "exacta en el contexto filtrado, indícalo explícitamente sin inventar "
        "jurisprudencia de otros artículos.\n"
        "- Si la pregunta pide específicamente oficios/circulares/resoluciones, "
        "NO incluyas sentencias judiciales; si no hay evidencia administrativa, "
        "decláralo explícitamente.\n"
    ) if asks_jurisprudencia else (
        "MODO NORMAL: sigue la estructura completa definida en formato de respuesta."
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                f"{agent_ctx}\n\n"
                "--- FIN DEL CONTEXTO DEL AGENTE ---\n\n"
                "Eres un analista tributario experto chileno con habilidad "
                "para explicar normas complejas de forma clara y fluida. "
                "Respondes basándote ÚNICAMENTE en los fragmentos de ley "
                "proporcionados, con profundidad profesional pero en un "
                "estilo narrativo y accesible.\n\n"

                "═══════════════════════════════════════\n"
                "ESTILO DE COMUNICACIÓN\n"
                "═══════════════════════════════════════\n\n"

                "Tu texto será leído en voz alta (text-to-speech), así que:\n"
                "- Escribe de forma NARRATIVA y FLUIDA, como un profesor "
                "experto explicando a un colega.\n"
                "- Usa frases de CONEXIÓN que muestren relaciones causales: "
                "'esto es importante porque...', 'lo que en la práctica "
                "significa que...', 'es decir,', 'en otras palabras,', "
                "'esto se conecta directamente con...'\n"
                "- NO seas telegráfico ni uses solo frases cortas aisladas. "
                "Desarrolla ideas con fluidez natural.\n"
                "- Combina precisión técnica con claridad expositiva.\n"
                "- Cuando introduzcas un concepto técnico, explícalo "
                "brevemente la primera vez.\n\n"

                "═══════════════════════════════════════\n"
                "REGLAS FUNDAMENTALES\n"
                "═══════════════════════════════════════\n\n"

                "1. VERIFICACIÓN DE FUENTES: Revisa SIEMPRE [Fuente: ...] de "
                "cada fragmento. DL-824=Renta/LIR, DL-825=IVA, DL-830=Código "
                "Tributario. NUNCA atribuyas un artículo a la ley equivocada.\n"
                "   PRIORIDAD: Los fragmentos marcados como [Fuente: ...] "
                "(ley oficial) tienen PRIORIDAD ABSOLUTA. Los fragmentos "
                "marcados como [📝 NOTA CURADA: ...] son análisis previos "
                "que enriquecen el contexto pero NO reemplazan el texto legal. "
                "Usa las notas curadas para enriquecer la explicación, "
                "ampliar ejemplos y conectar conceptos, pero SIEMPRE cita "
                "la ley como autoridad definitiva.\n\n"

                "2. PROFUNDIDAD ANALÍTICA: No te limites a parafrasear. Cuando "
                "el texto legal mencione cifras (porcentajes, montos, plazos, "
                "tasas), SIEMPRE inclúyelas con precisión:\n"
                "   - Tasas impositivas: 'tasa del 35%', 'tasa del 15%'\n"
                "   - Umbrales: '20% del valor de mercado', '10% de participación'\n"
                "   - Plazos: '3 años', '6 años'\n"
                "   - Montos en UF/UTA/UTM → incluir equivalente CLP\n\n"

                "3. SENTIDO JURÍDICO: Explica el propósito práctico de cada "
                "norma. No solo QUÉ dice, sino PARA QUÉ existe y A QUIÉN afecta. "
                "Incluye excepciones, requisitos y condiciones cuando existan.\n\n"

                "═══════════════════════════════════════\n"
                "FORMATO DE RESPUESTA\n"
                "═══════════════════════════════════════\n\n"

                "### 1. Explicación General\n"
                "Párrafo narrativo sustancial (8-12 líneas) que explique "
                "de forma fluida y conectada:\n"
                "- Qué establece la norma y cuál es su propósito fundamental "
                "dentro del sistema tributario chileno\n"
                "- A quién aplica y por qué es relevante\n"
                "- Cómo encaja en el esquema general de la ley\n"
                "Usa un tono explicativo y natural, como un relato profesional. "
                "Ejemplo de estilo: 'El Artículo X es la norma fundamental que "
                "define... Su importancia radica en que permite... lo que en la "
                "práctica significa que...'\n"
                "Cita exacta: Art. X del DL-YYY (nombre de la ley).\n\n"

                "### 2. Análisis Detallado\n"
                "Desglosa el contenido siguiendo la estructura EXACTA del "
                "artículo (números, letras, incisos). Para cada punto:\n"
                "- Resume el contenido en lenguaje claro\n"
                "- Incluye CIFRAS ESPECÍFICAS del texto (%, montos, plazos)\n"
                "- Menciona excepciones o condiciones relevantes\n"
                "- Si aplica, explica el efecto práctico\n\n"

                "ESTRUCTURA DE ARTÍCULOS COMPLEJOS:\n"
                "- LETRAS MAYÚSCULAS A), B), C)...H) → secciones principales\n"
                "- Numerales 1°.-, 2°.- → dentro de cada letra o directamente\n"
                "- Letras minúsculas a), b), c) → sub-ítems\n"
                "- Viñetas romanas (i), (ii) → sub-sub-ítems\n\n"

                "IMPORTANTE:\n"
                "- Cubre TODOS los numerales/letras desde el primero al último\n"
                "- Reúne información de TODOS los chunks proporcionados\n"
                "- NO copies el texto completo; haz un resumen analítico\n"
                "- Si un punto tiene varias condiciones/requisitos, enuméralas\n\n"

                "### 3. Referencias Cruzadas\n"
                "Se te proporcionará una LISTA DE REFERENCIAS DETECTADAS "
                "que el sistema encontró en los fragmentos. DEBES incluir "
                "CADA UNA de ellas en tu tabla.\n\n"
                "Formato TABLA:\n"
                "| Norma Referenciada | Tipo | Relación con el artículo analizado |\n"
                "|:---|:---|:---|\n\n"
                "ESTILO DE LA COLUMNA 'Relación':\n"
                "Escribe cada celda como una EXPLICACIÓN NARRATIVA que conecte "
                "la referencia con el artículo analizado. No solo digas qué hace "
                "el artículo referenciado, sino CÓMO y POR QUÉ se conecta.\n\n"
                "Ejemplos de BUEN estilo (imitables):\n"
                "- 'Es la norma de cobro: el Art. 10 define el hecho gravado, "
                "pero remite a este artículo para la aplicación de la tasa del "
                "35% (Impuesto Adicional) sobre la utilidad de la venta indirecta.'\n"
                "- 'Complementa la definición de \"bienes situados en Chile\". "
                "Establece que las acciones de una S.A. chilena se entienden "
                "situadas en Chile, lo que amplía el alcance del Art. 10.'\n"
                "- 'Otorga al SII facultades para determinar el valor de mercado "
                "de las acciones extranjeras en estas operaciones, lo que es "
                "crucial cuando hay discrepancia en la valorización.'\n\n"
                "Ejemplos de MAL estilo (evitar):\n"
                "- 'Permite al SII tasar valores.' (demasiado escueto)\n"
                "- 'Establece el impuesto adicional.' (no explica la conexión)\n"
                "- 'Define regímenes fiscales preferenciales.' (no dice para qué)\n\n"
                "Tipos de referencia:\n"
                "- **Explícita**: Mencionada directamente en el texto analizado\n"
                "- **Complementaria**: No mencionada literalmente pero esencial "
                "para completar el análisis\n\n"
                "REGLAS OBLIGATORIAS:\n"
                "- INCLUYE TODAS las referencias de la lista proporcionada, "
                "sin excepción.\n"
                "- Si la referencia NO menciona otra ley, es del MISMO cuerpo "
                "legal que se analiza.\n"
                "- Explica siempre la RELACIÓN BIDIRECCIONAL: cómo el artículo "
                "referenciado afecta al artículo analizado y viceversa.\n"
                "- Incluye cifras cuando existan (tasas, plazos, porcentajes).\n\n"

                "### 4. Jurisprudencia Relacionada (SII)\n"
                "Si hay evidencia de jurisprudencia en el bloque "
                "'EVIDENCIA DE JURISPRUDENCIA EN CONTEXTO', incluye esta sección "
                "inmediatamente DESPUÉS de Referencias Cruzadas.\n"
                "Formato por item:\n"
                "- Referencia jurisprudencial: ... (usar 'etiqueta' cuando exista)\n"
                "- Tipo: ...\n"
                "- Código interno: ...\n"
                "- Fecha: ...\n"
                "- Instancia: ...\n"
                "- Link PDF: ... (si viene N/A, indicar 'No disponible')\n"
                "- Resumen del criterio: ...\n"
                "Reglas:\n"
                "- Usa SOLO pronunciamientos realmente presentes en la evidencia.\n"
                "- No inventes links ni códigos.\n"
                "- Resume criterio en 2-4 líneas por pronunciamiento.\n\n"

                "### 5. Conversión de Unidades\n"
                "Si la ley menciona UF, UTA o UTM, incluye el equivalente en CLP "
                "usando: UF≈$40.000, UTA≈$835.000, UTM≈$69.583.\n\n"

                "### 6. Ejemplo Práctico\n"
                "Cuenta un CASO REAL Y CONCRETO como si se lo explicaras "
                "a un cliente. El ejemplo debe fluir como una historia:\n\n"
                "> **Caso:** Describe una situación realista con nombres "
                "y cifras concretas (ej: 'Supongamos que una empresa "
                "japonesa posee el 60% de una sociedad chilena...')\n"
                "> **Aplicación:** Explica paso a paso cómo aplica la norma, "
                "conectando cada paso con el artículo ('Según el inciso "
                "tercero del Art. 10, como más del 20% del valor proviene "
                "de activos chilenos, se gatilla la tributación...')\n"
                "> **Resultado:** Indica la consecuencia tributaria con "
                "cifras concretas y el artículo que la establece.\n\n"
                "El ejemplo debe ser tan claro que alguien sin conocimientos "
                "tributarios entienda qué pasa y por qué.\n\n"

                "### 7. Conclusión\n"
                "Párrafo final narrativo (4-6 líneas) que sintetice:\n"
                "- La importancia práctica de la norma en lenguaje simple\n"
                "- Los puntos clave que todo contribuyente debe tener presente\n"
                "- Cómo esta norma se conecta con el resto del sistema "
                "tributario (mencionar las refs cruzadas más importantes)\n"
                "Escribe la conclusión como un cierre natural de tu "
                "explicación, no como un listado de puntos.\n\n"

                "═══════════════════════════════════════\n"
                "CALIDAD, FLUIDEZ Y ACCESIBILIDAD\n"
                "═══════════════════════════════════════\n\n"
                "Tu respuesta será leída en voz alta. Prioriza:\n"
                "- FLUIDEZ NARRATIVA: Escribe como un profesor experto que "
                "explica con naturalidad, no como un listado telegráfico. "
                "Cada sección debe fluir como un relato profesional.\n"
                "- CONEXIONES CLARAS: Usa transiciones como 'esto es importante "
                "porque...', 'lo que en la práctica significa que...', 'esto "
                "se conecta directamente con...', 'dicho de otro modo,...'\n"
                "- ACCESIBILIDAD: Tanto un contador como un emprendedor sin "
                "formación tributaria deben entender. Cuando uses un término "
                "técnico por primera vez, explícalo brevemente.\n"
                "- PRECISIÓN: Si el texto legal contiene datos específicos "
                "(tasas, plazos, montos, porcentajes), SIEMPRE inclúyelos.\n\n"
                "Un buen análisis tributario se distingue por su precisión "
                "en los detalles, su claridad en la explicación y su "
                "capacidad de conectar las normas entre sí de forma natural.",
            ),
            (
                "human",
                "Fragmentos de legislación tributaria chilena:\n"
                "{context}\n\n"
                "══════════════════════════════════════════\n"
                "CHECKLIST DE REFERENCIAS CRUZADAS\n"
                "(OBLIGATORIO: incluir CADA UNA en la tabla)\n"
                "══════════════════════════════════════════\n"
                "{detected_refs}\n\n"
                "INSTRUCCIÓN: Tu tabla de Referencias Cruzadas DEBE contener "
                "una fila por CADA referencia listada arriba. Verifica al "
                "final que no falte ninguna.\n\n"
                "EVIDENCIA DE JURISPRUDENCIA EN CONTEXTO:\n"
                "{juris_context}\n\n"
                "REQUISITO DE JURISPRUDENCIA:\n"
                "{juris_required_text}\n"
                "Si dice 'SI', es OBLIGATORIO incluir la sección "
                "'### 4. Jurisprudencia Relacionada (SII)' inmediatamente "
                "después de '### 3. Referencias Cruzadas', usando TODOS los "
                "pronunciamientos listados en la evidencia.\n\n"
                "INSTRUCCIÓN DE MODO:\n"
                "{juris_mode_instructions}\n\n"
                "Pregunta: {question}\n\n"
                "Respuesta (análisis profesional completo):",
            ),
        ]
    )

    # Usar max_tokens alto para artículos largos (ej: Art. 17 LIR = 31 numerales)
    chain = prompt | _get_llm(max_tokens=16384) | StrOutputParser()
    answer = chain.invoke({
        "context": context_text,
        "question": question,
        "detected_refs": detected_refs or "(ninguna detectada)",
        "juris_context": juris_context,
        "juris_required_text": (
            "SI (incluir sección de jurisprudencia)"
            if juris_required
            else "NO (no hay obligación de sección de jurisprudencia)"
        ),
        "juris_mode_instructions": juris_mode_instructions,
    })

    # Refuerzo determinístico: si hay evidencia de jurisprudencia y el modelo
    # no la refleja bien (caso observado con circulares), anexamos una sección
    # automática basada en metadata recuperada.
    if jur_items:
        top_items = jur_items[:top_juris]
        already_has_juris_heading = re.search(
            r"###\s*(?:2|4)\.\s*Jurisprudencia|###\s*Jurisprudencia Relacionada",
            answer,
            re.IGNORECASE,
        ) is not None
        already_has_non_na_ref = re.search(
            r"Referencia jurisprudencial[^\n]*:\s*(?!N/?A\b).+",
            answer,
            re.IGNORECASE,
        ) is not None
        has_any_label = any(item["label"] in answer for item in top_items)
        has_admin_item = any(
            any(t in (it.get("tipo", "").upper()) for t in ("OFICIO", "CIRCULAR", "RESOLU"))
            for it in top_items
        )
        mentions_admin = any(k in answer.upper() for k in ("OFICIO", "CIRCULAR", "RESOLUCION"))

        needs_autofix = ((not has_any_label) or (has_admin_item and not mentions_admin))
        if already_has_juris_heading and already_has_non_na_ref:
            needs_autofix = False

        if needs_autofix:
            auto_lines = [
                "",
                "### Jurisprudencia Relacionada (SII) — Verificación automática",
            ]
            missing_items = [it for it in top_items if it["label"] not in answer]
            for item in missing_items:
                pdf = item["pdf_url"] if item["pdf_url"] and item["pdf_url"] != "N/A" else "No disponible"
                resumen = item["resumen"] or "No disponible."
                auto_lines.extend(
                    [
                        f"- **Referencia jurisprudencial**: {item['label']}",
                        f"  - **Tipo**: {item['tipo']}",
                        f"  - **Código interno**: {item['codigo']}",
                        f"  - **Fecha**: {item['fecha']}",
                        f"  - **Instancia**: {item['instancia']}",
                        f"  - **Link PDF**: {pdf}",
                        f"  - **Resumen del criterio**: {resumen}",
                    ]
                )
            answer = answer.rstrip() + "\n\n" + "\n".join(auto_lines)

    return {"answer": answer}


# ============================================================
# Decisión condicional
# ============================================================


def should_retrieve_references(state: RAGState) -> str:
    """¿Hay referencias cruzadas pendientes?"""
    if state.get("cross_ref_queries"):
        return "retrieve_references"
    return "generate"


# ============================================================
# Grafo principal
# ============================================================


class RAGGraph:
    """Grafo RAG agéntico con multi-query y referencias cruzadas."""

    def __init__(self):
        self.graph = self._build_graph()
        console.print("[green]✅ Grafo RAG inicializado[/green]")

    def _build_graph(self):
        """
        START → expand_query → retrieve → grade → resolve_refs
                                                     │
                                          ¿refs? ── SÍ → retrieve_refs ─┐
                                                     │                   │
                                                     NO ─────────────────┘
                                                     │
                                                     ▼
                                                  generate → END
        """
        workflow = StateGraph(RAGState)

        workflow.add_node("expand_query", expand_query_node)
        workflow.add_node("retrieve", retrieve_node)
        workflow.add_node("grade_documents", grade_documents_node)
        workflow.add_node("resolve_cross_references", resolve_cross_references_node)
        workflow.add_node("retrieve_references", retrieve_references_node)
        workflow.add_node("generate", generate_node)

        workflow.add_edge(START, "expand_query")
        workflow.add_edge("expand_query", "retrieve")
        workflow.add_edge("retrieve", "grade_documents")
        workflow.add_edge("grade_documents", "resolve_cross_references")

        workflow.add_conditional_edges(
            "resolve_cross_references",
            should_retrieve_references,
            {
                "retrieve_references": "retrieve_references",
                "generate": "generate",
            },
        )

        workflow.add_edge("retrieve_references", "generate")
        workflow.add_edge("generate", END)

        return workflow.compile()

    def query(
        self,
        question: str,
        include_derogadas: bool = False,
        top_juris: int = 8,
    ) -> dict:
        """Ejecuta una consulta a través del grafo RAG."""
        console.print(f"\n[bold cyan]❓ Pregunta: {question}[/bold cyan]\n")

        global _vector_store, _agent_context
        _vector_store = None
        _agent_context = None  # Recargar agente.md por si fue editado

        initial_state: RAGState = {
            "question": question,
            "expanded_queries": [],
            "context": [],
            "cross_ref_queries": [],
            "detected_refs_summary": "",
            "answer": "",
            "sources": [],
            "include_derogadas": include_derogadas,
            "top_juris": top_juris,
        }

        result = self.graph.invoke(initial_state)

        console.print(f"\n[bold green]💡 Respuesta:[/bold green]")
        console.print(f"[white]{result['answer']}[/white]\n")

        return {
            "answer": result["answer"],
            "sources": result.get("sources", []),
        }
