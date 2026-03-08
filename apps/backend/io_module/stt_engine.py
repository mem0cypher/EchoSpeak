from typing import Dict, Tuple


_REMOVED_MESSAGE = "Local STT has been removed. Use browser speech recognition instead."


def get_stt_engine():
    raise RuntimeError(_REMOVED_MESSAGE)


def transcribe_bytes(audio_bytes: bytes) -> Tuple[str, Dict[str, object]]:
    raise RuntimeError(_REMOVED_MESSAGE)
