"""
Configuración centralizada del proyecto RAG.
Carga variables de entorno desde .env
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# ============================================
# Rutas del proyecto
# ============================================
BASE_DIR = Path(__file__).parent
DOCUMENTS_DIR = BASE_DIR / "documents"
NOTES_DIR = BASE_DIR / "notes"
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", str(BASE_DIR / "chroma_db"))
AGENT_CONTEXT_FILE = BASE_DIR / "agente.md"

# Crear directorios si no existen
DOCUMENTS_DIR.mkdir(exist_ok=True)
NOTES_DIR.mkdir(exist_ok=True)
Path(CHROMA_PERSIST_DIR).mkdir(exist_ok=True)

# ============================================
# OpenAI
# ============================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# ============================================
# Embeddings (OpenAI)
# ============================================
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

# ============================================
# ChromaDB
# ============================================
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "rag_documentos")

# ============================================
# Chunking Estructural
# ============================================
MAX_CHUNK_SIZE = int(os.getenv("MAX_CHUNK_SIZE", "4000"))
MIN_CHUNK_SIZE = int(os.getenv("MIN_CHUNK_SIZE", "100"))

# Extensiones soportadas
SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".txt", ".md",
    ".csv", ".rtf", ".odt", ".html", ".htm",
}

# ============================================
# OCR (Tesseract)
# ============================================
TESSERACT_PATH = os.getenv(
    "TESSERACT_PATH", r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)
TESSERACT_LANG = os.getenv("TESSERACT_LANG", "spa+eng")

# ============================================
# Pinecone (migración futura)
# ============================================
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "rag-documentos")
PINECONE_ENVIRONMENT = os.getenv("PINECONE_ENVIRONMENT", "")

# ============================================
# Seguridad de URLs de jurisprudencia
# ============================================
_allowed_domains_raw = os.getenv(
    "ALLOWED_JURIS_PDF_DOMAINS",
    "sii.cl,pjud.cl,bcn.cl",
)
ALLOWED_JURIS_PDF_DOMAINS = tuple(
    d.strip().lower()
    for d in _allowed_domains_raw.split(",")
    if d.strip()
)

_blocked_hosts_raw = os.getenv(
    "BLOCKED_JURIS_PDF_HOSTS",
    "ejemplo.cl,example.com,localhost,127.0.0.1",
)
BLOCKED_JURIS_PDF_HOSTS = tuple(
    h.strip().lower()
    for h in _blocked_hosts_raw.split(",")
    if h.strip()
)

# ============================================
# Telegram MVP (Taxpy)
# ============================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_REQUIRE_INVITE = os.getenv("TELEGRAM_REQUIRE_INVITE", "false").lower() in {
    "1", "true", "yes", "on"
}
TELEGRAM_INVITE_CODES = tuple(
    c.strip()
    for c in os.getenv("TELEGRAM_INVITE_CODES", "").split(",")
    if c.strip()
)
TELEGRAM_FREE_QUERIES_PER_MONTH = int(
    os.getenv("TELEGRAM_FREE_QUERIES_PER_MONTH", "10")
)
TELEGRAM_PRO_PLAN_PRICE_USD = os.getenv("TELEGRAM_PRO_PLAN_PRICE_USD", "27")
TELEGRAM_DB_PATH = Path(
    os.getenv("TELEGRAM_DB_PATH", str(BASE_DIR / "taxpy_telegram.sqlite3"))
)

# ============================================
# API MVP (web -> backend Python)
# ============================================
API_SERVER_HOST = os.getenv("API_SERVER_HOST", "0.0.0.0")
API_SERVER_PORT = int(os.getenv("API_SERVER_PORT", "8000"))
API_ACCESS_TOKEN = os.getenv("API_ACCESS_TOKEN", "")
API_DB_PATH = Path(os.getenv("API_DB_PATH", str(BASE_DIR / "taxpy_api.sqlite3")))
