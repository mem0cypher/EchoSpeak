from typing import Dict, Optional, Tuple


_REMOVED_MESSAGE = "Pocket-TTS has been removed. Use browser speech playback instead."


class PocketTTSEngine:
    def __init__(self) -> None:
        raise RuntimeError(_REMOVED_MESSAGE)

    def warmup(self, voice: Optional[str] = None, voice_prompt: Optional[str] = None) -> None:
        raise RuntimeError(_REMOVED_MESSAGE)

    @property
    def sample_rate(self) -> int:
        raise RuntimeError(_REMOVED_MESSAGE)

    def synthesize_wav(self, text: str, voice: Optional[str] = None, voice_prompt: Optional[str] = None) -> Tuple[bytes, Dict[str, object]]:
        raise RuntimeError(_REMOVED_MESSAGE)


def get_pocket_tts_engine() -> PocketTTSEngine:
    raise RuntimeError(_REMOVED_MESSAGE)
