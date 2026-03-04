"""
Chunking Estructural Inteligente para documentos legales / tributarios.
========================================================================

Estrategia:
1.  Detectar la estructura del documento con regex:
    LIBRO → TÍTULO → CAPÍTULO → PÁRRAFO → SECCIÓN → ARTÍCULO → inciso → letra
    (+ Markdown headings, numbered sections, etc.)

2.  Construir un árbol jerárquico del documento.

3.  Cada chunk = una sección lógica completa.
    -   Si la sección es más larga que MAX_CHUNK_SIZE se subdivide
        por *párrafos completos* (nunca a mitad de oración).

4.  Overlap inteligente: en vez de copiar 200 caracteres al azar,
    cada chunk lleva prepended su *ruta jerárquica*:
        «LIBRO I > TÍTULO II > Artículo 21 > Inciso 3»
    Eso le da al embedding y al LLM todo el contexto necesario sin
    duplicar contenido.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from langchain_core.documents import Document
from rich.console import Console

import config

console = Console()

# ============================================================
# Patrones de estructura legal (orden = prioridad de detección)
# ============================================================

_STRUCTURE_PATTERNS: list[tuple[re.Pattern, str, int]] = [
    # ── Legislación hispanoamericana ──
    # Soporta romanos (I, II, III), arábigos (1, 2) y ordinales
    # castellanos (Primero, Segundo, Tercero) → DL-830 usa "LIBRO PRIMERO"
    (re.compile(
        r"^LIBRO\s+(?:[IVXLCDM\d]+|PRIMER[OA]|SEGUND[OA]|TERCER[OA]"
        r"|CUART[OA]|QUINT[OA]|SEXT[OA])",
        re.IGNORECASE,
    ), "Libro", 1),
    (re.compile(
        r"^T[ÍI]TULO\s+[IVXLCDM\d]+",
        re.IGNORECASE,
    ), "Título", 2),
    (re.compile(
        r"^CAP[ÍI]TULO\s+[IVXLCDM\d]+",
        re.IGNORECASE,
    ), "Capítulo", 3),
    (re.compile(
        r"^P[ÁA]RRAFO\s+\d+",
        re.IGNORECASE,
    ), "Párrafo", 4),
    (re.compile(
        r"^SECCI[ÓO]N\s+\d+",
        re.IGNORECASE,
    ), "Sección", 5),

    # ── Artículos: SOLO encabezados reales, NO referencias en texto ──
    # Requiere "°.-" o ".-" o "°-" después del número → distingue
    #   "ARTICULO 31°.- La renta líquida..."  (HEADING ✓)
    #   "artículo 21, la destrucción..."      (REFERENCIA ✗)
    (re.compile(
        r"^ART[ÍI]CULO\s+\d+[°º]?\s*(?:BIS|TER|QU[ÁA]TER)?\s*[\.\-]",
        re.IGNORECASE,
    ), "Artículo", 6),
    (re.compile(
        r"^Art\.?\s*\d+[°º]?\s*(?:BIS|TER|QU[ÁA]TER)?\s*[\.\-]",
    ), "Artículo", 6),

    # ── Markdown headings ──
    (re.compile(r"^#{1}\s+.+"), "H1", 2),
    (re.compile(r"^#{2}\s+.+"), "H2", 3),
    (re.compile(r"^#{3}\s+.+"), "H3", 5),
    (re.compile(r"^#{4,}\s+.+"), "H4", 6),

    # ── Letras mayúsculas como divisiones principales ──
    # En legislación chilena, artículos complejos (ej: Art. 14 LIR) usan
    # letras mayúsculas A), B), C)...H) como secciones principales.
    # Estas están ARRIBA de los numerales en la jerarquía.
    # Ej: "A) Rentas provenientes de empresas obligadas a declarar"
    #     "D) Régimen para las micro, pequeñas y medianas empresas (Pymes)."
    (re.compile(r"^[A-Z]\)\s+\S"), "LetraMayor", 7),

    # ── Numerales dentro de artículos: "1°.- La indemnización...", "10°.- Los beneficios..." ──
    # Formato principal: N°.- o Nº.- al inicio de línea.
    (re.compile(r"^\d+[°º]\s*[\.\-]"), "Numeral", 8),
    # Formato alternativo 1: N° seguido de espacio y mayúscula (sin .- )
    # Ej: "30° La parte de los gananciales..." (inconsistencia en el PDF)
    (re.compile(r"^\d{1,2}[°º]\s+[A-ZÁÉÍÓÚÑ]"), "Numeral", 8),
    # Formato alternativo 2: N.- sin ° (inconsistencia en el PDF)
    # Ej: "31.- Las compensaciones económicas..."
    (re.compile(r"^\d{1,2}\s*\.\-\s+[A-ZÁÉÍÓÚÑ]"), "Numeral", 8),

    # ── Numeración genérica ──
    # Excluir números tipo "8.000", "16.271" que son cantidades, no subsecciones
    (re.compile(r"^\d{1,2}\.\d{1,2}\.?\s+\S"), "Subsección", 8),
    (re.compile(r"^\d+\)\s+\S"), "Inciso", 9),
    (re.compile(r"^[a-z]\)\s+\S"), "Letra", 10),

    # ── Líneas en MAYÚSCULAS que parecen títulos ──
    # Máx. ~80 chars para evitar falsos positivos con texto en mayúsculas
    (re.compile(
        r"^[A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ\s\-:,\.]{10,80}$",
    ), "Encabezado", 3),
]


def _is_likely_reference(line: str) -> bool:
    """
    Detecta si una línea que comienza con 'artículo' es una referencia
    dentro del texto (no un encabezado de artículo).

    Patrones de REFERENCIA (NO heading):
        "artículo 21, la destrucción voluntaria..."
        "artículo 31 de esta ley, podrá..."
        "artículo 107. Tratándose de acciones..."
        "artículo 29°. LEY 19347"  ← anotación marginal del PDF

    Patrones de HEADING (SÍ heading):
        "ARTICULO 31°.- La renta líquida..."
        "Artículo 8°- El impuesto de este..."
    """
    # ── Anotaciones marginales del PDF ──
    # Cuando PyMuPDF extrae PDFs de leyes chilenas, las anotaciones
    # de la columna derecha (LEY, DL, D.O., Art., NOTA) a veces se
    # concatenan con texto del margen.
    # Ej: "artículo 29°. LEY 19347" → NO es un heading de artículo,
    #     es una anotación marginal que indica qué ley modificó esa parte.
    if re.match(
        r"^art[íi]culo\s+\d+[°º]?\s*\.?\s*"
        r"(LEY|DL|D\.O\.|NOTA|Art\.\s*\d|N[°º]\s*\d)",
        line,
        re.IGNORECASE,
    ):
        return True  # ES referencia/anotación, NO heading

    # ── REGLA CLAVE: Distinción heading vs referencia ──
    # Headings reales en leyes chilenas usan MAYÚSCULA INICIAL + separador:
    #   DL-824 (Renta):      "ARTICULO 31°.- La renta..."        (ALL-CAPS)
    #   DL-830 (Cód. Trib.): "Artículo 1°.- Apruébase..."       (Title Case)
    #   DL-825 (IVA):        "Artículo 4°- Estarán gravadas..."  (Title Case, °-)
    #   DL-830:              "Artículo 4° bis.- Las obligaciones" (con bis/ter/etc.)
    #
    # Referencias internas (NO headings):
    #   "artículo 21, la destrucción..."   (minúscula, sin separador .-)
    #   "artículo 4° bis"                  (minúscula)
    #   "artículo 1°, y de ellas"          (minúscula, coma después)
    #   "artículo 41°.- para determinar"   (minúscula, continuación de texto)
    #
    # Criterio:
    #   Primera letra MAYÚSCULA + N°(mod)?[.-] → HEADING
    #   Primera letra minúscula → REFERENCIA
    if line[0].isupper() and re.match(
        r"^Art[íiÍI]culo\s+\d+[°º]?\s*"
        r"(?:bis|ter|qu[áa]ter|quinquies|sexies)?\s*[\.\-]",
        line,
        re.IGNORECASE,
    ):
        return False  # HEADING real (ALL-CAPS o Title Case)

    # Todo lo demás (minúscula o sin separador) es referencia
    return True


def _is_false_structural_heading(line: str, level_name: str) -> bool:
    """
    Detecta si una línea que coincidió con un patrón de Título/Capítulo/
    Párrafo/Libro/Sección es en realidad texto envuelto, no un heading real.

    Falso positivo típico:
        "Título V de esta ley. Con todo, se considerará renta toda"
        (es continuación de texto que casualmente empieza con "Título V")

    Heading real:
        "TITULO V"
        "TITULO V De las disposiciones transitorias"
    """
    if level_name not in ("Libro", "Título", "Capítulo", "Párrafo", "Sección"):
        return False

    # 0) Si empieza con minúscula, NO es heading estructural
    #    Headings reales: "TITULO I", "LIBRO PRIMERO", "Libro Segundo"
    #    Falsos positivos: "libro de inventarios y balances.", "título de bienes raíces."
    if line[0].islower():
        return True

    # 1) Headings reales suelen ser cortos (< 80 chars)
    #    Si es largo, probablemente es texto envuelto
    if len(line) > 80:
        return True

    # 2) Si contiene frases típicas de referencia, no es heading
    _REFERENCE_PHRASES = [
        "de esta ley",
        "de la presente",
        "del presente",
        "del mismo",
        "de la misma",
        "de dicha ley",
        "de la ley",
        "de ese",
        "de esa",
        "con todo,",
        "sin embargo,",
        "no obstante,",
        "en su caso,",
        "de la ordenanza",
    ]
    line_lower = line.lower()
    for phrase in _REFERENCE_PHRASES:
        if phrase in line_lower:
            return True

    # 3) Si está en minúsculas o case mixto y es > 40 chars, sospechoso
    #    Headings reales en leyes chilenas son ALL-CAPS
    upper_ratio = sum(1 for c in line if c.isupper()) / max(len(line), 1)
    if upper_ratio < 0.5 and len(line) > 40:
        return True

    return False


@dataclass
class _Section:
    """Nodo de la estructura jerárquica del documento."""

    title: str
    level: int
    level_name: str
    content_lines: list[str] = field(default_factory=list)
    hierarchy: list[str] = field(default_factory=list)


# ============================================================
# Procesador principal
# ============================================================


class TextProcessor:
    """
    Chunking por estructura + overlap inteligente (header carryover).
    """

    def __init__(
        self,
        max_chunk_size: int | None = None,
        min_chunk_size: int | None = None,
    ):
        self.max_chunk_size = max_chunk_size or config.MAX_CHUNK_SIZE
        self.min_chunk_size = min_chunk_size or config.MIN_CHUNK_SIZE

    # ========================================
    # API pública
    # ========================================

    def process_documents(self, documents: list[Document]) -> list[Document]:
        """
        Recibe documentos completos (1 por archivo) y retorna chunks
        inteligentes por estructura.
        """
        if not documents:
            console.print(
                "[yellow]⚠️ No hay documentos para procesar.[/yellow]"
            )
            return []

        all_chunks: list[Document] = []

        for doc in documents:
            cleaned = self._clean_text(doc.page_content)
            if not cleaned.strip():
                continue

            sections = self._parse_structure(cleaned)
            console.print(
                f"  [dim]📐 {doc.metadata.get('filename', '?')}: "
                f"{len(sections)} secciones detectadas[/dim]"
            )

            chunks = self._sections_to_chunks(sections, doc.metadata)
            all_chunks.extend(chunks)

        for i, chunk in enumerate(all_chunks):
            chunk.metadata["chunk_id"] = i

        console.print(
            f"[green]✅ {len(documents)} documento(s) → "
            f"{len(all_chunks)} chunks estructurales[/green]"
        )
        return all_chunks

    # ========================================
    # Parsing de estructura
    # ========================================

    def _parse_structure(self, text: str) -> list[_Section]:
        """
        Recorre el texto línea por línea, detecta encabezados y
        construye una lista plana de secciones con su jerarquía.
        """
        lines = text.split("\n")
        sections: list[_Section] = []
        hierarchy_stack: list[tuple[int, str]] = []
        current: _Section | None = None

        for raw_line in lines:
            line = raw_line.strip()
            detected = self._detect_heading(line)

            if detected is not None:
                level_name, level, heading_text = detected

                if current is not None:
                    sections.append(current)

                while hierarchy_stack and hierarchy_stack[-1][0] >= level:
                    hierarchy_stack.pop()

                hierarchy_stack.append((level, heading_text))
                hier_path = [h[1] for h in hierarchy_stack]

                current = _Section(
                    title=heading_text,
                    level=level,
                    level_name=level_name,
                    content_lines=[line],
                    hierarchy=list(hier_path),
                )
            else:
                if current is None:
                    current = _Section(
                        title="(Preámbulo)",
                        level=0,
                        level_name="Preámbulo",
                        content_lines=[],
                        hierarchy=["(Preámbulo)"],
                    )
                current.content_lines.append(raw_line)

        if current is not None:
            sections.append(current)

        return sections

    def _detect_heading(
        self, line: str
    ) -> tuple[str, int, str] | None:
        """
        Comprueba si `line` coincide con algún patrón de estructura.
        Retorna (level_name, level, heading_text) o None.

        Incluye validación extra para evitar falsos positivos:
        - Líneas tipo "artículo 21, la destrucción..." son REFERENCIAS.
        - Líneas tipo "Título V de esta ley. Con todo..." son texto envuelto.
        - Números tipo "8.000 unidades" no son subsecciones.
        """
        if not line or len(line) > 200:
            return None

        # Filtrar líneas que parecen artículos pero son referencias
        if re.match(r"^art[íi]culo\s+\d+", line, re.IGNORECASE):
            if _is_likely_reference(line):
                return None

        for pattern, level_name, level in _STRUCTURE_PATTERNS:
            if pattern.match(line):
                # Validar que headings estructurales no sean texto envuelto
                if _is_false_structural_heading(line, level_name):
                    continue

                # Validar que "LetraMayor" no sea un falso positivo
                # Falsos positivos típicos por salto de línea del PDF:
                #   "...acogerse al artículo 14 letra\n"
                #   "B) número 1, se deberá..."  ← NO es heading
                if level_name == "LetraMayor":
                    ll = line.lower()
                    # Si después de X) viene un número/referencia, no es heading
                    after_paren = re.match(r"^[A-Z]\)\s+(.*)", line)
                    if after_paren:
                        rest = after_paren.group(1).lower()
                        # Empieza con patrón de referencia
                        if re.match(
                            r"(número|numeral|n[°º]|inciso|del|de\s)",
                            rest,
                        ):
                            continue
                    # Si la línea tiene frases de referencia, no es heading
                    if any(p in ll for p in [
                        "de esta ley", "de la presente", "del presente",
                        "de dicha ley", "de este artículo",
                    ]):
                        continue

                # Validar que Numeral/Letra no sea una referencia de texto
                # Falso positivo: "2°.- del artículo 20" (es continuación)
                # Falso positivo: "e) del artículo referido"
                if level_name in ("Numeral", "Letra"):
                    # Extraer texto después del marcador
                    rest_match = re.match(
                        r"^(?:\d+[°º]?\s*[\.\-]+\s*|[a-z]\)\s+)(.*)",
                        line,
                    )
                    if rest_match:
                        rest = rest_match.group(1).lower()
                        if rest.startswith(("del artículo", "del art.",
                                            "de este", "de la")):
                            continue

                # Validar que "Subsección" no sea un número grande (8.000, 16.271)
                if level_name == "Subsección":
                    m = re.match(r"^(\d+)\.(\d+)", line)
                    if m:
                        major = int(m.group(1))
                        minor_str = m.group(2)
                        # Si el "minor" tiene 3+ dígitos, es un número (8.000)
                        if len(minor_str) >= 3:
                            continue
                        # Si major > 50, probablemente es una cantidad
                        if major > 50:
                            continue

                return level_name, level, line

        return None

    # ========================================
    # Secciones → Chunks
    # ========================================

    def _sections_to_chunks(
        self,
        sections: list[_Section],
        base_metadata: dict,
    ) -> list[Document]:
        """
        Convierte secciones en Documents de LangChain.
        """
        chunks: list[Document] = []
        merged = self._merge_small_sections(sections)

        for section in merged:
            body = "\n".join(section.content_lines).strip()
            if not body:
                continue

            hier_prefix = " > ".join(section.hierarchy)

            if len(body) <= self.max_chunk_size:
                chunks.append(
                    self._make_chunk(hier_prefix, body, section, base_metadata)
                )
            else:
                sub_chunks = self._split_long_section(
                    body, hier_prefix, section, base_metadata
                )
                chunks.extend(sub_chunks)

        return chunks

    # Niveles que NUNCA deben ser absorbidos por merge.
    # Los Artículos son la unidad fundamental de una ley y siempre
    # deben ser chunks independientes.
    _NEVER_MERGE_LEVELS = {
        "Artículo", "Libro", "Título", "Capítulo", "Párrafo", "Sección",
        "LetraMayor", "Numeral",
    }

    def _merge_small_sections(
        self, sections: list[_Section]
    ) -> list[_Section]:
        """
        Fusiona secciones consecutivas demasiado cortas.

        REGLA CLAVE: Nunca fusiona Artículos, Títulos, Capítulos, etc.
        en su sección padre. Estos son unidades legales fundamentales
        y deben mantener su identidad como chunks independientes.
        """
        if not sections:
            return []

        merged: list[_Section] = []
        buffer: _Section | None = None

        for section in sections:
            if buffer is None:
                buffer = section
                continue

            buffer_len = sum(len(l) for l in buffer.content_lines)

            # NUNCA fusionar si la nueva sección es una unidad legal
            # principal (Artículo, Título, Capítulo, etc.)
            if section.level_name in self._NEVER_MERGE_LEVELS:
                merged.append(buffer)
                buffer = section
                continue

            if (
                buffer_len < self.min_chunk_size
                and section.level >= buffer.level
            ):
                buffer.content_lines.append("")
                buffer.content_lines.extend(section.content_lines)
            else:
                merged.append(buffer)
                buffer = section

        if buffer is not None:
            merged.append(buffer)

        return merged

    def _split_long_section(
        self,
        body: str,
        hier_prefix: str,
        section: _Section,
        base_metadata: dict,
    ) -> list[Document]:
        """
        Divide una sección larga en sub-chunks por párrafos completos.
        """
        paragraphs = re.split(r"\n{2,}", body)

        sub_chunks: list[Document] = []
        current_parts: list[str] = []
        current_len = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            para_len = len(para)

            if current_len + para_len > self.max_chunk_size and current_parts:
                chunk_body = "\n\n".join(current_parts)
                sub_chunks.append(
                    self._make_chunk(
                        hier_prefix, chunk_body, section, base_metadata,
                        part=len(sub_chunks) + 1,
                    )
                )
                current_parts = []
                current_len = 0

            current_parts.append(para)
            current_len += para_len + 2

        if current_parts:
            chunk_body = "\n\n".join(current_parts)
            sub_chunks.append(
                self._make_chunk(
                    hier_prefix, chunk_body, section, base_metadata,
                    part=len(sub_chunks) + 1 if len(sub_chunks) > 0 else None,
                )
            )

        final: list[Document] = []
        for ch in sub_chunks:
            if len(ch.page_content) > self.max_chunk_size * 1.5:
                final.extend(
                    self._split_by_sentences(
                        ch.page_content, hier_prefix, section, base_metadata
                    )
                )
            else:
                final.append(ch)

        return final

    def _split_by_sentences(
        self,
        text: str,
        hier_prefix: str,
        section: _Section,
        base_metadata: dict,
    ) -> list[Document]:
        """Último recurso: partir por oraciones."""
        sentences = re.split(r"(?<=\.)\s+", text)

        chunks: list[Document] = []
        current_parts: list[str] = []
        current_len = 0

        for sent in sentences:
            if current_len + len(sent) > self.max_chunk_size and current_parts:
                chunk_body = " ".join(current_parts)
                chunks.append(
                    self._make_chunk(
                        hier_prefix, chunk_body, section, base_metadata,
                        part=len(chunks) + 1,
                    )
                )
                current_parts = []
                current_len = 0
            current_parts.append(sent)
            current_len += len(sent) + 1

        if current_parts:
            chunk_body = " ".join(current_parts)
            chunks.append(
                self._make_chunk(
                    hier_prefix, chunk_body, section, base_metadata,
                    part=len(chunks) + 1 if len(chunks) > 0 else None,
                )
            )

        return chunks

    # ========================================
    # Utilidades
    # ========================================

    def _make_chunk(
        self,
        hier_prefix: str,
        body: str,
        section: _Section,
        base_metadata: dict,
        part: int | None = None,
    ) -> Document:
        """
        Construye un Document con header carryover.
        El page_content incluye la ruta jerárquica como contexto.
        """
        content = f"[{hier_prefix}]\n{body}"

        metadata = {
            **base_metadata,
            "section_title": section.title,
            "section_level": section.level,
            "section_level_name": section.level_name,
            "hierarchy": " > ".join(section.hierarchy),
            "hierarchy_path": hier_prefix,
            "chunk_size": len(content),
        }
        if part is not None:
            metadata["part"] = part

        return Document(page_content=content, metadata=metadata)

    def _clean_text(self, text: str) -> str:
        """Limpia texto conservando la estructura de líneas."""
        # 1) Caracteres de control
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

        # 2) ── Limpiar anotaciones marginales del PDF ──
        #    IMPORTANTE: hacer ANTES de normalizar espacios, ya que
        #    usamos los gaps de 5+ espacios para detectarlas.
        #
        #    Tipo A: Anotaciones TRAILING (al final de líneas del texto)
        #    DL-830:  "...reconocer la buena fe de los            Art. 10 N° 1"
        #    DL-830:  "Libro Segundo.                             D.O. 18.02.1975"
        #    DL-825:  "Artículo 4°- Estarán gravadas              D.O. 24.02.2020"
        #    Se eliminan porque son metadatos de modificación, no contenido.
        text = re.sub(
            r"[ \t]{5,}(?:"
            r"Art\.\s*\d+.*"
            r"|D\.O\.\s+[\d\.]+.*"
            r"|LEY\s+\d+.*"
            r"|DL\s+\d+.*"
            r"|NOTA\s+\d+.*"
            r"|N[°º]\s*\d+.*"
            r")$",
            "",
            text,
            flags=re.MULTILINE,
        )

        #    Tipo B: Anotaciones que PRECEDEN un numeral o LetraMayor
        #    DL-824:  "LEY 20239     31.- Las compensaciones económicas"
        #    → separar en dos líneas
        text = re.sub(
            r"((?:LEY|DL|D\.O\.)\s+[\d\w\.\-,/]+)[ \t]{2,}"
            r"(\d{1,2}[°º]?\s*[\.\-])",
            r"\1\n\2",
            text,
        )
        text = re.sub(
            r"((?:LEY|DL|D\.O\.)\s+[\d\w\.\-,/]+)[ \t]{2,}"
            r"([A-Z]\)\s+\S)",
            r"\1\n\2",
            text,
        )

        # 3) Normalizar espacios horizontales y verticales
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        lines = [line.strip() for line in text.split("\n")]
        text = "\n".join(lines)

        return text.strip()
