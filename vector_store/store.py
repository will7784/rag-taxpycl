"""
Módulo de Vector Store.
Usa ChromaDB como base de datos vectorial local.
Preparado para migración futura a Pinecone.
"""

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from rich.console import Console

import config

console = Console()


class VectorStoreManager:
    """
    Gestiona la base de datos vectorial local con ChromaDB.
    Diseñado para facilitar la migración a Pinecone u otros proveedores cloud.
    """

    def __init__(self):
        console.print("[blue]🔧 Inicializando embeddings (OpenAI)...[/blue]")
        self.embeddings = OpenAIEmbeddings(
            model=config.EMBEDDING_MODEL,
            api_key=config.OPENAI_API_KEY,
        )

        self.vectorstore = Chroma(
            collection_name=config.CHROMA_COLLECTION_NAME,
            embedding_function=self.embeddings,
            persist_directory=config.CHROMA_PERSIST_DIR,
        )
        console.print("[green]✅ Vector store inicializado (ChromaDB)[/green]")

    def add_documents(
        self, documents: list[Document], batch_size: int = 100
    ) -> list[str]:
        """
        Agrega documentos al vector store en lotes (batches).

        OpenAI tiene un límite de ~300K tokens por request de embeddings.
        Con batch_size=100 nos mantenemos dentro del límite.
        """
        if not documents:
            console.print("[yellow]⚠️ No hay documentos para agregar.[/yellow]")
            return []

        total = len(documents)
        console.print(
            f"[blue]📥 Agregando {total} chunks al vector store "
            f"(lotes de {batch_size})...[/blue]"
        )

        all_ids: list[str] = []
        for i in range(0, total, batch_size):
            batch = documents[i : i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (total + batch_size - 1) // batch_size
            console.print(
                f"  [dim]Lote {batch_num}/{total_batches} "
                f"({len(batch)} chunks)...[/dim]"
            )
            ids = self.vectorstore.add_documents(batch)
            all_ids.extend(ids)

        console.print(
            f"[green]✅ {len(all_ids)} chunks almacenados en ChromaDB[/green]"
        )
        return all_ids

    def similarity_search(self, query: str, k: int = 4) -> list[Document]:
        """Busca documentos similares a la consulta."""
        return [doc for doc, _ in self.similarity_search_with_score(query, k=k)]

    def similarity_search_with_score(
        self, query: str, k: int = 4
    ) -> list[tuple[Document, float]]:
        """
        Busca documentos similares con puntuación.

        Nota:
        En algunos estados corruptos/incompletos de Chroma, `documents` puede
        venir con `None`. En ese caso filtramos registros inválidos para evitar
        fallos de validación de LangChain/Pydantic.
        """
        collection = self.vectorstore._collection
        try:
            query_embedding = self.embeddings.embed_query(query)
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=k,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            console.print(
                f"  [red]⚠️ Error en similarity_search_with_score: {e}[/red]"
            )
            return []

        documents = (results.get("documents") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]

        safe_results: list[tuple[Document, float]] = []
        for doc_text, meta, distance in zip(documents, metadatas, distances):
            if not isinstance(doc_text, str) or not doc_text.strip():
                continue
            safe_results.append(
                (
                    Document(page_content=doc_text, metadata=meta or {}),
                    float(distance),
                )
            )
        return safe_results

    def search_by_document_content(
        self,
        contains: str,
        filename_filter: str | None = None,
        limit: int = 20,
    ) -> list[Document]:
        """
        Busca chunks cuyo CONTENIDO (page_content) contenga un texto.
        Usa ChromaDB's where_document/$contains (full-text search en docs).

        NOTA: ChromaDB $contains NO funciona en campos de metadata (where),
        solo funciona en where_document para buscar en el contenido.

        Args:
            contains: texto a buscar en el contenido (ej: 'ARTICULO 10°')
            filename_filter: filtrar por filename exacto (ej: 'DL-824_31-DIC-1974.pdf')
            limit: máximo de resultados
        """
        collection = self.vectorstore._collection

        # Construir filtros
        where_doc: dict = {"$contains": contains}
        where_meta: dict | None = None
        if filename_filter:
            where_meta = {"filename": filename_filter}

        try:
            kwargs: dict = {
                "where_document": where_doc,
                "include": ["documents", "metadatas"],
                "limit": limit,
            }
            if where_meta:
                kwargs["where"] = where_meta

            results = collection.get(**kwargs)
        except Exception as e:
            console.print(
                f"  [red]⚠️ Error en search_by_document_content: {e}[/red]"
            )
            return []

        docs = []
        for doc_text, meta in zip(
            results.get("documents", []),
            results.get("metadatas", []),
        ):
            if doc_text:
                docs.append(Document(page_content=doc_text, metadata=meta or {}))

        return docs

    def search_by_hierarchy(
        self,
        hierarchy_contains: str,
        filename_filter: str | None = None,
        limit: int = 40,
    ) -> list[Document]:
        """
        Busca chunks cuyo contenido incluya el hierarchy_path buscado.

        Cada chunk tiene el formato: [TITULO I > ... > ARTICULO X°...]\\n...
        Aprovechamos where_document/$contains para buscar en el contenido,
        ya que ChromaDB NO soporta $contains en metadata.

        Ideal para recuperar TODOS los sub-chunks de un artículo específico.

        Args:
            hierarchy_contains: texto a buscar (ej: "ARTICULO 17°")
            filename_filter: filtrar por filename exacto
            limit: máximo de resultados
        """
        collection = self.vectorstore._collection

        try:
            kwargs: dict = {
                "where_document": {"$contains": hierarchy_contains},
                "include": ["documents", "metadatas"],
                "limit": limit,
            }
            if filename_filter:
                kwargs["where"] = {"filename": filename_filter}

            results = collection.get(**kwargs)
        except Exception as e:
            console.print(
                f"  [red]⚠️ Error en search_by_hierarchy: {e}[/red]"
            )
            return []

        docs = []
        for doc_text, meta in zip(
            results.get("documents", []),
            results.get("metadatas", []),
        ):
            if doc_text:
                docs.append(Document(page_content=doc_text, metadata=meta or {}))

        return docs

    def search_by_source_type(
        self,
        source_type: str,
        contains: str | None = None,
        limit: int = 80,
    ) -> list[Document]:
        """
        Busca documentos por metadata source_type y opcionalmente por contenido.

        Útil para recuperar jurisprudencia SII sin mezclar con leyes/notas.
        """
        collection = self.vectorstore._collection
        kwargs: dict = {
            "where": {"source_type": source_type},
            "include": ["documents", "metadatas"],
            "limit": limit,
        }
        if contains:
            kwargs["where_document"] = {"$contains": contains}

        try:
            results = collection.get(**kwargs)
        except Exception as e:
            console.print(
                f"  [red]⚠️ Error en search_by_source_type: {e}[/red]"
            )
            return []

        docs: list[Document] = []
        for doc_text, meta in zip(
            results.get("documents", []),
            results.get("metadatas", []),
        ):
            if not isinstance(doc_text, str) or not doc_text.strip():
                continue
            docs.append(Document(page_content=doc_text, metadata=meta or {}))
        return docs

    def get_retriever(self, k: int = 4):
        """Retorna un retriever compatible con LangChain."""
        return self.vectorstore.as_retriever(
            search_type="similarity",
            search_kwargs={"k": k},
        )

    def get_collection_stats(self) -> dict:
        """Retorna estadísticas de la colección."""
        collection = self.vectorstore._collection
        count = collection.count()
        return {
            "collection_name": config.CHROMA_COLLECTION_NAME,
            "total_documents": count,
            "persist_directory": config.CHROMA_PERSIST_DIR,
        }

    def delete_by_filename(self, filename: str) -> int:
        """
        Elimina todos los chunks asociados a un archivo específico.
        Usa el campo 'filename' en metadata.

        Returns:
            Cantidad de chunks eliminados.
        """
        collection = self.vectorstore._collection
        # Obtener IDs que tengan ese filename en metadata
        results = collection.get(
            where={"filename": filename},
        )
        ids_to_delete = results["ids"]
        if ids_to_delete:
            collection.delete(ids=ids_to_delete)
            console.print(
                f"  [yellow]🗑️ {len(ids_to_delete)} chunks de "
                f"'{filename}' eliminados[/yellow]"
            )
        return len(ids_to_delete)

    def clear_collection(self):
        """Elimina todos los documentos de la colección."""
        collection = self.vectorstore._collection
        all_data = collection.get()
        if all_data["ids"]:
            collection.delete(ids=all_data["ids"])
            console.print(
                f"[yellow]🗑️ Se eliminaron {len(all_data['ids'])} "
                f"documentos de la colección.[/yellow]"
            )
        else:
            console.print("[yellow]La colección ya está vacía.[/yellow]")

    def migrate_to_pinecone(self):
        """Migra los datos de ChromaDB a Pinecone."""
        if not config.PINECONE_API_KEY:
            console.print(
                "[red]❌ Configura PINECONE_API_KEY en .env para migrar[/red]"
            )
            return

        try:
            from langchain_pinecone import PineconeVectorStore
            from pinecone import Pinecone, ServerlessSpec

            console.print("[blue]🚀 Iniciando migración a Pinecone...[/blue]")

            pc = Pinecone(api_key=config.PINECONE_API_KEY)

            existing_indexes = [idx.name for idx in pc.list_indexes()]
            if config.PINECONE_INDEX_NAME not in existing_indexes:
                pc.create_index(
                    name=config.PINECONE_INDEX_NAME,
                    dimension=1536,  # text-embedding-3-small
                    metric="cosine",
                    spec=ServerlessSpec(cloud="aws", region="us-east-1"),
                )
                console.print(
                    f"[green]✅ Índice '{config.PINECONE_INDEX_NAME}' "
                    f"creado en Pinecone[/green]"
                )

            collection = self.vectorstore._collection
            all_data = collection.get(include=["documents", "metadatas"])

            if not all_data["documents"]:
                console.print("[yellow]⚠️ No hay documentos para migrar.[/yellow]")
                return

            docs = [
                Document(page_content=doc, metadata=meta or {})
                for doc, meta in zip(all_data["documents"], all_data["metadatas"])
            ]

            PineconeVectorStore.from_documents(
                documents=docs,
                embedding=self.embeddings,
                index_name=config.PINECONE_INDEX_NAME,
            )

            console.print(
                f"[green]✅ {len(docs)} documentos migrados a Pinecone[/green]"
            )

        except ImportError:
            console.print(
                "[red]❌ Instala: pip install langchain-pinecone pinecone[/red]"
            )
        except Exception as e:
            console.print(f"[red]❌ Error en migración: {e}[/red]")
