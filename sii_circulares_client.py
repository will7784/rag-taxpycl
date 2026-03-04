"""
Cliente para índices de circulares SII por año.

Fuente ejemplo:
https://www.sii.cl/normativa_legislacion/circulares/2025/indcir2025.htm
"""

from __future__ import annotations

import html
import re
import ssl
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen


class SIICircularesClient:
    def __init__(self, timeout: int = 30, verify_ssl: bool = True) -> None:
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self._user_agent = "rag-documentos/1.0 (+sync-sii-circulares)"

    def _get_text(self, url: str) -> str:
        req = Request(url, headers={"User-Agent": self._user_agent}, method="GET")
        context = None if self.verify_ssl else ssl._create_unverified_context()
        with urlopen(req, timeout=self.timeout, context=context) as resp:
            return resp.read().decode("utf-8", errors="ignore")

    @staticmethod
    def _strip_tags(value: str) -> str:
        no_tags = re.sub(r"<[^>]+>", " ", value)
        text = html.unescape(no_tags)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _month_to_num(month_es: str) -> int | None:
        m = month_es.lower().strip()
        mapping = {
            "enero": 1,
            "febrero": 2,
            "marzo": 3,
            "abril": 4,
            "mayo": 5,
            "junio": 6,
            "julio": 7,
            "agosto": 8,
            "septiembre": 9,
            "setiembre": 9,
            "octubre": 10,
            "noviembre": 11,
            "diciembre": 12,
        }
        return mapping.get(m)

    @classmethod
    def _parse_title(cls, title: str) -> dict[str, Any]:
        """
        Ejemplo:
        "Circular N° 11 del 30 de Enero del 2025"
        """
        out: dict[str, Any] = {
            "numero": "",
            "fecha_iso": "",
        }
        m = re.search(
            r"Circular\s+N[°º]\s*([0-9]+)\s+del\s+([0-9]{1,2})\s+de\s+([A-Za-záéíóúÁÉÍÓÚ]+)\s+del\s+([0-9]{4})",
            title,
            re.IGNORECASE,
        )
        if not m:
            return out
        num = m.group(1).strip()
        day = int(m.group(2))
        month_name = m.group(3)
        year = int(m.group(4))
        month = cls._month_to_num(month_name)
        out["numero"] = num
        if month:
            out["fecha_iso"] = f"{year:04d}-{month:02d}-{day:02d}"
        return out

    def list_circulares_by_year(self, year: int) -> list[dict[str, Any]]:
        base = f"https://www.sii.cl/normativa_legislacion/circulares/{year}/"
        index_url = urljoin(base, f"indcir{year}.htm")
        html_text = self._get_text(index_url)

        pattern = re.compile(
            r"<h5[^>]*>\s*<a[^>]*href=['\"](?P<href>[^'\"]+)['\"][^>]*>(?P<title>.*?)</a>\s*</h5>\s*"
            r"<p[^>]*>(?P<summary>.*?)</p>\s*"
            r"<span[^>]*>\s*<i>(?P<source>.*?)</i>\s*</span>",
            re.IGNORECASE | re.DOTALL,
        )

        results: list[dict[str, Any]] = []
        for m in pattern.finditer(html_text):
            href = m.group("href").strip()
            title_raw = self._strip_tags(m.group("title"))
            summary_raw = self._strip_tags(m.group("summary"))
            source_raw = self._strip_tags(m.group("source"))
            parsed = self._parse_title(title_raw)
            pdf_url = urljoin(base, href)
            numero = parsed.get("numero", "")
            fecha_iso = parsed.get("fecha_iso", "")

            results.append(
                {
                    "year": year,
                    "numero": numero,
                    "fecha": fecha_iso,
                    "titulo": title_raw,
                    "resumen": summary_raw,
                    "fuente": source_raw,
                    "pdf_url": pdf_url,
                    "index_url": index_url,
                }
            )
        return results

