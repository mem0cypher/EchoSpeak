"""
Document store for Echo Speak.
Handles document ingestion, chunking, FAISS indexing, and retrieval.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

try:
    from langchain_core.documents import Document
except ImportError:
    try:
        from langchain.schema import Document
    except ImportError:
        from langchain.docstore.document import Document

try:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
except Exception:
    RecursiveCharacterTextSplitter = None

from langchain_community.vectorstores import FAISS

try:
    from rank_bm25 import BM25Okapi
except Exception:
    BM25Okapi = None

try:
    from sentence_transformers import CrossEncoder
except Exception:
    CrossEncoder = None


class DocumentStore:
    """Persistent FAISS-backed document store for RAG."""

    def __init__(self, embeddings: Any, index_dir: str, meta_path: str):
        self.embeddings = embeddings
        self.index_dir = Path(index_dir)
        self.meta_path = Path(meta_path)
        self.enabled = bool(self.embeddings)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._docs: Dict[str, Dict[str, Any]] = {}
        self._chunks: List[Document] = []
        self._chunks_by_id: Dict[str, Document] = {}
        self._bm25 = None
        self._bm25_docs: List[Document] = []
        self._bm25_tokens: List[List[str]] = []
        self._graph_entities: Dict[str, set[str]] = defaultdict(set)
        self._chunk_entities: Dict[str, List[str]] = {}
        self._reranker = None
        self.graph_path = self.index_dir / "doc_graph.json"
        self._load_settings()
        self._load_meta()

        if self.enabled:
            self.vector_store = self._load_or_create_vectorstore()
            self._refresh_indices()
        else:
            self.vector_store = None
            logger.warning("DocumentStore disabled (no embeddings available)")

    def _load_settings(self) -> None:
        self.doc_context_max_chars = int(getattr(config, "doc_context_max_chars", 2800) or 2800)
        self.doc_context_show_labels = bool(getattr(config, "doc_context_show_labels", True))
        self.doc_preview_chars = int(getattr(config, "doc_source_preview_chars", 160) or 160)
        self.hybrid_enabled = bool(getattr(config, "doc_hybrid_enabled", False))
        self.vector_k = int(getattr(config, "doc_vector_k", 30) or 30)
        self.bm25_k = int(getattr(config, "doc_bm25_k", 30) or 30)
        self.final_k = int(getattr(config, "doc_final_k", 5) or 5)
        self.candidate_k = int(getattr(config, "doc_candidate_k", 0) or 0)
        self.rrf_k = int(getattr(config, "doc_rrf_k", 60) or 60)
        self.rerank_enabled = bool(getattr(config, "doc_rerank_enabled", False))
        self.rerank_model = str(
            getattr(config, "doc_rerank_model", "cross-encoder/ms-marco-MiniLM-L-6-v2")
            or "cross-encoder/ms-marco-MiniLM-L-6-v2"
        ).strip()
        self.rerank_k = int(getattr(config, "doc_rerank_k", 30) or 30)
        self.graph_enabled = bool(getattr(config, "doc_graph_enabled", False))
        self.graph_expand_k = int(getattr(config, "doc_graph_expand_k", 12) or 12)
        self.graph_max_entities = int(getattr(config, "doc_graph_max_entities", 8) or 8)
        self.graph_query_entities = int(getattr(config, "doc_graph_query_entities", 8) or 8)
        if self.hybrid_enabled and BM25Okapi is None:
            logger.warning("DOC_HYBRID_ENABLED is true but rank-bm25 is unavailable; falling back to vector-only.")
            self.hybrid_enabled = False

    def _refresh_indices(self) -> None:
        self._chunks = list(self._iter_documents())
        self._chunks_by_id = {}
        for doc in self._chunks:
            chunk_id = self._chunk_key(doc)
            self._chunks_by_id[chunk_id] = doc
        if self.hybrid_enabled:
            self._build_bm25_index()
        else:
            self._bm25 = None
            self._bm25_docs = []
            self._bm25_tokens = []
        if self.graph_enabled:
            if not self._load_graph_index():
                self._build_graph_index(self._chunks)
        else:
            self._graph_entities = defaultdict(set)
            self._chunk_entities = {}

    def _iter_documents(self) -> Iterable[Document]:
        if not self.vector_store:
            return []
        store = getattr(self.vector_store, "docstore", None)
        d = getattr(store, "_dict", None) if store is not None else None
        if isinstance(d, dict):
            return [doc for doc in d.values() if not (getattr(doc, "metadata", {}) or {}).get("bootstrap")]
        payload = self.vector_store.get() or {}
        docs = payload.get("documents") or []
        metas = payload.get("metadatas") or []
        out: List[Document] = []
        n = min(len(docs), len(metas))
        for i in range(n):
            meta = metas[i] or {}
            if isinstance(meta, dict) and meta.get("bootstrap"):
                continue
            out.append(Document(page_content=docs[i] or "", metadata=meta))
        return out

    def _chunk_key(self, doc: Document) -> str:
        meta = getattr(doc, "metadata", {}) or {}
        doc_id = str(meta.get("doc_id") or "").strip()
        chunk = meta.get("chunk")
        if doc_id and chunk is not None:
            return f"{doc_id}:{chunk}"
        if doc_id:
            return doc_id
        safe = re.sub(r"\s+", " ", (getattr(doc, "page_content", "") or "").strip())
        return f"chunk:{abs(hash(safe))}"

    def _resolve_candidate_k(self, final_k: int) -> int:
        if self.candidate_k > 0:
            return max(self.candidate_k, final_k)
        return max(final_k * 6, self.vector_k, self.bm25_k, self.rerank_k)

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r"[a-z0-9_]+", (text or "").lower())

    def _build_bm25_index(self) -> None:
        docs = list(self._chunks)
        if not docs or BM25Okapi is None:
            self._bm25 = None
            self._bm25_docs = []
            self._bm25_tokens = []
            return
        tokens = [self._tokenize(doc.page_content) for doc in docs]
        self._bm25 = BM25Okapi(tokens)
        self._bm25_docs = docs
        self._bm25_tokens = tokens

    def _vector_search(self, query: str, k: int) -> List[Document]:
        results = self.vector_store.similarity_search(query, k=max(k, 4))
        return [d for d in results if not (getattr(d, "metadata", {}) or {}).get("bootstrap")]

    def _bm25_search(self, query: str, k: int) -> List[Document]:
        if not self._bm25 or not self._bm25_docs:
            return []
        tokens = self._tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        hits = [self._bm25_docs[i] for i in ranked[: max(k, 1)]]
        return hits

    def _rrf_fuse(self, primary: List[Document], secondary: List[Document]) -> List[Document]:
        scores: Dict[str, float] = {}
        doc_map: Dict[str, Document] = {}
        for rank, doc in enumerate(primary, start=1):
            key = self._chunk_key(doc)
            doc_map[key] = doc
            scores[key] = scores.get(key, 0.0) + 1.0 / (self.rrf_k + rank)
        for rank, doc in enumerate(secondary, start=1):
            key = self._chunk_key(doc)
            doc_map[key] = doc
            scores[key] = scores.get(key, 0.0) + 1.0 / (self.rrf_k + rank)
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [doc_map[key] for key, _score in ordered if key in doc_map]

    def _dedupe_docs(self, docs: List[Document]) -> List[Document]:
        seen = set()
        unique: List[Document] = []
        for doc in docs:
            key = self._chunk_key(doc)
            if key in seen:
                continue
            seen.add(key)
            unique.append(doc)
        return unique

    def _get_reranker(self) -> Optional[Any]:
        if not self.rerank_enabled:
            return None
        if self._reranker is not None:
            return self._reranker
        if CrossEncoder is None:
            logger.warning("Reranker requested but sentence-transformers is unavailable.")
            return None
        try:
            self._reranker = CrossEncoder(self.rerank_model)
        except Exception as exc:
            logger.warning(f"Failed to load reranker model: {exc}")
            self._reranker = None
        return self._reranker

    def _rerank(self, query: str, docs: List[Document], k: int) -> List[Document]:
        reranker = self._get_reranker()
        if reranker is None:
            return docs[:k]
        pairs = [(query, doc.page_content) for doc in docs[: max(self.rerank_k, k)]]
        if not pairs:
            return []
        try:
            scores = reranker.predict(pairs)
        except Exception as exc:
            logger.warning(f"Rerank failed: {exc}")
            return docs[:k]
        scored = list(zip(docs[: len(scores)], scores))
        scored.sort(key=lambda item: float(item[1]), reverse=True)
        return [doc for doc, _score in scored[:k]]

    def _load_graph_index(self) -> bool:
        if not self.graph_path.exists():
            return False
        try:
            payload = json.loads(self.graph_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        meta = payload.get("meta") or {}
        if meta.get("chunk_count") != len(self._chunks):
            return False
        entities = payload.get("entities") or {}
        chunks = payload.get("chunks") or {}
        graph_entities: Dict[str, set[str]] = defaultdict(set)
        chunk_entities: Dict[str, List[str]] = {}
        if isinstance(entities, dict):
            for key, value in entities.items():
                if not isinstance(value, list):
                    continue
                graph_entities[str(key)] = set(str(v) for v in value)
        if isinstance(chunks, dict):
            for key, value in chunks.items():
                if not isinstance(value, list):
                    continue
                chunk_entities[str(key)] = [str(v) for v in value]
        self._graph_entities = graph_entities
        self._chunk_entities = chunk_entities
        return True

    def _save_graph_index(self) -> None:
        try:
            payload = {
                "meta": {
                    "chunk_count": len(self._chunks),
                    "updated_at": datetime.now().isoformat(),
                },
                "entities": {k: sorted(list(v)) for k, v in self._graph_entities.items()},
                "chunks": self._chunk_entities,
            }
            self.graph_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Failed to save graph index: {exc}")

    def _extract_entities(self, text: str, limit: int) -> List[str]:
        if not text:
            return []
        tokens = re.findall(r"\b[A-Za-z][A-Za-z0-9_-]{2,}\b", text)
        stop = {
            "the", "and", "for", "with", "that", "this", "from", "into", "your", "you", "are",
            "was", "were", "have", "has", "had", "can", "will", "not", "but", "use", "using",
            "about", "when", "where", "what", "which", "who", "why", "how", "also", "such",
            "into", "onto", "over", "under", "after", "before", "between", "within", "into",
        }
        seen = set()
        entities: List[str] = []
        for token in tokens:
            val = token.lower()
            if val in stop or val in seen:
                continue
            seen.add(val)
            entities.append(val)
            if len(entities) >= limit:
                break
        return entities

    def _build_graph_index(self, docs: List[Document]) -> None:
        graph_entities: Dict[str, set[str]] = defaultdict(set)
        chunk_entities: Dict[str, List[str]] = {}
        for doc in docs:
            chunk_id = self._chunk_key(doc)
            entities = self._extract_entities(doc.page_content, self.graph_max_entities)
            if not entities:
                continue
            chunk_entities[chunk_id] = entities
            for ent in entities:
                graph_entities[ent].add(chunk_id)
        self._graph_entities = graph_entities
        self._chunk_entities = chunk_entities
        self._save_graph_index()

    def _expand_with_graph(self, base_docs: List[Document], query: str) -> List[Document]:
        if not self.graph_enabled or not self._graph_entities:
            return base_docs
        seed_entities = set(self._extract_entities(query, self.graph_query_entities))
        for doc in base_docs:
            chunk_id = self._chunk_key(doc)
            for ent in self._chunk_entities.get(chunk_id, []):
                seed_entities.add(ent)
                if len(seed_entities) >= self.graph_query_entities:
                    break
        if not seed_entities:
            return base_docs
        scores: Dict[str, int] = {}
        for ent in seed_entities:
            for chunk_id in self._graph_entities.get(ent, set()):
                scores[chunk_id] = scores.get(chunk_id, 0) + 1
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        expanded: List[Document] = []
        for chunk_id, _score in ordered:
            doc = self._chunks_by_id.get(chunk_id)
            if doc is None:
                continue
            expanded.append(doc)
            if len(expanded) >= self.graph_expand_k:
                break
        merged = self._dedupe_docs(base_docs + expanded)
        return merged

    def _format_context_chunk(self, doc: Document, chunk_id: str) -> str:
        meta = getattr(doc, "metadata", {}) or {}
        label = meta.get("filename") or meta.get("source") or meta.get("doc_id") or "document"
        content = (doc.page_content or "").strip()
        if not self.doc_context_show_labels:
            return content
        return f"[{chunk_id}] {label}\n{content}"

    def _clamp_context(self, joined: str) -> str:
        if self.doc_context_max_chars <= 0:
            return joined.strip()
        if len(joined) > self.doc_context_max_chars:
            return joined[: self.doc_context_max_chars].rstrip() + "…"
        return joined.strip()

    def _load_meta(self) -> None:
        if not self.meta_path.exists():
            self._docs = {}
            return
        try:
            data = json.loads(self.meta_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._docs = data
            else:
                self._docs = {}
        except Exception as exc:
            logger.warning(f"Failed to load doc metadata: {exc}")
            self._docs = {}

    def _save_meta(self) -> None:
        try:
            self.meta_path.parent.mkdir(parents=True, exist_ok=True)
            self.meta_path.write_text(json.dumps(self._docs, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Failed to save doc metadata: {exc}")

    def _load_or_create_vectorstore(self) -> FAISS:
        if self.index_dir.exists() and any(self.index_dir.iterdir()):
            try:
                vs = FAISS.load_local(
                    str(self.index_dir),
                    self.embeddings,
                    allow_dangerous_deserialization=True,
                )
                logger.info("Loaded existing document index")
                return vs
            except Exception as exc:
                logger.warning(f"Failed to load document index: {exc}. Rebuilding.")
        return FAISS.from_texts(["bootstrap"], self.embeddings, metadatas=[{"bootstrap": True}])

    def _split_text(self, text: str) -> List[str]:
        clean = (text or "").strip()
        if not clean:
            return []
        if RecursiveCharacterTextSplitter is not None:
            splitter = RecursiveCharacterTextSplitter(chunk_size=950, chunk_overlap=160)
            return [c.strip() for c in splitter.split_text(clean) if c.strip()]
        # Fallback: simple chunking
        chunks = []
        size = 900
        overlap = 140
        i = 0
        while i < len(clean):
            chunk = clean[i : i + size]
            if chunk.strip():
                chunks.append(chunk.strip())
            i += max(1, size - overlap)
        return chunks

    def add_document(self, filename: str, text: str, source: str = "", mime: str = "") -> Dict[str, Any]:
        if not self.enabled or self.vector_store is None:
            raise RuntimeError("Document store is disabled (no embeddings available)")

        chunks = self._split_text(text)
        if not chunks:
            raise ValueError("No extractable text found in document")

        doc_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        documents = [
            Document(
                page_content=chunk,
                metadata={
                    "doc_id": doc_id,
                    "filename": filename,
                    "chunk": idx,
                    "source": source,
                    "mime": mime,
                    "timestamp": now,
                },
            )
            for idx, chunk in enumerate(chunks)
        ]

        self.vector_store.add_documents(documents)
        self.vector_store.save_local(str(self.index_dir))

        meta = {
            "id": doc_id,
            "filename": filename,
            "chunks": len(chunks),
            "source": source,
            "mime": mime,
            "timestamp": now,
        }
        self._docs[doc_id] = meta
        self._save_meta()
        self._refresh_indices()
        return meta

    def list_documents(self) -> List[Dict[str, Any]]:
        items = list(self._docs.values())
        items.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
        return items

    def delete_documents(self, ids: List[str]) -> int:
        if not self.enabled or self.vector_store is None:
            return 0
        id_set = {str(i) for i in ids if i}
        if not id_set:
            return 0

        store = getattr(self.vector_store, "docstore", None)
        d = getattr(store, "_dict", None) if store is not None else None
        kept_texts: List[str] = []
        kept_metas: List[Dict[str, Any]] = []
        deleted = 0

        if isinstance(d, dict):
            for _doc_id, doc in d.items():
                meta = getattr(doc, "metadata", {}) or {}
                if isinstance(meta, dict) and meta.get("bootstrap"):
                    continue
                if meta.get("doc_id") in id_set:
                    deleted += 1
                    continue
                kept_texts.append(getattr(doc, "page_content", "") or "")
                kept_metas.append(meta if isinstance(meta, dict) else {})
        else:
            payload = self.vector_store.get() or {}
            docs = payload.get("documents") or []
            metas = payload.get("metadatas") or []
            n = min(len(docs), len(metas))
            for i in range(n):
                meta = metas[i] or {}
                if isinstance(meta, dict) and meta.get("bootstrap"):
                    continue
                if meta.get("doc_id") in id_set:
                    deleted += 1
                    continue
                kept_texts.append(docs[i] or "")
                kept_metas.append(meta if isinstance(meta, dict) else {})

        if deleted <= 0:
            return 0

        if kept_texts:
            texts = ["bootstrap"] + kept_texts
            metas = [{"bootstrap": True}] + kept_metas
            self.vector_store = FAISS.from_texts(texts, self.embeddings, metadatas=metas)
        else:
            self.vector_store = FAISS.from_texts(["bootstrap"], self.embeddings, metadatas=[{"bootstrap": True}])
        self.vector_store.save_local(str(self.index_dir))

        for doc_id in list(self._docs.keys()):
            if doc_id in id_set:
                self._docs.pop(doc_id, None)
        self._save_meta()
        self._refresh_indices()
        return deleted

    def clear(self) -> None:
        if not self.enabled or self.vector_store is None:
            return
        self.vector_store = FAISS.from_texts(["bootstrap"], self.embeddings, metadatas=[{"bootstrap": True}])
        self.vector_store.save_local(str(self.index_dir))
        self._docs = {}
        self._save_meta()
        self._refresh_indices()

    def query(self, query: str, k: int = 4) -> Tuple[str, List[Dict[str, Any]]]:
        if not self.enabled or self.vector_store is None:
            return "", []
        q = (query or "").strip()
        if not q:
            return "", []

        final_k = max(int(k or 0), 1)
        candidate_k = self._resolve_candidate_k(final_k)

        if self.hybrid_enabled:
            vector_docs = self._vector_search(q, k=max(self.vector_k, candidate_k))
            bm25_docs = self._bm25_search(q, k=max(self.bm25_k, candidate_k))
            candidates = self._rrf_fuse(vector_docs, bm25_docs)
        else:
            candidates = self._vector_search(q, k=max(candidate_k, 8))

        candidates = self._dedupe_docs(candidates)
        if self.graph_enabled:
            candidates = self._expand_with_graph(candidates, q)
        if candidate_k > 0 and len(candidates) > candidate_k:
            candidates = candidates[:candidate_k]

        if self.rerank_enabled:
            results = self._rerank(q, candidates, final_k)
        else:
            results = candidates[:final_k]

        if not results:
            return "", []

        context_parts: List[str] = []
        sources: List[Dict[str, Any]] = []
        seen = set()

        for doc in results:
            meta = getattr(doc, "metadata", {}) or {}
            chunk_id = self._chunk_key(doc)
            if chunk_id in seen:
                continue
            seen.add(chunk_id)

            text = getattr(doc, "page_content", "") or ""
            preview = text.strip()
            if self.doc_preview_chars > 0 and len(preview) > self.doc_preview_chars:
                preview = preview[: self.doc_preview_chars].rstrip() + "…"

            sources.append({
                "id": meta.get("doc_id") or chunk_id,
                "chunk_id": chunk_id,
                "chunk": meta.get("chunk"),
                "filename": meta.get("filename"),
                "source": meta.get("source"),
                "timestamp": meta.get("timestamp"),
                "preview": preview,
            })

            if text.strip():
                context_parts.append(self._format_context_chunk(doc, chunk_id))

        joined = "\n\n".join(context_parts)
        joined = self._clamp_context(joined)
        return joined, sources
