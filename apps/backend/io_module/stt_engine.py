"""Local speech-to-text (STT) engine wrapper."""

from __future__ import annotations

import tempfile
import threading
from typing import Any, Dict, Tuple

from loguru import logger

from config import config

_engine_lock = threading.Lock()
_engine: Any = None


def get_stt_engine():
    if not bool(getattr(config, "local_stt_enabled", False)):
        raise RuntimeError("Local STT disabled (LOCAL_STT_ENABLED=false)")
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except Exception as exc:
            raise ImportError("faster-whisper is required for local STT") from exc
        model_name = getattr(config, "local_stt_model", "base") or "base"
        device = getattr(config, "local_stt_device", "cpu") or "cpu"
        compute_type = getattr(config, "local_stt_compute_type", "int8") or "int8"
        logger.info(f"Loading local STT model={model_name} device={device} compute_type={compute_type}")
        _engine = WhisperModel(model_name, device=device, compute_type=compute_type)
        return _engine


def transcribe_bytes(audio_bytes: bytes) -> Tuple[str, Dict[str, Any]]:
    engine = get_stt_engine()
    if not audio_bytes:
        raise ValueError("Missing audio data")

    suffix = ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        tmp_path = tmp.name

    segments, info = engine.transcribe(tmp_path, beam_size=5)
    text_parts = []
    for seg in segments:
        txt = (seg.text or "").strip()
        if txt:
            text_parts.append(txt)

    text = " ".join(text_parts).strip()
    meta = {
        "language": getattr(info, "language", None),
        "duration": getattr(info, "duration", None),
    }
    return text, meta
