"""
API module for Echo Speak.
Provides FastAPI server for REST API access.
"""

import os
import sys
import base64
import json
import queue
import threading
import time
import uuid
import hmac
import hashlib
from datetime import datetime
from pathlib import Path
from io import BytesIO
from collections import deque, OrderedDict
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from fastapi import FastAPI, HTTPException, Query, Response, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from loguru import logger

import anyio

try:
    from croniter import croniter
except Exception:
    croniter = None

try:
    from langchain_core.callbacks.base import BaseCallbackHandler
except ImportError:
    from langchain.callbacks.base import BaseCallbackHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config, ModelProvider

LM_STUDIO_ONLY = True
LM_STUDIO_DEFAULT_URL = "http://localhost:1234"


_agent = None
_agent_pool: "OrderedDict[str, Any]" = OrderedDict()
_agent_pool_lock = threading.Lock()
_agent_pool_max = 8
_vision_manager = None
_runtime_provider: Optional[ModelProvider] = None

_metrics_lock = threading.Lock()
_metrics = {
    "requests": 0,
    "errors": 0,
    "tool_calls": 0,
    "tool_errors": 0,
}
_tool_latency_ms: deque[float] = deque(maxlen=200)


def _force_lmstudio_config() -> None:
    config.use_local_models = True
    config.local.provider = ModelProvider.LM_STUDIO
    if not (config.local.base_url or "").strip():
        config.local.base_url = LM_STUDIO_DEFAULT_URL


def _normalize_thread_id(thread_id: Optional[str]) -> str:
    if thread_id is None:
        return "default"
    val = str(thread_id).strip()
    return val or "default"


def get_agent(thread_id: Optional[str] = None):
    """Get or create the agent instance.

    When MULTI_AGENT_ENABLED=true, agents are pooled per thread_id.
    """
    global _agent
    global _runtime_provider
    from agent.core import EchoSpeakAgent

    if not bool(getattr(config, "multi_agent_enabled", True)):
        if _agent is None:
            if LM_STUDIO_ONLY:
                _force_lmstudio_config()
                provider = ModelProvider.LM_STUDIO
            elif _runtime_provider is not None:
                provider = _runtime_provider
            else:
                provider = config.local.provider if config.use_local_models else ModelProvider.OPENAI
            _agent = EchoSpeakAgent(llm_provider=provider)
        return _agent

    key = _normalize_thread_id(thread_id)
    with _agent_pool_lock:
        existing = _agent_pool.pop(key, None)
        if existing is not None:
            _agent_pool[key] = existing
            return existing

        if LM_STUDIO_ONLY:
            _force_lmstudio_config()
            provider = ModelProvider.LM_STUDIO
        elif _runtime_provider is not None:
            provider = _runtime_provider
        else:
            provider = config.local.provider if config.use_local_models else ModelProvider.OPENAI

        agent = EchoSpeakAgent(llm_provider=provider)
        _agent_pool[key] = agent
        while len(_agent_pool) > _agent_pool_max:
            _agent_pool.popitem(last=False)
        return agent


def get_document_store():
    agent = get_agent()
    if not bool(getattr(config, "document_rag_enabled", False)):
        return None
    return getattr(agent, "document_store", None)


def _load_webhook_secret() -> str:
    secret = str(getattr(config, "webhook_secret", "") or "").strip()
    if secret:
        return secret
    path_val = str(getattr(config, "webhook_secret_path", "") or "").strip()
    if not path_val:
        return ""
    path = Path(path_val).expanduser()
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return ""


def _parse_signature(header_val: str) -> Optional[str]:
    raw = str(header_val or "").strip()
    if not raw:
        return None
    if raw.lower().startswith("sha256="):
        raw = raw.split("=", 1)[1].strip()
    raw = raw.strip()
    if not raw:
        return None
    return raw


def _verify_webhook_signature(secret: str, body: bytes, signature_header: Optional[str]) -> bool:
    if not secret:
        return False
    sig = _parse_signature(signature_header or "")
    if not sig:
        return False
    expected = hmac.new(secret.encode("utf-8"), body or b"", hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


_cron_state_lock = threading.Lock()


def _load_cron_state() -> dict:
    path_val = str(getattr(config, "cron_state_path", "") or "").strip()
    if not path_val:
        return {}
    path = Path(path_val).expanduser()
    try:
        if not path.exists():
            return {}
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cron_state(state: dict) -> None:
    path_val = str(getattr(config, "cron_state_path", "") or "").strip()
    if not path_val:
        return
    path = Path(path_val).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return


def get_vision_manager():
    """Get or create the vision manager instance."""
    global _vision_manager
    if _vision_manager is None:
        from io_module.vision import create_vision_manager
        _vision_manager = create_vision_manager()
    return _vision_manager


def _metric_inc(key: str, amount: int = 1) -> None:
    with _metrics_lock:
        if key not in _metrics:
            _metrics[key] = 0
        _metrics[key] += amount


def _record_tool_latency(ms: float) -> None:
    with _metrics_lock:
        _tool_latency_ms.append(ms)


class _StreamingHandler(BaseCallbackHandler):
    def __init__(self, q: queue.Queue, request_id: str):
        self._q = q
        self._request_id = request_id
        self._tool_run_map: dict = {}
        self._tool_started_at: dict = {}

    def on_tool_start(self, serialized: dict, input_str: str, run_id: str, parent_run_id: Optional[str] = None, **kwargs):
        tool_name = (serialized or {}).get("name") or (serialized or {}).get("id") or "tool"
        call_id = str(run_id)
        self._tool_run_map[call_id] = tool_name
        self._tool_started_at[call_id] = time.perf_counter()
        _metric_inc("tool_calls", 1)

        inp = input_str if isinstance(input_str, str) else str(input_str)
        inp = " ".join((inp or "").split())
        if len(inp) > 600:
            inp = inp[:600] + "…"
        self._q.put(
            {
                "type": "tool_start",
                "id": call_id,
                "name": tool_name,
                "input": inp,
                "at": time.time(),
                "request_id": self._request_id,
            }
        )

    def on_tool_end(self, output: str, run_id: str, parent_run_id: Optional[str] = None, **kwargs):
        call_id = str(run_id)
        out = output if isinstance(output, str) else str(output)
        tool_name = self._tool_run_map.get(call_id, "")
        max_len = 8000 if tool_name == "web_search" else 800
        if len(out) > max_len:
            out = out[:max_len] + "…"
        started = self._tool_started_at.pop(call_id, None)
        if started is not None:
            _record_tool_latency((time.perf_counter() - started) * 1000.0)
        self._q.put({"type": "tool_end", "id": call_id, "name": tool_name, "output": out, "at": time.time(), "request_id": self._request_id})

    def on_tool_error(self, error: BaseException, run_id: str, parent_run_id: Optional[str] = None, **kwargs):
        call_id = str(run_id)
        _metric_inc("tool_errors", 1)
        started = self._tool_started_at.pop(call_id, None)
        if started is not None:
            _record_tool_latency((time.perf_counter() - started) * 1000.0)
        tool_name = self._tool_run_map.get(call_id, "")
        self._q.put({"type": "tool_error", "id": call_id, "name": tool_name, "error": str(error), "at": time.time(), "request_id": self._request_id})


def _start_agent_thread(
    *,
    agent,
    message: str,
    include_memory: bool,
    thread_id: Optional[str],
    request_id: str,
    q: queue.Queue,
) -> None:
    def run_agent():
        try:
            handler = _StreamingHandler(q, request_id)
            response, success = agent.process_query(
                message,
                include_memory=include_memory,
                callbacks=[handler],
                thread_id=thread_id,
            )
            doc_sources = agent.get_last_doc_sources() if include_memory else []
            spoken_text = ""
            try:
                spoken_text = str(agent.get_last_tts_text() or "")
            except Exception:
                spoken_text = ""
            q.put({"type": "memory_saved", "memory_count": agent.memory.memory_count, "at": time.time(), "request_id": request_id})
            q.put(
                {
                    "type": "final",
                    "response": response,
                    "success": success,
                    "memory_count": agent.memory.memory_count,
                    "doc_sources": doc_sources,
                    "spoken_text": spoken_text,
                    "request_id": request_id,
                    "at": time.time(),
                }
            )
        except Exception as e:
            _metric_inc("errors", 1)
            q.put({"type": "error", "message": str(e), "at": time.time(), "request_id": request_id})
        finally:
            q.put(None)

    threading.Thread(target=run_agent, daemon=True).start()


def _extract_text_from_upload(filename: str, content_type: Optional[str], data: bytes) -> str:
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    if name.endswith(".pdf") or ctype == "application/pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as exc:
            raise HTTPException(status_code=503, detail="pypdf is required to parse PDF files") from exc
        try:
            reader = PdfReader(BytesIO(data))
            parts = []
            for page in reader.pages:
                text = page.extract_text() or ""
                if text.strip():
                    parts.append(text.strip())
            return "\n\n".join(parts).strip()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {exc}") from exc

    try:
        return data.decode("utf-8", errors="ignore").strip()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unsupported text encoding: {exc}") from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan."""
    logger.info("Starting Echo Speak API server...")
    if bool(getattr(config, "use_pocket_tts", False)):
        def _warm_pocket_tts():
            try:
                from io_module.pocket_tts_engine import get_pocket_tts_engine

                engine = get_pocket_tts_engine()
                engine.warmup()
                logger.info("Pocket-TTS warmup complete")
            except Exception as exc:
                logger.warning(f"Pocket-TTS warmup skipped: {exc}")

        threading.Thread(target=_warm_pocket_tts, daemon=True).start()
    yield
    logger.info("Shutting down Echo Speak API server...")


app = FastAPI(
    title="Echo Speak API",
    description="Voice AI Assistant API with support for local models",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    """Request model for query endpoint."""
    message: str = Field(..., description="User message to process")
    include_memory: bool = Field(default=True, description="Include conversation memory")
    thread_id: Optional[str] = Field(default=None, description="Conversation thread id for LangGraph persistence")


class QueryResponse(BaseModel):
    """Response model for query endpoint."""
    response: str
    success: bool
    memory_count: int
    request_id: Optional[str] = None
    doc_sources: Optional[list] = None


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    agent = get_agent(request.thread_id)
    request_id = str(uuid.uuid4())
    try:
        response, success = agent.process_query(
            request.message,
            include_memory=request.include_memory,
            callbacks=None,
            thread_id=request.thread_id,
        )
        doc_sources = agent.get_last_doc_sources() if request.include_memory else []
        return QueryResponse(
            response=response,
            success=bool(success),
            memory_count=agent.memory.memory_count,
            request_id=request_id,
            doc_sources=doc_sources,
        )
    except Exception as exc:
        _metric_inc("errors", 1)
        raise HTTPException(status_code=500, detail=str(exc))


class DoctorResponse(BaseModel):
    ok: bool
    report: Dict[str, Any]
    text: str


class SessionsResponse(BaseModel):
    multi_agent_enabled: bool
    pool_max: int
    pool_size: int
    thread_ids: List[str]
    lm_studio_only: bool
    runtime_provider: Optional[str] = None


class CronTickRequest(BaseModel):
    job_id: str = Field(..., description="Job identifier")
    cron: str = Field(..., description="Cron schedule (5-field)")
    message: str = Field(..., description="Message to run when due")
    thread_id: Optional[str] = Field(default=None, description="Session/thread id")
    include_memory: bool = Field(default=True, description="Include memory")


class ScreenAnalysisResponse(BaseModel):
    """Response model for screen analysis."""
    text: str
    text_length: int
    has_text: bool
    image_size: dict


class ScreenCaptureResponse(BaseModel):
    """Response model for screen capture."""
    success: bool
    image_base64: Optional[str] = None
    error: Optional[str] = None


class HistoryResponse(BaseModel):
    """Response model for conversation history."""
    history: list
    count: int


class MemoryItem(BaseModel):
    id: str
    text: str
    timestamp: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class MemoryListResponse(BaseModel):
    items: List[MemoryItem]
    count: int
    use_faiss: bool


class MemoryDeleteRequest(BaseModel):
    ids: List[str]


class DocumentItem(BaseModel):
    id: str
    filename: str
    chunks: int
    source: Optional[str] = None
    mime: Optional[str] = None
    timestamp: Optional[str] = None


class DocumentListResponse(BaseModel):
    items: List[DocumentItem]
    count: int
    enabled: bool


class DocumentDeleteRequest(BaseModel):
    ids: List[str]


class ProviderInfoResponse(BaseModel):
    """Response model for provider information."""
    provider: str
    model: str
    local: bool
    base_url: Optional[str] = None
    available_providers: list


class SwitchProviderRequest(BaseModel):
    """Request model for switching provider."""
    provider: str = Field(..., description="Provider ID (openai, ollama, lmstudio, localai, llama_cpp, vllm)")
    model: Optional[str] = Field(default=None, description="Model name (or path for llama.cpp)")
    base_url: Optional[str] = Field(default=None, description="Base URL for local servers (Ollama/LM Studio/LocalAI/vLLM)")
    openai_model: Optional[str] = Field(default=None, description="OpenAI model override when provider=openai")


class TTSRequest(BaseModel):
    text: str = Field(..., description="Text to synthesize")
    voice: Optional[str] = Field(default=None, description="Voice alias or prompt (e.g., alba, or hf://...wav)")
    voice_prompt: Optional[str] = Field(default=None, description="Voice cloning prompt (local path, http(s) URL, or hf://)")


class STTInfoResponse(BaseModel):
    enabled: bool
    model: str
    device: str
    compute_type: str


class STTResponse(BaseModel):
    text: str
    language: Optional[str] = None
    duration: Optional[float] = None


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Echo Speak API",
        "version": "1.0.0",
        "status": "running",
        "local_models_enabled": config.use_local_models
    }


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    """
    Process a user query through the agent.

    Args:
        request: Query request with message.

    Returns:
        Agent response.
    """
    request_id = str(uuid.uuid4())
    _metric_inc("requests", 1)
    try:
        agent = get_agent(request.thread_id)
        response, success = agent.process_query(
            request.message,
            include_memory=request.include_memory,
            thread_id=request.thread_id,
        )
        doc_sources = agent.get_last_doc_sources() if request.include_memory else []

        return QueryResponse(
            response=response,
            success=success,
            memory_count=agent.memory.memory_count,
            request_id=request_id,
            doc_sources=doc_sources,
        )
    except Exception as e:
        _metric_inc("errors", 1)
        logger.error(f"Query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sessions", response_model=SessionsResponse)
async def list_sessions():
    multi_agent_enabled = bool(getattr(config, "multi_agent_enabled", True))
    runtime_provider = _runtime_provider.value if _runtime_provider is not None else None

    if not multi_agent_enabled:
        return SessionsResponse(
            multi_agent_enabled=False,
            pool_max=_agent_pool_max,
            pool_size=1 if _agent is not None else 0,
            thread_ids=["default"],
            lm_studio_only=bool(LM_STUDIO_ONLY),
            runtime_provider=runtime_provider,
        )

    with _agent_pool_lock:
        thread_ids = list(_agent_pool.keys())

    return SessionsResponse(
        multi_agent_enabled=True,
        pool_max=_agent_pool_max,
        pool_size=len(thread_ids),
        thread_ids=thread_ids,
        lm_studio_only=bool(LM_STUDIO_ONLY),
        runtime_provider=runtime_provider,
    )


@app.get("/agents", response_model=SessionsResponse)
async def list_agents():
    return await list_sessions()


@app.get("/documents", response_model=DocumentListResponse)
async def list_documents():
    store = get_document_store()
    if store is None:
        return DocumentListResponse(items=[], count=0, enabled=False)
    items = store.list_documents()
    return DocumentListResponse(items=[DocumentItem(**i) for i in items], count=len(items), enabled=True)


@app.post("/documents/upload", response_model=DocumentItem)
async def upload_document(file: UploadFile = File(...), source: Optional[str] = None):
    store = get_document_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Document RAG is disabled")
    try:
        data = await file.read()
        max_bytes = int(getattr(config, "doc_upload_max_mb", 25) or 25) * 1024 * 1024
        if len(data) > max_bytes:
            raise HTTPException(status_code=413, detail="Upload too large")
        text = _extract_text_from_upload(file.filename or "document", file.content_type, data)
        meta = store.add_document(file.filename or "document", text, source=source or "", mime=file.content_type or "")
        return DocumentItem(**meta)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Document upload failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/documents/delete")
async def delete_documents(request: DocumentDeleteRequest):
    store = get_document_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Document RAG is disabled")
    deleted = store.delete_documents(request.ids)
    return {"success": True, "deleted": deleted}


@app.post("/documents/clear")
async def clear_documents():
    store = get_document_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Document RAG is disabled")
    store.clear()
    return {"success": True}


@app.post("/query/stream")
async def query_stream(request: QueryRequest):
    agent = get_agent(request.thread_id)
    q: queue.Queue = queue.Queue()
    request_id = str(uuid.uuid4())
    _metric_inc("requests", 1)

    _start_agent_thread(
        agent=agent,
        message=request.message,
        include_memory=request.include_memory,
        thread_id=request.thread_id,
        request_id=request_id,
        q=q,
    )

    async def gen():
        while True:
            item = await anyio.to_thread.run_sync(q.get)
            if item is None:
                break
            yield (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.websocket("/gateway/ws")
async def gateway_ws(websocket: WebSocket):
    await websocket.accept()
    session_id = str(uuid.uuid4())
    await websocket.send_json({"type": "gateway_ready", "session_id": session_id, "at": time.time()})

    while True:
        try:
            payload = await websocket.receive_json()
        except WebSocketDisconnect:
            break
        except Exception as exc:
            await websocket.send_json({"type": "error", "message": f"Invalid message: {exc}", "at": time.time()})
            continue

        if not isinstance(payload, dict):
            await websocket.send_json({"type": "error", "message": "Message must be a JSON object.", "at": time.time()})
            continue

        msg_type = str(payload.get("type") or "").strip().lower()
        if msg_type == "ping":
            await websocket.send_json({"type": "pong", "at": time.time()})
            continue
        if msg_type != "query":
            await websocket.send_json({"type": "error", "message": f"Unknown message type: {payload.get('type')}", "at": time.time()})
            continue

        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            await websocket.send_json({"type": "error", "message": "Missing 'message' field for query.", "at": time.time()})
            continue

        include_memory = payload.get("include_memory", True)
        if isinstance(include_memory, str):
            include_memory = include_memory.strip().lower() not in {"false", "0", "no", "off"}
        elif include_memory is None:
            include_memory = True
        else:
            include_memory = bool(include_memory)

        thread_id_val = payload.get("thread_id")
        thread_id = str(thread_id_val).strip() if thread_id_val is not None else None
        if thread_id == "":
            thread_id = None

        request_id = payload.get("request_id") or str(uuid.uuid4())
        request_id = str(request_id)

        agent = get_agent(thread_id)

        q: queue.Queue = queue.Queue()
        _metric_inc("requests", 1)
        _start_agent_thread(
            agent=agent,
            message=message,
            include_memory=include_memory,
            thread_id=thread_id,
            request_id=request_id,
            q=q,
        )

        while True:
            item = await anyio.to_thread.run_sync(q.get)
            if item is None:
                break
            try:
                await websocket.send_json(item)
            except WebSocketDisconnect:
                return
            except Exception as exc:
                logger.warning(f"Gateway WS send failed: {exc}")
                break


@app.get("/doctor", response_model=DoctorResponse)
async def doctor(thread_id: Optional[str] = Query(default=None)):
    try:
        agent = get_agent(thread_id)
        report = agent.get_doctor_report()
        text = ""
        try:
            text = str(agent.format_doctor_report(report) or "")
        except Exception:
            text = ""
        return DoctorResponse(ok=bool(report.get("ok")), report=report, text=text)
    except Exception as e:
        logger.error(f"Doctor error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/trigger/cron")
async def trigger_cron(request: CronTickRequest):
    if not bool(getattr(config, "cron_enabled", False)):
        raise HTTPException(status_code=403, detail="Cron triggers disabled (CRON_ENABLED=false)")
    if croniter is None:
        raise HTTPException(status_code=503, detail="croniter is not available")

    job_id = (request.job_id or "").strip() or "default"
    cron_expr = (request.cron or "").strip()
    if not cron_expr:
        raise HTTPException(status_code=422, detail="Missing cron expression")

    now = datetime.utcnow()
    now_ts = now.timestamp()
    with _cron_state_lock:
        state = _load_cron_state()
        jobs = state.get("jobs")
        if not isinstance(jobs, dict):
            jobs = {}
        last_run = jobs.get(job_id)

        due = False
        next_run = None
        if last_run is None:
            due = True
            try:
                next_run = croniter(cron_expr, now).get_next(datetime)
            except Exception:
                next_run = None
        else:
            try:
                base = datetime.utcfromtimestamp(float(last_run))
                next_run = croniter(cron_expr, base).get_next(datetime)
                due = now >= next_run
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid cron expression: {exc}")

        if not due:
            return {
                "ran": False,
                "job_id": job_id,
                "next_run_at": next_run.isoformat() if next_run else None,
                "last_run_at": datetime.utcfromtimestamp(float(last_run)).isoformat() if last_run is not None else None,
            }

        agent = get_agent(request.thread_id)
        response, success = agent.process_query(
            request.message,
            include_memory=request.include_memory,
            thread_id=request.thread_id,
        )
        jobs[job_id] = now_ts
        state["jobs"] = jobs
        _save_cron_state(state)

    return {
        "ran": True,
        "job_id": job_id,
        "ran_at": now.isoformat(),
        "success": bool(success),
        "response": response,
    }


@app.post("/trigger/webhook")
async def trigger_webhook(req: Request):
    if not bool(getattr(config, "webhook_enabled", False)):
        raise HTTPException(status_code=403, detail="Webhook triggers disabled (WEBHOOK_ENABLED=false)")

    body = await req.body()
    secret = _load_webhook_secret()
    if not secret:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    sig = req.headers.get("x-echospeak-signature") or req.headers.get("x-signature") or ""
    if not _verify_webhook_signature(secret, body, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    message = (str(payload.get("message") or "").strip())
    if not message:
        raise HTTPException(status_code=422, detail="Missing 'message'")
    thread_id_val = payload.get("thread_id")
    thread_id = str(thread_id_val).strip() if thread_id_val is not None else None
    if thread_id == "":
        thread_id = None
    include_memory = payload.get("include_memory", True)
    if isinstance(include_memory, str):
        include_memory = include_memory.strip().lower() not in {"false", "0", "no", "off"}
    else:
        include_memory = bool(include_memory)

    agent = get_agent(thread_id)
    response, success = agent.process_query(
        message,
        include_memory=include_memory,
        thread_id=thread_id,
    )

    return {"success": bool(success), "response": response}


@app.post("/tts")
async def tts(req: Request):
    """Synthesize speech audio and return a WAV file."""
    try:
        if not bool(getattr(config, "use_pocket_tts", False)):
            raise HTTPException(status_code=503, detail="Pocket-TTS disabled (USE_POCKET_TTS=false)")

        payload = {}
        try:
            payload = await req.json()
        except Exception:
            try:
                raw = (await req.body()).decode("utf-8", errors="ignore").strip()
            except Exception:
                raw = ""
            payload = {"text": raw}

        if not isinstance(payload, dict):
            payload = {"text": str(payload)}

        text_val = payload.get("text")
        if not text_val:
            text_val = payload.get("message")
        if not text_val:
            text_val = payload.get("input")

        text = (str(text_val) if text_val is not None else "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="Missing 'text' field for /tts")

        client = (req.headers.get("x-echospeak-client") or "").strip()
        if client:
            logger.info(f"TTS request from client={client} chars={len(text)}")

        voice_val = payload.get("voice")
        voice = (str(voice_val).strip() if isinstance(voice_val, str) and voice_val.strip() else None)

        vp_val = payload.get("voice_prompt")
        if not vp_val:
            vp_val = payload.get("voicePrompt")
        voice_prompt = (str(vp_val).strip() if isinstance(vp_val, str) and vp_val.strip() else None)

        from io_module.pocket_tts_engine import get_pocket_tts_engine

        engine = get_pocket_tts_engine()
        wav_bytes, meta = engine.synthesize_wav(
            text,
            voice=voice,
            voice_prompt=voice_prompt,
        )

        headers = {
            "X-TTS-Engine": str((meta or {}).get("engine") or "pocket_tts"),
            "X-TTS-Sample-Rate": str((meta or {}).get("sample_rate") or engine.sample_rate),
            "X-TTS-Voice": str((meta or {}).get("voice_id") or ""),
        }
        return StreamingResponse(BytesIO(wav_bytes), media_type="audio/wav", headers=headers)
    except HTTPException:
        raise
    except ValueError as e:
        msg = str(e)
        logger.error(f"TTS error: {msg}")
        if "voice cloning" in msg and "accept the terms" in msg:
            raise HTTPException(status_code=403, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except ImportError as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.options("/tts")
async def tts_options() -> Response:
    return Response(status_code=200)


@app.get("/stt/info", response_model=STTInfoResponse)
async def stt_info():
    return STTInfoResponse(
        enabled=bool(getattr(config, "local_stt_enabled", False)),
        model=str(getattr(config, "local_stt_model", "base")),
        device=str(getattr(config, "local_stt_device", "cpu")),
        compute_type=str(getattr(config, "local_stt_compute_type", "int8")),
    )


@app.post("/stt", response_model=STTResponse)
async def stt(audio: UploadFile = File(...)):
    if not bool(getattr(config, "local_stt_enabled", False)):
        raise HTTPException(status_code=503, detail="Local STT disabled (LOCAL_STT_ENABLED=false)")
    try:
        data = await audio.read()
        from io_module.stt_engine import transcribe_bytes

        text, meta = transcribe_bytes(data)
        return STTResponse(
            text=text or "",
            language=(meta or {}).get("language"),
            duration=(meta or {}).get("duration"),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"STT error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/history", response_model=HistoryResponse)
async def get_history():
    """
    Get conversation history.

    Returns:
        List of conversation messages.
    """
    try:
        agent = get_agent()
        history = agent.get_history()

        return HistoryResponse(
            history=[str(h) for h in history],
            count=len(history)
        )
    except Exception as e:
        logger.error(f"History error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/history/clear")
async def clear_history():
    """Clear conversation history."""
    try:
        agent = get_agent()
        agent.clear_conversation()
        return {"success": True, "message": "Conversation history cleared"}
    except Exception as e:
        logger.error(f"Clear history error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memory", response_model=MemoryListResponse)
async def list_memory(offset: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=500)):
    try:
        agent = get_agent()
        items = agent.memory.list_items(offset=offset, limit=limit)
        return MemoryListResponse(
            items=[MemoryItem(**(i or {})) for i in items],
            count=agent.memory.memory_count,
            use_faiss=bool(getattr(agent.memory, "use_faiss", False)),
        )
    except Exception as e:
        logger.error(f"List memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/delete")
async def delete_memory(request: MemoryDeleteRequest):
    try:
        agent = get_agent()
        deleted = agent.memory.delete_items(request.ids)
        return {"success": True, "deleted": deleted, "memory_count": agent.memory.memory_count}
    except Exception as e:
        logger.error(f"Delete memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/clear")
async def clear_memory():
    try:
        agent = get_agent()
        agent.memory.clear_memory()
        return {"success": True, "memory_count": agent.memory.memory_count}
    except Exception as e:
        logger.error(f"Clear memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/provider", response_model=ProviderInfoResponse)
async def get_provider_info():
    """
    Get current provider information.

    Returns:
        Current provider details and available providers.
    """
    from agent.core import list_available_providers

    agent = get_agent()
    providers = list_available_providers()
    if LM_STUDIO_ONLY:
        _force_lmstudio_config()
        providers = [p for p in providers if p.get("id") == ModelProvider.LM_STUDIO.value]
        return ProviderInfoResponse(
            provider=ModelProvider.LM_STUDIO.value,
            model=config.local.model_name,
            local=True,
            base_url=config.local.base_url or LM_STUDIO_DEFAULT_URL,
            available_providers=providers,
        )

    is_local = agent.llm_provider != ModelProvider.OPENAI
    model = config.openai.model if agent.llm_provider == ModelProvider.OPENAI else config.local.model_name
    base_url = None if agent.llm_provider in (ModelProvider.OPENAI, ModelProvider.LLAMA_CPP) else config.local.base_url

    return ProviderInfoResponse(
        provider=agent.llm_provider.value,
        model=model,
        local=is_local,
        base_url=base_url,
        available_providers=providers
    )


@app.post("/provider/switch")
async def switch_provider(request: SwitchProviderRequest):
    """
    Switch to a different model provider.

    Args:
        request: Switch provider request.

    Returns:
        Success message.
    """
    try:
        if LM_STUDIO_ONLY:
            raise HTTPException(status_code=403, detail="Provider switching is disabled (LM Studio only)")
        provider = ModelProvider(request.provider)
        global _agent, _runtime_provider
        _runtime_provider = provider

        if provider == ModelProvider.OPENAI:
            if request.openai_model:
                config.openai.model = request.openai_model
            config.use_local_models = False
        else:
            config.local.provider = provider
            if request.model:
                config.local.model_name = request.model
            if request.base_url:
                config.local.base_url = request.base_url
            else:
                if provider == ModelProvider.OLLAMA:
                    config.local.base_url = "http://localhost:11434"
                elif provider == ModelProvider.LM_STUDIO:
                    config.local.base_url = "http://localhost:1234"
                elif provider == ModelProvider.LOCALAI:
                    config.local.base_url = "http://localhost:8080"
                elif provider == ModelProvider.VLLM:
                    config.local.base_url = "http://localhost:8000"
            config.use_local_models = True

        _agent = None
        with _agent_pool_lock:
            _agent_pool.clear()

        return {
            "success": True,
            "message": f"Switched to {provider.value}",
            "provider": provider.value
        }
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid provider: {request.provider}")
    except Exception as e:
        logger.error(f"Switch provider error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/provider/models")
async def list_provider_models(provider: Optional[str] = Query(default=None)):
    p = None
    if LM_STUDIO_ONLY:
        p = ModelProvider.LM_STUDIO
    elif provider:
        try:
            p = ModelProvider(provider)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid provider: {provider}")
    else:
        p = get_agent().llm_provider

    if p == ModelProvider.OLLAMA:
        try:
            import requests

            base = (config.local.base_url or "").rstrip("/")
            if config.local.provider != ModelProvider.OLLAMA:
                base = "http://localhost:11434"
            if not base:
                base = "http://localhost:11434"
            resp = requests.get(f"{base}/api/tags", timeout=4)
            resp.raise_for_status()
            data = resp.json() or {}
            models = set()
            for m in data.get("models") or []:
                name = m.get("name")
                if name:
                    models.add(name)
            return {"provider": p.value, "models": sorted(models)}
        except Exception as e:
            logger.warning(f"Failed to list Ollama models: {e}")
            return {"provider": p.value, "models": []}

    if p in (ModelProvider.LM_STUDIO, ModelProvider.LOCALAI, ModelProvider.VLLM):
        try:
            import requests

            base = (config.local.base_url or "").rstrip("/")
            if not base:
                if p == ModelProvider.LM_STUDIO:
                    base = "http://localhost:1234"
                elif p == ModelProvider.LOCALAI:
                    base = "http://localhost:8080"
                elif p == ModelProvider.VLLM:
                    base = "http://localhost:8000"
            if base.endswith("/v1"):
                url = f"{base}/models"
            else:
                url = f"{base}/v1/models"

            resp = requests.get(url, timeout=4)
            resp.raise_for_status()
            data = resp.json() or {}
            models = []
            for m in data.get("data") or []:
                model_id = m.get("id")
                if model_id:
                    models.append(model_id)
            return {"provider": p.value, "models": sorted(set(models))}
        except Exception as e:
            logger.warning(f"Failed to list {p.value} models: {e}")
            return {"provider": p.value, "models": []}

    return {"provider": p.value, "models": []}


@app.post("/vision/analyze", response_model=ScreenAnalysisResponse)
async def analyze_screen():
    """
    Capture screen and perform OCR analysis.

    Returns:
        Analysis results with extracted text.
    """
    try:
        vision = get_vision_manager()
        result = vision.capture_and_analyze()

        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])

        return ScreenAnalysisResponse(
            text=result.get("text", ""),
            text_length=result.get("text_length", 0),
            has_text=result.get("has_text", False),
            image_size=result.get("image_size", {})
        )
    except Exception as e:
        logger.error(f"Screen analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/vision/capture", response_model=ScreenCaptureResponse)
async def capture_screen():
    """
    Capture screen and return as base64 encoded image.

    Returns:
        Base64 encoded image.
    """
    try:
        import cv2
        from PIL import Image

        vision = get_vision_manager()
        image = vision.capture_and_analyze()

        if "error" in image:
            return ScreenCaptureResponse(success=False, error=image["error"])

        import numpy as np
        from io import BytesIO
        import base64

        img_array = np.array(vision.last_capture)
        img_rgb = cv2.cvtColor(img_array, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)

        buffer = BytesIO()
        pil_img.save(buffer, format="PNG")
        img_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        return ScreenCaptureResponse(
            success=True,
            image_base64=f"data:image/png;base64,{img_base64}"
        )
    except Exception as e:
        logger.error(f"Capture error: {e}")
        return ScreenCaptureResponse(success=False, error=str(e))


@app.get("/vision/info")
async def get_screen_info():
    """
    Get screen/monitor information.

    Returns:
        Screen information.
    """
    try:
        vision = get_vision_manager()
        return vision.get_screen_info()
    except Exception as e:
        logger.error(f"Screen info error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/metrics")
async def metrics():
    with _metrics_lock:
        counts = dict(_metrics)
        samples = list(_tool_latency_ms)

    stats = {"count": len(samples)}
    if samples:
        samples.sort()
        n = len(samples)
        avg = sum(samples) / max(1, n)
        def pick(p: float) -> float:
            idx = int(max(0, min(n - 1, round((n - 1) * p))))
            return float(samples[idx])

        stats.update(
            {
                "avg_ms": round(avg, 2),
                "p50_ms": round(pick(0.50), 2),
                "p90_ms": round(pick(0.90), 2),
                "p99_ms": round(pick(0.99), 2),
            }
        )
    return {"requests": counts.get("requests", 0), "errors": counts.get("errors", 0), "tool_calls": counts.get("tool_calls", 0), "tool_errors": counts.get("tool_errors", 0), "tool_latency_ms": stats}


def start_server(host: str = None, port: int = None):
    """
    Start the FastAPI server.

    Args:
        host: Host to bind to.
        port: Port to listen on.
    """
    import uvicorn
    host = host or config.api.host
    port = port or config.api.port
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    start_server()
