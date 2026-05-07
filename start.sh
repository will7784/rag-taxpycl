#!/bin/bash
set -e

echo "=== Taxpy startup ==="

# Verificar si chroma_db ya tiene documentos
DOCS=$(python -c "
try:
    from vector_store import VectorStoreManager
    vs = VectorStoreManager()
    stats = vs.get_collection_stats()
    print(int(stats.get('total_documents', 0)))
except Exception:
    print(0)
" 2>/dev/null)

echo "ChromaDB documents: $DOCS"

if [ "$DOCS" -eq "0" ]; then
    echo "=== ChromaDB vacío — ejecutando ingesta inicial ==="
    python main.py ingest
    echo "=== Ingesta completa ==="
fi

echo "=== Iniciando API server ==="
exec python main.py api-server
