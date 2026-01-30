"""
Memory module for Echo Speak.
Provides FAISS-based vector store for conversation memory persistence.
"""

import os
import re
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any, Iterable, Tuple
from datetime import datetime
from loguru import logger

try:
    from langchain_core.documents import Document
except ImportError:
    try:
        from langchain.schema import Document
    except ImportError:
        from langchain.docstore.document import Document
try:
    from langchain_openai import OpenAIEmbeddings
except ImportError:
    OpenAIEmbeddings = None
from langchain_community.vectorstores import FAISS

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config


class AgentMemory:
    """Manages conversation memory using FAISS vector store."""

    def __init__(self, memory_path: Optional[str] = None):
        self.memory_root = Path(memory_path or str(config.memory_path))
        self.memory_path = str(self.memory_root)
        self.use_faiss = True
        self.file_memory_enabled = bool(getattr(config, "file_memory_enabled", False))
        self.file_memory_dir = Path(str(getattr(config, "file_memory_dir", "")).strip() or str(Path(self.memory_path).parent))
        self.file_memory_log_conversations = bool(getattr(config, "file_memory_log_conversations", True))
        self.file_memory_max_chars = int(getattr(config, "file_memory_max_chars", 2000) or 2000)
        self.partition_enabled = bool(getattr(config, "memory_partition_enabled", False))
        self._vector_stores: Dict[str, FAISS] = {}

        api_key = config.openai.api_key if config.openai.api_key else os.getenv("OPENAI_API_KEY", "")

        self.embeddings = None
        if api_key:
            if OpenAIEmbeddings is None:
                raise ImportError("langchain-openai is required for OpenAI embeddings")
            self.embeddings = OpenAIEmbeddings(model="text-embedding-3-small", api_key=api_key)
        else:
            try:
                try:
                    from langchain_huggingface import HuggingFaceEmbeddings
                except ImportError:
                    from langchain_community.embeddings import HuggingFaceEmbeddings
                self.embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
                logger.info("Using local HuggingFace embeddings for memory (no OpenAI key)")
            except Exception as e:
                logger.warning(f"No OpenAI key and local embeddings unavailable ({e}). Using simple memory storage (FAISS disabled).")
                self.use_faiss = False
                self.simple_memory = []

        if self.use_faiss and self.embeddings is not None:
            if self.partition_enabled:
                self.memory_root.mkdir(parents=True, exist_ok=True)
                self.vector_store = None
            else:
                self.vector_store = self._load_or_create_vectorstore(self.memory_root, create_if_missing=True)
        logger.info(f"Memory initialized at: {self.memory_root}")

    def _normalize_mode_value(self, mode: Optional[str]) -> str:
        default_mode = str(getattr(config, "memory_default_mode", "general") or "general").strip() or "general"
        if mode is None:
            return default_mode
        mode_val = str(mode).strip()
        return mode_val or default_mode

    def _normalize_thread_id(self, thread_id: Optional[str]) -> str:
        if thread_id is None:
            return "default"
        thread_val = str(thread_id).strip()
        return thread_val or "default"

    def _resolve_mode_filter(self, mode: Optional[str]) -> Optional[str]:
        if mode is None:
            return None
        mode_val = str(mode).strip()
        if not mode_val:
            return None
        if mode_val.lower() in {"all", "*"}:
            return None
        return mode_val

    def _mode_matches(self, candidate: Optional[str], mode: Optional[str]) -> bool:
        mode_filter = self._resolve_mode_filter(mode)
        if mode_filter is None:
            return True
        return self._normalize_mode_value(candidate) == mode_filter

    def _thread_matches(self, candidate: Optional[str], thread_id: Optional[str]) -> bool:
        if thread_id is None:
            return True
        thread_val = self._normalize_thread_id(thread_id)
        return self._normalize_thread_id(candidate) == thread_val

    def _build_namespace_key(self, mode: Optional[str], thread_id: Optional[str]) -> str:
        mode_val = self._normalize_mode_value(mode)
        thread_val = self._normalize_thread_id(thread_id)
        return f"{mode_val}:{thread_val}"

    def _build_namespaced_id(self, mode: Optional[str], thread_id: Optional[str]) -> str:
        namespace_key = self._build_namespace_key(mode, thread_id)
        return f"{namespace_key}:{uuid.uuid4()}"

    def _sanitize_component(self, value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip())
        cleaned = cleaned.strip("_")
        return cleaned or "default"

    def _namespace_dir(self, mode: str, thread_id: str) -> Path:
        return self.memory_root / self._sanitize_component(mode) / self._sanitize_component(thread_id)

    def _has_index_files(self, path: Path) -> bool:
        if not path.exists() or not path.is_dir():
            return False
        return any((path / name).exists() for name in ["index.faiss", "index.pkl"])

    def _load_or_create_vectorstore(self, path: Path, create_if_missing: bool = True) -> Optional[FAISS]:
        if self.use_faiss and self._has_index_files(path):
            try:
                vector_store = FAISS.load_local(
                    str(path),
                    self.embeddings,
                    allow_dangerous_deserialization=True,
                )
                logger.info("Loaded existing memory from disk")
                return vector_store
            except Exception as e:
                logger.warning(f"Failed to load existing memory: {e}. Creating new memory.")
        if not create_if_missing:
            return None
        path.mkdir(parents=True, exist_ok=True)
        return FAISS.from_texts(["bootstrap"], self.embeddings, metadatas=[{"bootstrap": True}])

    def _get_vector_store(self, mode: str, thread_id: str, create_if_missing: bool) -> Optional[FAISS]:
        if not self.use_faiss or self.embeddings is None:
            return None
        if not self.partition_enabled:
            return self.vector_store
        path = self._namespace_dir(mode, thread_id)
        cache_key = str(path)
        cached = self._vector_stores.get(cache_key)
        if cached is not None:
            return cached
        store = self._load_or_create_vectorstore(path, create_if_missing=create_if_missing)
        if store is not None:
            self._vector_stores[cache_key] = store
        return store

    def _iter_namespace_paths(self, mode: Optional[str], thread_id: Optional[str]) -> Iterable[Tuple[Path, Optional[str], Optional[str]]]:
        if not self.partition_enabled:
            yield self.memory_root, None, None
            return
        mode_filter = self._resolve_mode_filter(mode)
        thread_filter = self._normalize_thread_id(thread_id) if thread_id is not None else None
        if mode_filter is not None:
            mode_dir = self.memory_root / self._sanitize_component(mode_filter)
            if not mode_dir.exists() or not mode_dir.is_dir():
                return
            if thread_filter is not None:
                thread_dir = mode_dir / self._sanitize_component(thread_filter)
                if thread_dir.exists() and thread_dir.is_dir():
                    yield thread_dir, mode_filter, thread_filter
                return
            for thread_dir in mode_dir.iterdir():
                if thread_dir.is_dir():
                    yield thread_dir, mode_filter, thread_dir.name
            return
        for mode_dir in self.memory_root.iterdir() if self.memory_root.exists() else []:
            if not mode_dir.is_dir():
                continue
            if thread_filter is not None:
                thread_dir = mode_dir / self._sanitize_component(thread_filter)
                if thread_dir.exists() and thread_dir.is_dir():
                    yield thread_dir, mode_dir.name, thread_filter
                continue
            for thread_dir in mode_dir.iterdir():
                if thread_dir.is_dir():
                    yield thread_dir, mode_dir.name, thread_dir.name

    def _iter_vector_stores(self, mode: Optional[str], thread_id: Optional[str]) -> Iterable[Tuple[Path, FAISS]]:
        if not self.partition_enabled:
            if self.vector_store is not None:
                yield self.memory_root, self.vector_store
            return
        for path, mode_val, thread_val in self._iter_namespace_paths(mode, thread_id):
            cache_key = str(path)
            cached = self._vector_stores.get(cache_key)
            if cached is not None:
                yield path, cached
                continue
            store = self._load_or_create_vectorstore(path, create_if_missing=False)
            if store is None:
                continue
            self._vector_stores[cache_key] = store
            yield path, store

    def _iter_store_records(self, store: FAISS) -> Iterable[Tuple[str, str, Dict[str, Any]]]:
        store_obj = getattr(store, "docstore", None)
        d = getattr(store_obj, "_dict", None) if store_obj is not None else None
        if isinstance(d, dict):
            for doc_id, doc in d.items():
                meta = getattr(doc, "metadata", {}) or {}
                if isinstance(meta, dict) and meta.get("bootstrap"):
                    continue
                yield str(doc_id), getattr(doc, "page_content", "") or "", meta if isinstance(meta, dict) else {}
            return
        payload = store.get() or {}
        ids = payload.get("ids") or []
        docs = payload.get("documents") or []
        metas = payload.get("metadatas") or []
        n = min(len(ids), len(docs), len(metas))
        for i in range(n):
            meta = metas[i] or {}
            if isinstance(meta, dict) and meta.get("bootstrap"):
                continue
            yield str(ids[i]), docs[i] or "", meta if isinstance(meta, dict) else {}

    def _save_vector_store(self, store: FAISS, path: Path) -> None:
        if store is None:
            return
        try:
            path.mkdir(parents=True, exist_ok=True)
            store.save_local(str(path))
            logger.debug("Memory saved to disk")
        except Exception as e:
            logger.error(f"Failed to save memory: {e}")

    def _search_store(self, store: FAISS, query: str, k: int) -> List[Tuple[Document, Optional[float]]]:
        if store is None:
            return []
        if hasattr(store, "similarity_search_with_score"):
            try:
                scored = store.similarity_search_with_score(query, k=max(k, 4))
                return [
                    (doc, score)
                    for doc, score in scored
                    if not (getattr(doc, "metadata", {}) or {}).get("bootstrap")
                ]
            except Exception:
                pass
        docs = store.similarity_search(query, k=max(k, 4))
        return [
            (doc, None)
            for doc in docs
            if not (getattr(doc, "metadata", {}) or {}).get("bootstrap")
        ]

    def add_conversation(self, user_message: str, ai_response: str, mode: Optional[str] = None, thread_id: Optional[str] = None) -> None:
        timestamp = datetime.now().isoformat()
        combined_text = f"User: {user_message}\nAI: {ai_response}\nTimestamp: {timestamp}"
        mode_value = self._normalize_mode_value(mode)
        thread_value = self._normalize_thread_id(thread_id)
        namespace_key = self._build_namespace_key(mode_value, thread_value)
        doc_id = self._build_namespaced_id(mode_value, thread_value)

        if self.use_faiss:
            store = self._get_vector_store(mode_value, thread_value, create_if_missing=True)
            if store is None:
                if not hasattr(self, "simple_memory"):
                    self.simple_memory = []
                self.use_faiss = False
                logger.warning("Memory store unavailable; falling back to simple memory list.")
                self.simple_memory.append({
                    "id": str(uuid.uuid4()),
                    "text": combined_text,
                    "timestamp": timestamp,
                    "mode": mode_value,
                    "thread_id": thread_value,
                    "namespace": namespace_key,
                })
            else:
                metadata = {
                    "timestamp": timestamp,
                    "type": "conversation",
                    "mode": mode_value,
                    "thread_id": thread_value,
                    "namespace": namespace_key,
                }
                try:
                    store.add_texts([combined_text], metadatas=[metadata], ids=[doc_id])
                except Exception:
                    document = Document(page_content=combined_text, metadata=metadata)
                    store.add_documents([document])
                if self.partition_enabled:
                    path = self._namespace_dir(mode_value, thread_value)
                else:
                    path = self.memory_root
                self._save_vector_store(store, path)
        else:
            self.simple_memory.append({
                "id": str(uuid.uuid4()),
                "text": combined_text,
                "timestamp": timestamp,
                "mode": mode_value,
                "thread_id": thread_value,
                "namespace": namespace_key,
            })
        if self.file_memory_enabled and self.file_memory_log_conversations:
            self.append_daily_memory(
                f"User: {user_message}\nAI: {ai_response}\nTimestamp: {timestamp}",
                mode=mode_value,
                thread_id=thread_value,
            )
        logger.debug(f"Added conversation to memory: {user_message[:50]}...")

    def _daily_memory_path(self, day: Optional[datetime] = None) -> Path:
        day = day or datetime.now()
        filename = f"{day.strftime('%Y-%m-%d')}.md"
        return self.file_memory_dir / "memory" / filename

    def _ensure_memory_dirs(self) -> None:
        try:
            (self.file_memory_dir / "memory").mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning(f"Failed to ensure memory dirs: {exc}")

    def _sanitize_memory_text(self, text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        max_chars = max(200, int(self.file_memory_max_chars or 0))
        if len(raw) > max_chars:
            raw = raw[:max_chars].rstrip() + "..."
        return raw

    def append_daily_memory(self, text: str, mode: Optional[str] = None, thread_id: Optional[str] = None) -> bool:
        if not self.file_memory_enabled:
            return False
        cleaned = self._sanitize_memory_text(text)
        if not cleaned:
            return False
        self._ensure_memory_dirs()
        path = self._daily_memory_path()
        stamp = datetime.now().strftime("%H:%M")
        mode_val = self._normalize_mode_value(mode)
        thread_val = self._normalize_thread_id(thread_id)
        header = f"- [{stamp}] ({mode_val}/{thread_val})"
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{header} {cleaned}\n")
            return True
        except Exception as exc:
            logger.warning(f"Failed to write daily memory: {exc}")
            return False

    def append_curated_memory(self, text: str) -> bool:
        if not self.file_memory_enabled:
            return False
        cleaned = self._sanitize_memory_text(text)
        if not cleaned:
            return False
        self._ensure_memory_dirs()
        path = self.file_memory_dir / "MEMORY.md"
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"- {cleaned}\n")
            return True
        except Exception as exc:
            logger.warning(f"Failed to write curated memory: {exc}")
            return False

    def list_items(self, offset: int = 0, limit: int = 200, mode: Optional[str] = None, thread_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if limit <= 0:
            return []

        if not self.use_faiss:
            items: List[Dict[str, Any]] = []
            for i, m in enumerate(self.simple_memory):
                mid = m.get("id") or f"simple-{i}"
                ts = m.get("timestamp")
                mode_val = m.get("mode")
                if not self._mode_matches(mode_val, mode):
                    continue
                if not self._thread_matches(m.get("thread_id"), thread_id):
                    continue
                items.append(
                    {
                        "id": str(mid),
                        "text": m.get("text") or "",
                        "timestamp": ts,
                        "metadata": {
                            "timestamp": ts,
                            "type": "conversation",
                            "mode": self._normalize_mode_value(mode_val),
                            "thread_id": self._normalize_thread_id(m.get("thread_id")),
                            "namespace": m.get("namespace") or self._build_namespace_key(mode_val, m.get("thread_id")),
                        },
                    }
                )
            items.sort(key=lambda x: (x.get("timestamp") or ""), reverse=True)
            return items[offset : offset + limit]

        items: List[Dict[str, Any]] = []
        if self.partition_enabled:
            for _path, store in self._iter_vector_stores(mode, thread_id):
                for doc_id, text, meta in self._iter_store_records(store):
                    ts = meta.get("timestamp") if isinstance(meta, dict) else None
                    mode_val = meta.get("mode") if isinstance(meta, dict) else None
                    if not self._mode_matches(mode_val, mode):
                        continue
                    if not self._thread_matches(meta.get("thread_id") if isinstance(meta, dict) else None, thread_id):
                        continue
                    items.append({
                        "id": str(doc_id),
                        "text": text or "",
                        "timestamp": ts,
                        "metadata": meta if isinstance(meta, dict) else {},
                    })
        else:
            vs = self.vector_store
            if vs is None:
                return []
            store = getattr(vs, "docstore", None)
            d = getattr(store, "_dict", None) if store is not None else None
            if isinstance(d, dict):
                for doc_id, doc in d.items():
                    meta = getattr(doc, "metadata", {}) or {}
                    if isinstance(meta, dict) and meta.get("bootstrap"):
                        continue
                    ts = meta.get("timestamp") if isinstance(meta, dict) else None
                    mode_val = meta.get("mode") if isinstance(meta, dict) else None
                    if not self._mode_matches(mode_val, mode):
                        continue
                    if not self._thread_matches(meta.get("thread_id") if isinstance(meta, dict) else None, thread_id):
                        continue
                    items.append(
                        {
                            "id": str(doc_id),
                            "text": getattr(doc, "page_content", "") or "",
                            "timestamp": ts,
                            "metadata": meta if isinstance(meta, dict) else {},
                        }
                    )
            else:
                payload = vs.get() or {}
                ids = payload.get("ids") or []
                docs = payload.get("documents") or []
                metas = payload.get("metadatas") or []
                n = min(len(ids), len(docs), len(metas))
                for i in range(n):
                    meta = metas[i] or {}
                    if isinstance(meta, dict) and meta.get("bootstrap"):
                        continue
                    ts = meta.get("timestamp") if isinstance(meta, dict) else None
                    mode_val = meta.get("mode") if isinstance(meta, dict) else None
                    if not self._mode_matches(mode_val, mode):
                        continue
                    if not self._thread_matches(meta.get("thread_id") if isinstance(meta, dict) else None, thread_id):
                        continue
                    items.append({"id": str(ids[i]), "text": docs[i] or "", "timestamp": ts, "metadata": meta if isinstance(meta, dict) else {}})

        items.sort(key=lambda x: (x.get("timestamp") or ""), reverse=True)
        return items[offset : offset + limit]

    def delete_items(self, ids: List[str]) -> int:
        if not ids:
            return 0
        id_set = {str(i) for i in ids if i is not None}
        if not id_set:
            return 0

        if not self.use_faiss:
            before = len(self.simple_memory)
            kept = []
            for i, m in enumerate(self.simple_memory):
                mid = m.get("id") or f"simple-{i}"
                if str(mid) in id_set:
                    continue
                kept.append(m)
            self.simple_memory = kept
            return before - len(self.simple_memory)

        deleted_total = 0
        if self.partition_enabled:
            for path, store in self._iter_vector_stores(None, None):
                kept_ids: List[str] = []
                kept_texts: List[str] = []
                kept_metas: List[Dict[str, Any]] = []
                deleted = 0
                for doc_id, text, meta in self._iter_store_records(store):
                    if str(doc_id) in id_set:
                        deleted += 1
                        continue
                    kept_ids.append(str(doc_id))
                    kept_texts.append(text or "")
                    kept_metas.append(meta if isinstance(meta, dict) else {})
                if deleted <= 0:
                    continue
                deleted_total += deleted
                new_store = FAISS.from_texts(["bootstrap"], self.embeddings, metadatas=[{"bootstrap": True}])
                if kept_texts:
                    try:
                        new_store.add_texts(kept_texts, metadatas=kept_metas, ids=kept_ids)
                    except Exception:
                        new_store.add_texts(kept_texts, metadatas=kept_metas)
                self._save_vector_store(new_store, path)
                self._vector_stores[str(path)] = new_store
            return deleted_total

        vs = self.vector_store
        if vs is None:
            return 0
        store = getattr(vs, "docstore", None)
        d = getattr(store, "_dict", None) if store is not None else None

        kept_ids = []
        kept_texts = []
        kept_metas = []
        deleted = 0

        if isinstance(d, dict):
            for doc_id, doc in d.items():
                meta = getattr(doc, "metadata", {}) or {}
                if isinstance(meta, dict) and meta.get("bootstrap"):
                    continue
                if str(doc_id) in id_set:
                    deleted += 1
                    continue
                kept_ids.append(str(doc_id))
                kept_texts.append(getattr(doc, "page_content", "") or "")
                kept_metas.append(meta if isinstance(meta, dict) else {})
        else:
            payload = vs.get() or {}
            ids_all = payload.get("ids") or []
            docs_all = payload.get("documents") or []
            metas_all = payload.get("metadatas") or []
            n = min(len(ids_all), len(docs_all), len(metas_all))
            for i in range(n):
                meta = metas_all[i] or {}
                if isinstance(meta, dict) and meta.get("bootstrap"):
                    continue
                if str(ids_all[i]) in id_set:
                    deleted += 1
                    continue
                kept_ids.append(str(ids_all[i]))
                kept_texts.append(docs_all[i] or "")
                kept_metas.append(meta if isinstance(meta, dict) else {})

        if deleted <= 0:
            return 0

        self.vector_store = FAISS.from_texts(["bootstrap"], self.embeddings, metadatas=[{"bootstrap": True}])
        if kept_texts:
            try:
                self.vector_store.add_texts(kept_texts, metadatas=kept_metas, ids=kept_ids)
            except Exception:
                self.vector_store.add_texts(kept_texts, metadatas=kept_metas)
        self._save_to_disk()
        return deleted

    def retrieve_relevant(self, query: str, k: int = 5, mode: Optional[str] = None, thread_id: Optional[str] = None) -> List[Document]:
        if not self.use_faiss:
            return []
        if self.partition_enabled:
            scored: List[Tuple[Document, Optional[float]]] = []
            for _path, store in self._iter_vector_stores(mode, thread_id):
                scored.extend(self._search_store(store, query, k=max(k, 4)))
            if not scored:
                return []
            if any(score is not None for _doc, score in scored):
                scored.sort(key=lambda item: float(item[1] if item[1] is not None else 0.0))
            docs = [doc for doc, _score in scored]
            return docs[:k]
        if self.vector_store is None:
            return []
        results = self.vector_store.similarity_search(query, k=max(k, 8))
        results = [d for d in results if not (getattr(d, "metadata", {}) or {}).get("bootstrap")]
        results = results[:k]
        logger.debug(f"Retrieved {len(results)} relevant memories for query: {query[:50]}...")
        return results

    def get_conversation_context(self, query: str, k: int = 5, mode: Optional[str] = None, thread_id: Optional[str] = None) -> str:
        if not self.use_faiss:
            if not self.simple_memory:
                return ""
            matched = [
                m
                for m in self.simple_memory
                if self._mode_matches(m.get("mode"), mode)
                and self._thread_matches(m.get("thread_id"), thread_id)
            ]
            if not matched:
                return ""
            return "\n\n".join([m["text"] for m in matched[-k:]])
        
        docs = self.retrieve_relevant(query, k=max(k * 3, 8), mode=mode, thread_id=thread_id)
        if not docs:
            return ""

        context_parts = []
        for doc in docs:
            if doc.page_content.strip():
                mode_val = (getattr(doc, "metadata", {}) or {}).get("mode")
                if not self._mode_matches(mode_val, mode):
                    continue
                if not self._thread_matches((getattr(doc, "metadata", {}) or {}).get("thread_id"), thread_id):
                    continue
                context_parts.append(doc.page_content)

        if not context_parts:
            return ""

        return "\n\n".join(context_parts[-k:])

    def count_items(self, mode: Optional[str] = None, thread_id: Optional[str] = None) -> int:
        if mode is None or self._resolve_mode_filter(mode) is None:
            if thread_id is None:
                return self.memory_count
        if not self.use_faiss:
            return sum(
                1
                for m in self.simple_memory
                if self._mode_matches(m.get("mode"), mode)
                and self._thread_matches(m.get("thread_id"), thread_id)
            )

        count = 0
        if self.partition_enabled:
            for _path, store in self._iter_vector_stores(mode, thread_id):
                for _doc_id, _text, meta in self._iter_store_records(store):
                    if not self._mode_matches(meta.get("mode") if isinstance(meta, dict) else None, mode):
                        continue
                    if not self._thread_matches(meta.get("thread_id") if isinstance(meta, dict) else None, thread_id):
                        continue
                    count += 1
            return count

        vs = self.vector_store
        if vs is None:
            return 0
        store = getattr(vs, "docstore", None)
        d = getattr(store, "_dict", None) if store is not None else None

        if isinstance(d, dict):
            for _doc_id, doc in d.items():
                meta = getattr(doc, "metadata", {}) or {}
                if isinstance(meta, dict) and meta.get("bootstrap"):
                    continue
                if not self._mode_matches(meta.get("mode") if isinstance(meta, dict) else None, mode):
                    continue
                if not self._thread_matches(meta.get("thread_id") if isinstance(meta, dict) else None, thread_id):
                    continue
                count += 1
            return count

        payload = vs.get() or {}
        metas = payload.get("metadatas") or []
        for meta in metas:
            if isinstance(meta, dict) and meta.get("bootstrap"):
                continue
            if not self._mode_matches(meta.get("mode") if isinstance(meta, dict) else None, mode):
                continue
            if not self._thread_matches(meta.get("thread_id") if isinstance(meta, dict) else None, thread_id):
                continue
            count += 1
        return count

    def clear_memory(self) -> None:
        if self.use_faiss:
            if self.partition_enabled:
                for path, _store in list(self._iter_vector_stores(None, None)):
                    new_store = FAISS.from_texts(["bootstrap"], self.embeddings, metadatas=[{"bootstrap": True}])
                    self._save_vector_store(new_store, path)
                    self._vector_stores[str(path)] = new_store
            else:
                self.vector_store = FAISS.from_texts(["bootstrap"], self.embeddings, metadatas=[{"bootstrap": True}])
                self._save_to_disk()
        else:
            self.simple_memory = []
        logger.info("Memory cleared")

    def _save_to_disk(self) -> None:
        if self.use_faiss:
            try:
                if self.partition_enabled:
                    for path, store in self._iter_vector_stores(None, None):
                        self._save_vector_store(store, path)
                elif self.vector_store is not None:
                    self.vector_store.save_local(self.memory_path)
                    logger.debug("Memory saved to disk")
            except Exception as e:
                logger.error(f"Failed to save memory: {e}")

    @property
    def memory_count(self) -> int:
        if not self.use_faiss:
            return len(self.simple_memory)
        try:
            if self.partition_enabled:
                count = 0
                for _path, store in self._iter_vector_stores(None, None):
                    for _doc_id, _text, meta in self._iter_store_records(store):
                        if isinstance(meta, dict) and meta.get("bootstrap"):
                            continue
                        count += 1
                return count

            vs = self.vector_store
            if vs is None:
                return 0
            store = getattr(vs, "docstore", None)
            if store is not None:
                d = getattr(store, "_dict", None)
                if isinstance(d, dict):
                    count = 0
                    for _id, doc in d.items():
                        meta = getattr(doc, "metadata", {}) or {}
                        if isinstance(meta, dict) and meta.get("bootstrap"):
                            continue
                        count += 1
                    return count

            payload = vs.get() or {}
            metas = payload.get("metadatas") or []
            if metas:
                return sum(1 for m in metas if not (isinstance(m, dict) and m.get("bootstrap")))

            ids = getattr(vs, "index_to_docstore_id", None)
            if isinstance(ids, dict):
                return max(0, len(ids) - 1)

            return 0
        except Exception:
            return 0
