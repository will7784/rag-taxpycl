"""
Cliente para el Administrador de Contenido de Jurisprudencia (ACJ) del SII.

Usa endpoints JSON observados en el frontend oficial:
    /acjui/services/data/internetService/...

No requiere dependencias externas (urllib + ssl de la stdlib).
"""

from __future__ import annotations

import json
import ssl
import uuid
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class _HTMLTextExtractor(HTMLParser):
    """Extrae texto simple desde HTML, ignorando tags."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self._parts.append(data.strip())

    def get_text(self) -> str:
        return " ".join(self._parts).strip()


def html_to_text(value: str | None) -> str:
    """Convierte HTML a texto plano legible."""
    if not value:
        return ""
    parser = _HTMLTextExtractor()
    parser.feed(value)
    return parser.get_text()


class SIIACJClient:
    """Cliente HTTP para consumir endpoints publicos del ACJ del SII."""

    def __init__(
        self,
        base_url: str = "https://www4.sii.cl/acjui",
        timeout: int = 25,
        verify_ssl: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self._user_agent = "rag-documentos/1.0 (+sync-sii)"

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST JSON y retorna body parseado."""
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "User-Agent": self._user_agent,
        }
        req = Request(url, data=data, headers=headers, method="POST")

        context = None
        if not self.verify_ssl:
            context = ssl._create_unverified_context()

        try:
            with urlopen(req, timeout=self.timeout, context=context) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
        except ssl.SSLError:
            # Fallback defensivo: en algunos Windows falla validacion OCSP.
            with urlopen(
                req,
                timeout=self.timeout,
                context=ssl._create_unverified_context(),
            ) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
        except HTTPError as e:
            msg = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code} en {path}: {msg[:300]}") from e
        except URLError as e:
            raise RuntimeError(f"Error de red en {path}: {e}") from e

    @staticmethod
    def _metadata_payload(namespace_path: str) -> dict[str, Any]:
        return {
            "namespace": (
                "cl.sii.sdi.lob.juridica.acj.data.impl."
                f"{namespace_path}"
            ),
            "conversationId": "1",
            "transactionId": str(uuid.uuid4()),
            "page": None,
        }

    @staticmethod
    def _wrapped_payload(namespace_path: str, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "metaData": SIIACJClient._metadata_payload(namespace_path),
            "data": data,
        }

    def list_cuerpos_normativos(self) -> list[dict[str, Any]]:
        payload = self._metadata_payload(
            "InternetApplicationService/listCuerposNormativos"
        )
        resp = self._post_json(
            "/services/data/internetService/cuerpos-normativos",
            payload,
        )
        return resp.get("data", []) or []

    def list_tipos_instancia(self) -> list[dict[str, Any]]:
        payload = self._metadata_payload(
            "InternetApplicationService/listTiposInstancia"
        )
        resp = self._post_json(
            "/services/data/internetService/tipos-instancia",
            payload,
        )
        return resp.get("data", []) or []

    def get_tipo_instancia(self, tipo_instancia_id: int) -> dict[str, Any]:
        payload = self._wrapped_payload(
            "InternetApplicationService/getTipoInstancia",
            {"id": tipo_instancia_id},
        )
        resp = self._post_json(
            "/services/data/internetService/tipos-instancia/get",
            payload,
        )
        return resp.get("data", {}) or {}

    def find_grupos_instancia(self, tipo_instancia_id: int) -> list[dict[str, Any]]:
        payload = self._wrapped_payload(
            "InternetApplicationService/findGruposDeInstancia",
            {"id": tipo_instancia_id},
        )
        resp = self._post_json(
            "/services/data/internetService/find-grupos-instancia",
            payload,
        )
        return resp.get("data", []) or []

    def find_tipos_pronunciamiento(
        self,
        tipo_instancia_id: int,
    ) -> list[dict[str, Any]]:
        payload = self._wrapped_payload(
            "InternetApplicationService/findTiposDePronunciamiento",
            {"id": tipo_instancia_id},
        )
        resp = self._post_json(
            "/services/data/internetService/find-tipos-pronunciamiento",
            payload,
        )
        return resp.get("data", []) or []

    def find_instancias(self, grupo_instancia_id: int) -> list[dict[str, Any]]:
        payload = self._wrapped_payload(
            "InternetApplicationService/findInstancias",
            {"id": grupo_instancia_id},
        )
        resp = self._post_json(
            "/services/data/internetService/find-instancias",
            payload,
        )
        return resp.get("data", []) or []

    def find_articulos(self, cuerpo_normativo_id: int) -> list[dict[str, Any]]:
        payload = self._wrapped_payload(
            "InternetApplicationService/findArticulos",
            {"id": cuerpo_normativo_id},
        )
        resp = self._post_json(
            "/services/data/internetService/find-articulos",
            payload,
        )
        return resp.get("data", []) or []

    def find_pronunciamientos(
        self,
        articulo_id: int,
        tipo_instancia_id: int | None = 1,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        payload = self._wrapped_payload(
            "InternetApplicationService/findPronunciamientos",
            {
                "text": None,
                "tipoInstanciaId": tipo_instancia_id,
                "grupoInstanciaId": None,
                "tipoCodigoId": None,
                "codigo": None,
                "ruc": None,
                "instanciaId": None,
                "tipoPronunciamientoId": None,
                "cuerpoNormativoId": None,
                "articulosIds": [articulo_id],
                "reemplazos": [],
                "fechaDesde": None,
                "fechaHasta": None,
            },
        )
        resp = self._post_json(
            "/services/data/internetService/find-pronunciamientos",
            payload,
        )
        items = (resp.get("data") or {}).values()
        out = list(items)
        if max_items is not None:
            out = out[:max_items]
        return out

    def find_pronunciamientos_search_form(
        self,
        search_form: dict[str, Any],
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        payload = self._wrapped_payload(
            "InternetApplicationService/findPronunciamientos",
            search_form,
        )
        resp = self._post_json(
            "/services/data/internetService/find-pronunciamientos",
            payload,
        )
        items = (resp.get("data") or {}).values()
        out = list(items)
        if max_items is not None:
            out = out[:max_items]
        return out

    def get_full_pronunciamiento(self, pronunciamiento_id: int) -> dict[str, Any]:
        payload = self._wrapped_payload(
            "InternetApplicationService/getFullPronunciamiento",
            {"id": pronunciamiento_id},
        )
        resp = self._post_json(
            "/services/data/internetService/pronunciamientos/get-full",
            payload,
        )
        return resp.get("data", {}) or {}

    def download_pdf(self, pdf_url: str, output_file: Path) -> bool:
        """Descarga PDF si la URL existe y termina en .pdf."""
        if not pdf_url:
            return False
        parsed = urlparse(pdf_url)
        if parsed.scheme not in ("http", "https"):
            return False

        req = Request(
            pdf_url,
            headers={"User-Agent": self._user_agent},
            method="GET",
        )
        context = None if self.verify_ssl else ssl._create_unverified_context()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urlopen(req, timeout=self.timeout, context=context) as resp:
                data = resp.read()
                if not data:
                    return False
                output_file.write_bytes(data)
                return True
        except Exception:
            return False
