"""
PersonaPlex WebSocket Client for EchoSpeak.
Full-duplex audio streaming with Opus encoding via sphn.
Supports mic pause/resume for tool mode and PCM callbacks for local STT.
"""

import asyncio
import json
import struct
import threading
from typing import Optional, Callable, Any
from queue import Queue, Empty
from loguru import logger

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    logger.warning("numpy not available for PersonaPlex")

try:
    import sounddevice as sd
    SD_AVAILABLE = True
except ImportError:
    SD_AVAILABLE = False
    logger.warning("sounddevice not available for PersonaPlex")

try:
    import sphn
    SPHN_AVAILABLE = True
except ImportError:
    SPHN_AVAILABLE = False
    logger.warning("sphn (Opus codec) not available for PersonaPlex")

try:
    import websockets
    from websockets.client import WebSocketClientProtocol
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False
    logger.warning("websockets not available for PersonaPlex")


# Frame type constants
FRAME_HANDSHAKE = 0x00
FRAME_AUDIO = 0x01
FRAME_TEXT = 0x02
FRAME_CONTROL = 0x03


class AudioPlaybackQueue:
    """Thread-safe audio playback queue with sounddevice."""

    def __init__(self, sample_rate: int = 24000, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        self._queue: Queue[np.ndarray] = Queue()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stream: Optional[sd.OutputStream] = None

    def start(self, device: Optional[str] = None):
        """Start the playback thread."""
        if not SD_AVAILABLE or not NUMPY_AVAILABLE:
            logger.error("Cannot start playback: sounddevice or numpy not available")
            return

        self._running = True

        def callback(outdata, frames, time_info, status):
            try:
                data = self._queue.get_nowait()
                if len(data) < frames:
                    outdata[:len(data)] = data.reshape(-1, 1)
                    outdata[len(data):] = 0
                else:
                    outdata[:] = data[:frames].reshape(-1, 1)
            except Empty:
                outdata[:] = 0

        self._stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            callback=callback,
            device=device,
            dtype='float32'
        )
        self._stream.start()
        logger.info("Audio playback started")

    def stop(self):
        """Stop playback."""
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        logger.info("Audio playback stopped")

    def enqueue(self, pcm_data: np.ndarray):
        """Add PCM data to playback queue."""
        self._queue.put(pcm_data)


class PersonaPlexClient:
    """
    Async WebSocket client for NVIDIA PersonaPlex.

    Frame Protocol:
    - 0x00 (Handshake): JSON config on connect
    - 0x01 (Audio): Opus frames
    - 0x02 (Text): Text token events
    - 0x03 (Control): Interrupt, metadata
    """

    def __init__(
        self,
        url: str,
        sample_rate: int = 24000,
        channels: int = 1,
        frame_ms: int = 20,
        text_prompt: str = "",
        voice_prompt: str = "",
        voice: str = "",
        audio_temperature: float = 0.7,
        text_temperature: float = 0.7,
        audio_topk: int = 50,
        text_topk: int = 50,
        input_device: Optional[str] = None,
        output_device: Optional[str] = None,
        handshake_json: str = "",
        ssl_verify: bool = True,
        connect_timeout: float = 10.0,
        ping_interval: float = 20.0,
        ping_timeout: float = 20.0,
        on_text: Optional[Callable[[str], None]] = None,
        on_pcm: Optional[Callable[[np.ndarray], None]] = None,
    ):
        self.url = url
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_ms = frame_ms
        self.text_prompt = text_prompt
        self.voice_prompt = voice_prompt
        self.voice = voice
        self.audio_temperature = audio_temperature
        self.text_temperature = text_temperature
        self.audio_topk = audio_topk
        self.text_topk = text_topk
        self.input_device = input_device
        self.output_device = output_device
        self.handshake_json = handshake_json
        self.ssl_verify = ssl_verify
        self.connect_timeout = connect_timeout
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout

        self.on_text = on_text
        self.on_pcm = on_pcm

        self._ws: Optional[WebSocketClientProtocol] = None
        self._encoder: Optional[Any] = None
        self._decoder: Optional[Any] = None
        self._playback: Optional[AudioPlaybackQueue] = None
        self._mic_stream: Optional[sd.InputStream] = None
        self._mic_paused = False
        self._running = False
        self._send_queue: Queue[bytes] = Queue()

        self._frame_size = int(sample_rate * frame_ms / 1000)

    async def connect(self):
        """Connect to PersonaPlex WebSocket."""
        if not WS_AVAILABLE:
            raise RuntimeError("websockets library not available")
        if not SPHN_AVAILABLE:
            raise RuntimeError("sphn library not available")

        import ssl
        ssl_context = None
        if self.url.startswith("wss://"):
            ssl_context = ssl.create_default_context()
            if not self.ssl_verify:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

        logger.info(f"Connecting to PersonaPlex: {self.url}")
        self._ws = await asyncio.wait_for(
            websockets.connect(
                self.url,
                ssl=ssl_context,
                ping_interval=self.ping_interval,
                ping_timeout=self.ping_timeout,
            ),
            timeout=self.connect_timeout
        )
        logger.info("PersonaPlex WebSocket connected")

        # Initialize Opus encoder/decoder
        self._encoder = sphn.OpusEncoder(self.sample_rate, self.channels)
        self._decoder = sphn.OpusDecoder(self.sample_rate, self.channels)

        # Send handshake
        await self._send_handshake()

        # Start playback queue
        self._playback = AudioPlaybackQueue(self.sample_rate, self.channels)
        self._playback.start(self.output_device)

        self._running = True

    async def _send_handshake(self):
        """Send handshake frame with config."""
        if self.handshake_json.strip():
            config_data = json.loads(self.handshake_json)
        else:
            config_data = {
                "sample_rate": self.sample_rate,
                "channels": self.channels,
                "frame_ms": self.frame_ms,
                "text_prompt": self.text_prompt,
                "voice_prompt": self.voice_prompt,
                "voice": self.voice,
                "audio_temperature": self.audio_temperature,
                "text_temperature": self.text_temperature,
                "audio_topk": self.audio_topk,
                "text_topk": self.text_topk,
            }

        payload = json.dumps(config_data).encode("utf-8")
        frame = struct.pack("B", FRAME_HANDSHAKE) + payload
        await self._ws.send(frame)
        logger.debug("Handshake sent")

    async def disconnect(self):
        """Disconnect from PersonaPlex."""
        self._running = False
        self.stop_mic()
        if self._playback:
            self._playback.stop()
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("PersonaPlex disconnected")

    def start_mic(self):
        """Start microphone streaming."""
        if not SD_AVAILABLE or not NUMPY_AVAILABLE:
            logger.error("Cannot start mic: sounddevice or numpy not available")
            return

        self._mic_paused = False

        def mic_callback(indata, frames, time_info, status):
            if self._mic_paused or not self._running:
                return
            # Encode to Opus and queue for sending
            pcm = indata[:, 0].astype(np.float32)
            if self._encoder:
                opus_data = self._encoder.encode(pcm)
                frame = struct.pack("B", FRAME_AUDIO) + opus_data
                self._send_queue.put(frame)
            # Optional PCM callback for local STT
            if self.on_pcm:
                self.on_pcm(pcm)

        self._mic_stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            callback=mic_callback,
            blocksize=self._frame_size,
            device=self.input_device,
            dtype='float32'
        )
        self._mic_stream.start()
        logger.info("Microphone streaming started")

    def stop_mic(self):
        """Stop microphone streaming."""
        if self._mic_stream:
            self._mic_stream.stop()
            self._mic_stream.close()
            self._mic_stream = None
        logger.info("Microphone streaming stopped")

    def pause_mic(self):
        """Pause mic streaming (for tool mode)."""
        self._mic_paused = True
        logger.info("Microphone paused for tool mode")

    def resume_mic(self):
        """Resume mic streaming after tool mode."""
        self._mic_paused = False
        logger.info("Microphone resumed")

    async def send_interrupt(self):
        """Send interrupt control frame."""
        if self._ws:
            frame = struct.pack("B", FRAME_CONTROL) + b'{"type":"interrupt"}'
            await self._ws.send(frame)
            logger.debug("Interrupt sent")

    async def run(self):
        """Main event loop: send mic frames, receive audio/text."""
        send_task = asyncio.create_task(self._send_loop())
        recv_task = asyncio.create_task(self._recv_loop())

        try:
            await asyncio.gather(send_task, recv_task)
        except asyncio.CancelledError:
            pass
        finally:
            send_task.cancel()
            recv_task.cancel()

    async def _send_loop(self):
        """Send queued audio frames to WebSocket."""
        while self._running:
            try:
                frame = await asyncio.get_event_loop().run_in_executor(
                    None, self._send_queue.get, True, 0.1
                )
                if self._ws:
                    await self._ws.send(frame)
            except Empty:
                await asyncio.sleep(0.01)
            except Exception as e:
                logger.error(f"Send error: {e}")
                break

    async def _recv_loop(self):
        """Receive and process frames from WebSocket."""
        while self._running:
            try:
                data = await self._ws.recv()
                if isinstance(data, bytes) and len(data) > 0:
                    frame_type = data[0]
                    payload = data[1:]

                    if frame_type == FRAME_AUDIO:
                        # Decode Opus to PCM and play
                        if self._decoder and self._playback:
                            pcm = self._decoder.decode(payload)
                            self._playback.enqueue(pcm)

                    elif frame_type == FRAME_TEXT:
                        # Text token event
                        text = payload.decode("utf-8", errors="replace")
                        logger.debug(f"Text event: {text}")
                        if self.on_text:
                            self.on_text(text)

                    elif frame_type == FRAME_CONTROL:
                        # Control frame (metadata, end, etc.)
                        ctrl = payload.decode("utf-8", errors="replace")
                        logger.debug(f"Control event: {ctrl}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Receive error: {e}")
                break


class PersonaPlexOrchestrator:
    """
    High-level orchestrator for PersonaPlex integration.
    Manages the client lifecycle and tool routing.
    """

    def __init__(self, config: Any, agent: Any = None, transcribe_fn: Optional[Callable] = None):
        from config import config as app_config
        self.cfg = config or app_config.personaplex
        self.agent = agent
        self.transcribe_fn = transcribe_fn
        self.client: Optional[PersonaPlexClient] = None
        self._text_buffer: list[str] = []
        self._tool_mode = False

    def _on_text(self, text: str):
        """Handle text events from PersonaPlex."""
        self._text_buffer.append(text)
        # Check for tool-intent keywords
        combined = "".join(self._text_buffer)
        if self._detect_tool_intent(combined):
            self._trigger_tool_mode()

    def _detect_tool_intent(self, text: str) -> bool:
        """Detect if text contains tool-intent keywords."""
        keywords = ["search", "look up", "find", "calculate", "run", "open", "browse"]
        text_lower = text.lower()
        return any(kw in text_lower for kw in keywords)

    def _trigger_tool_mode(self):
        """Pause mic and run local tool processing."""
        if self._tool_mode:
            return
        self._tool_mode = True
        logger.info("Tool mode triggered")
        if self.client:
            self.client.pause_mic()
        # Process with local STT if available
        # (This would integrate with transcribe_bytes)
        # For now, just log
        logger.info(f"Tool intent detected: {''.join(self._text_buffer)}")

    def end_tool_mode(self):
        """Resume mic after tool processing."""
        self._tool_mode = False
        self._text_buffer.clear()
        if self.client:
            self.client.resume_mic()
        logger.info("Tool mode ended, mic resumed")

    async def run(self):
        """Run the PersonaPlex voice mode."""
        self.client = PersonaPlexClient(
            url=self.cfg.url,
            sample_rate=self.cfg.sample_rate,
            channels=self.cfg.channels,
            frame_ms=self.cfg.frame_ms,
            text_prompt=self.cfg.text_prompt,
            voice_prompt=self.cfg.voice_prompt,
            voice=self.cfg.voice,
            audio_temperature=self.cfg.audio_temperature,
            text_temperature=self.cfg.text_temperature,
            audio_topk=self.cfg.audio_topk,
            text_topk=self.cfg.text_topk,
            input_device=self.cfg.input_device,
            output_device=self.cfg.output_device,
            handshake_json=self.cfg.handshake_json,
            ssl_verify=self.cfg.ssl_verify,
            connect_timeout=self.cfg.connect_timeout,
            ping_interval=self.cfg.ping_interval,
            ping_timeout=self.cfg.ping_timeout,
            on_text=self._on_text,
        )

        await self.client.connect()
        self.client.start_mic()

        try:
            await self.client.run()
        finally:
            await self.client.disconnect()


def run_personaplex_voice_mode(agent: Any = None):
    """Entry point for PersonaPlex voice mode."""
    from config import config

    if not config.personaplex.enabled:
        logger.warning("PersonaPlex is not enabled in config")
        return

    orchestrator = PersonaPlexOrchestrator(config.personaplex, agent)
    asyncio.run(orchestrator.run())
