import threading
import wave
from io import BytesIO
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from config import config


class PocketTTSEngine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model: Any = None
        self._sample_rate: int = 24000
        self._voice_state_cache: Dict[str, Any] = {}

    def _resolve_voice_prompt_ref(self, voice_id: str) -> str:
        v = (voice_id or "").strip()
        if not v:
            return v

        if v.lower().startswith("hf://"):
            spec = v[5:]
            parts = spec.split("/", 2)
            if len(parts) < 3:
                raise RuntimeError(f"Invalid hf:// voice prompt reference: {voice_id}")
            repo_id = f"{parts[0]}/{parts[1]}"
            filename = parts[2]
            try:
                from huggingface_hub import hf_hub_download  # type: ignore
            except Exception as exc:
                raise ImportError("huggingface_hub is required to download hf:// voice prompts") from exc

            try:
                return hf_hub_download(repo_id=repo_id, filename=filename)
            except Exception:
                return hf_hub_download(repo_id=repo_id, filename=filename, repo_type="dataset")

        return v

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return

        with self._lock:
            if self._model is not None:
                return

            try:
                from pocket_tts import TTSModel  # type: ignore
            except Exception as exc:
                raise ImportError("pocket-tts is not installed") from exc

            variant = getattr(config, "pocket_tts_variant", "b6369a24")
            temp = float(getattr(config, "pocket_tts_temp", 0.7))
            lsd_decode_steps = int(getattr(config, "pocket_tts_lsd_decode_steps", 1))
            eos_threshold = float(getattr(config, "pocket_tts_eos_threshold", -4.0))

            logger.info("Loading Pocket-TTS model...")
            self._model = TTSModel.load_model(
                variant=variant,
                temp=temp,
                lsd_decode_steps=lsd_decode_steps,
                eos_threshold=eos_threshold,
            )
            try:
                self._sample_rate = int(getattr(self._model, "sample_rate", 24000))
            except Exception:
                self._sample_rate = 24000

    def warmup(self, voice: Optional[str] = None, voice_prompt: Optional[str] = None) -> None:
        if not bool(getattr(config, "use_pocket_tts", False)):
            return
        try:
            self._ensure_loaded()
            voice_id = self._normalize_voice_id(voice, voice_prompt)
            try:
                state = self._get_voice_state(voice_id)
                try:
                    _ = self._model.generate_audio(state, "Hello.")
                except Exception as exc:
                    logger.warning(f"Pocket-TTS warmup audio failed: {exc}")
            except Exception as exc:
                logger.warning(f"Pocket-TTS warmup skipped voice '{voice_id}': {exc}")
        except Exception as exc:
            logger.warning(f"Pocket-TTS warmup failed: {exc}")

    @property
    def sample_rate(self) -> int:
        self._ensure_loaded()
        return self._sample_rate

    def _normalize_voice_id(self, voice: Optional[str], voice_prompt: Optional[str]) -> str:
        if voice_prompt:
            return voice_prompt.strip()

        default_prompt = getattr(config, "pocket_tts_default_voice_prompt", "")
        if isinstance(default_prompt, str) and default_prompt.strip():
            return default_prompt.strip()

        predefined_voices = {
            "alba",
            "marius",
            "javert",
            "jean",
            "fantine",
            "cosette",
            "eponine",
            "azelma",
        }

        if voice and voice.strip():
            key = voice.strip()
            key_l = key.lower()
            if key_l in predefined_voices:
                return key_l
            return key

        default_voice = getattr(config, "pocket_tts_default_voice", "")
        if isinstance(default_voice, str) and default_voice.strip():
            key = default_voice.strip()
            key_l = key.lower()
            if key_l in predefined_voices:
                return key_l
            return key

        return "alba"

    def _get_voice_state(self, voice_id: str) -> Any:
        self._ensure_loaded()

        resolved_id = self._resolve_voice_prompt_ref(voice_id)

        with self._lock:
            cached = self._voice_state_cache.get(resolved_id)
            if cached is not None:
                return cached

        try:
            state = self._model.get_state_for_audio_prompt(resolved_id)
        except Exception as exc:
            logger.exception(f"Pocket-TTS failed to load voice prompt: {voice_id}")
            raise RuntimeError(f"Failed to load Pocket-TTS voice prompt: {voice_id} ({exc})") from exc

        with self._lock:
            self._voice_state_cache[resolved_id] = state
        return state

    def synthesize_wav(self, text: str, voice: Optional[str] = None, voice_prompt: Optional[str] = None) -> Tuple[bytes, Dict[str, Any]]:
        if not bool(getattr(config, "use_pocket_tts", False)):
            raise RuntimeError("Pocket-TTS is disabled (USE_POCKET_TTS=false)")

        clean = (text or "").strip()
        if not clean:
            raise ValueError("Missing text")

        max_chars = int(getattr(config, "pocket_tts_max_chars", 8000))
        if len(clean) > max_chars:
            clean = clean[:max_chars]

        voice_id = self._normalize_voice_id(voice, voice_prompt)
        state = self._get_voice_state(voice_id)

        self._ensure_loaded()

        try:
            audio = self._model.generate_audio(state, clean)
        except Exception as exc:
            raise RuntimeError("Pocket-TTS generation failed") from exc

        try:
            import torch  # type: ignore

            audio_t = audio.detach().cpu().contiguous()
            if audio_t.ndim != 1:
                audio_t = audio_t.reshape(-1)

            if audio_t.dtype == torch.int16:
                pcm_i16 = audio_t
            elif audio_t.dtype.is_floating_point:
                max_abs = float(audio_t.abs().max().item()) if audio_t.numel() else 0.0
                if max_abs <= 1.25:
                    pcm_i16 = (torch.clamp(audio_t, -1.0, 1.0) * 32767.0).round().to(torch.int16)
                elif max_abs <= 32767.0 * 1.25:
                    pcm_i16 = torch.clamp(audio_t, -32768.0, 32767.0).round().to(torch.int16)
                else:
                    if max_abs <= 0:
                        pcm_i16 = torch.zeros_like(audio_t, dtype=torch.int16)
                    else:
                        pcm_i16 = (audio_t / max_abs * 32767.0).round().to(torch.int16)
            else:
                max_abs = float(audio_t.abs().max().item()) if audio_t.numel() else 0.0
                if max_abs <= 32767.0 * 1.25:
                    pcm_i16 = torch.clamp(audio_t, -32768, 32767).to(torch.int16)
                else:
                    if max_abs <= 0:
                        pcm_i16 = torch.zeros_like(audio_t, dtype=torch.int16)
                    else:
                        pcm_i16 = (audio_t.to(torch.float32) / max_abs * 32767.0).round().to(torch.int16)

            pcm = pcm_i16.numpy().tobytes()
        except Exception as exc:
            raise RuntimeError("Failed to convert Pocket-TTS audio to PCM") from exc

        buf = BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm)

        meta = {
            "engine": "pocket_tts",
            "voice_id": voice_id,
            "sample_rate": self.sample_rate,
            "text_chars": len(clean),
        }
        return buf.getvalue(), meta


_engine: Optional[PocketTTSEngine] = None
_engine_lock = threading.Lock()


def get_pocket_tts_engine() -> PocketTTSEngine:
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            _engine = PocketTTSEngine()
    return _engine
