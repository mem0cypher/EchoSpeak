"""
Voice I/O module for Echo Speak.
Handles speech-to-text (STT) and text-to-speech (TTS) operations.
FIXED VERSION - Audio output debugging and robust TTS.
"""

import os
import sys
import time
import asyncio
import threading
from typing import Optional, Callable, List
from loguru import logger

try:
    import speech_recognition as sr
    SR_AVAILABLE = True
except ImportError:
    SR_AVAILABLE = False
    logger.warning("SpeechRecognition not available")

try:
    import pyttsx3
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False
    logger.warning("pyttsx3 not available")

try:
    import winsound
    WINSOUND_AVAILABLE = True
except ImportError:
    WINSOUND_AVAILABLE = False

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config


class VoiceOutput:
    """Handles text-to-speech output with robust audio output."""

    def __init__(self):
        """Initialize the text-to-speech engine."""
        self.engine = None
        self.sapi5_works = False

        self.backend = "pyttsx3"
        self._select_backend()

        if self.backend == "pyttsx3":
            if TTS_AVAILABLE:
                self._init_engines()
            else:
                logger.warning("pyttsx3 not available, voice output disabled")

    def _select_backend(self) -> None:
        engine = (getattr(config, "voice", None) and getattr(config.voice, "engine", "auto")) or "auto"
        engine = str(engine).strip().lower()

        if engine == "pocket":
            if bool(getattr(config, "use_pocket_tts", False)):
                self.backend = "pocket"
                return
            logger.warning("VOICE_ENGINE=pocket requested but USE_POCKET_TTS is false; falling back")

        if engine in ("auto", "pyttsx3", "sapi5"):
            if engine == "auto" and bool(getattr(config, "use_pocket_tts", False)):
                self.backend = "pocket"
                return
            self.backend = "pyttsx3"
            return

        if engine == "edge":
            logger.warning("VOICE_ENGINE=edge is no longer supported; falling back")

        logger.warning(f"Unknown VOICE_ENGINE '{engine}', falling back to auto")
        if bool(getattr(config, "use_pocket_tts", False)):
            self.backend = "pocket"
        else:
            self.backend = "pyttsx3"

    def _init_engines(self):
        """Initialize and test TTS engines."""
        # Test sapi5 (Windows default)
        try:
            logger.info("Initializing pyttsx3 with sapi5 driver...")
            self.engine = pyttsx3.init(driverName='sapi5')
            self._configure_voice()
            self.sapi5_works = self._test_output()
            if self.sapi5_works:
                logger.info("sapi5 driver working!")
                return
        except Exception as e:
            logger.warning(f"sapi5 failed: {e}")

        # Test sapi5 with explicit loop
        try:
            logger.info("Initializing pyttsx3 with sapi5 explicit loop...")
            self.engine = pyttsx3.init(driverName='sapi5')
            self._configure_voice()
            self.engine._inLoop = False
            self._test_output()
            return
        except Exception as e:
            logger.warning(f"sapi5 explicit loop failed: {e}")

        # Try nsss (macOS) or espeak (Linux)
        drivers = ['nsss', 'espeak']
        for driver in drivers:
            try:
                logger.info(f"Trying driver: {driver}")
                self.engine = pyttsx3.init(driverName=driver)
                self._configure_voice()
                if self._test_output():
                    logger.info(f"Driver {driver} working!")
                    return
            except:
                pass

        logger.error("All TTS drivers failed!")
        self.engine = None

    def _test_output(self) -> bool:
        """Test if audio output is working."""
        try:
            self.engine.say("test")
            self.engine.runAndWait()
            return True
        except Exception as e:
            logger.error(f"TTS test failed: {e}")
            return False

    def _configure_voice(self):
        """Configure voice rate, volume, and voice."""
        try:
            self.engine.setProperty('volume', float(getattr(config.voice, 'volume', 1.0)))

            # Set rate
            self.engine.setProperty('rate', config.voice.rate)

            # Select a female voice
            voices = self.engine.getProperty('voices')
            if voices:
                for voice in voices:
                    if 'female' in voice.name.lower() or 'zira' in voice.name.lower():
                        self.engine.setProperty('voice', voice.id)
                        logger.info(f"Selected voice: {voice.name}")
                        break
        except Exception as e:
            logger.warning(f"Voice config failed: {e}")

    def speak(self, text: str) -> None:
        """
        Convert text to speech and play it.

        Args:
            text: Text to speak.
        """
        if not text or not text.strip():
            return

        if self.engine is None:
            logger.error("TTS engine not available")
            return

        text = text.strip()
        if not text:
            return

        logger.info(f"Speaking: {text[:60]}...")

        if self.backend == "pocket":
            try:
                self._speak_pocket(text)
                return
            except Exception as e:
                logger.error(f"Pocket-TTS failed: {e}")

        if not TTS_AVAILABLE:
            logger.error("pyttsx3 not available for fallback")
            return

        try:
            if self.engine is None or not self.sapi5_works:
                self.engine = pyttsx3.init(driverName='sapi5')
                self._configure_voice()
            self.engine.say(text)
            self.engine.runAndWait()
        except Exception as e:
            logger.error(f"pyttsx3 TTS error: {e}")
            try:
                self.engine = pyttsx3.init(driverName='sapi5')
                self._configure_voice()
                self.engine.say(text)
                self.engine.runAndWait()
            except Exception as e2:
                logger.error(f"pyttsx3 TTS re-initialize failed: {e2}")

    def _run_async(self, coro) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)
            return

        exc: List[BaseException] = []

        def runner():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(coro)
            except BaseException as e:
                exc.append(e)
            finally:
                loop.close()

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        t.join()
        if exc:
            raise exc[0]

    def _speak_pocket(self, text: str) -> None:
        if not bool(getattr(config, "use_pocket_tts", False)):
            raise RuntimeError("Pocket-TTS disabled")
        if not (WINSOUND_AVAILABLE and sys.platform.startswith("win")):
            raise RuntimeError("Pocket-TTS playback currently requires winsound on Windows")

        from io_module.pocket_tts_engine import get_pocket_tts_engine

        engine = get_pocket_tts_engine()
        wav_bytes, _meta = engine.synthesize_wav(text)
        winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)

    def test(self):
        """Test TTS by speaking a message."""
        self.speak("Echo Speak voice test. Can you hear me?")
        time.sleep(2)
        self.speak("I am ready. Testing voice output.")
        time.sleep(2)
        self.speak("If you can hear me, voice is working!")
        time.sleep(2)


class VoiceInput:
    """Handles speech recognition from microphone input."""

    def __init__(self):
        """Initialize the speech recognizer."""
        if not SR_AVAILABLE:
            logger.warning("SpeechRecognition not available")
            self.recognizer = None
            return

        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.8
        logger.info("Voice input initialized")

    def listen(self, timeout: float = None, phrase_limit: float = None) -> Optional[str]:
        """
        Listen for voice input from the microphone.

        Args:
            timeout: Maximum time to wait for speech to start.
            phrase_limit: Maximum time to allow for a phrase.

        Returns:
            Transcribed text or None if no speech detected.
        """
        if not SR_AVAILABLE:
            return None

        timeout_val = timeout if timeout is not None else config.voice.timeout
        phrase_limit_val = phrase_limit if phrase_limit is not None else config.voice.phrase_limit

        try:
            with sr.Microphone() as source:
                logger.debug("Listening for voice input...")
                self.recognizer.adjust_for_ambient_noise(source, duration=0.5)

                audio = self.recognizer.listen(
                    source,
                    timeout=timeout_val,
                    phrase_time_limit=phrase_limit_val
                )

            text = self.recognizer.recognize_google(audio)
            logger.info(f"Recognized: {text}")
            return text

        except sr.WaitTimeoutError:
            logger.debug("No speech detected within timeout")
            return None
        except sr.UnknownValueError:
            logger.debug("Speech was unintelligible")
            return None
        except sr.RequestError as e:
            logger.error(f"Speech recognition request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Voice input error: {e}")
            return None


class VoiceManager:
    """Manages both voice input and output."""

    def __init__(self):
        """Initialize voice input and output."""
        self.input = VoiceInput()
        self.output = VoiceOutput()
        logger.info("Voice manager initialized")

    def conversational_loop(self, process_callback: Callable[[str], str], wake_word: str = None) -> None:
        """
        Run a conversational voice loop.

        Args:
            process_callback: Function to process user input and return response.
            wake_word: Word to activate listening (e.g., "echo", "jarvis").
        """
        if wake_word is None:
            wake_word = "echo"

        logger.info(f"Starting voice mode with wake word: '{wake_word}'")

        # Initial greeting
        self.output.speak(f"Echo Speak is ready. Say '{wake_word}' to start a conversation.")

        while True:
            try:
                text = self.input.listen(timeout=5.0)

                if text and text.strip().lower() == wake_word.lower():
                    logger.info(f"Wake word detected: {text}")
                    self.output.speak("Yes, I'm listening. What can I help you with?")

                    # Listen for the actual query
                    user_query = self.input.listen(timeout=10.0, phrase_limit=8.0)

                    if user_query:
                        logger.info(f"User query: {user_query}")
                        response = process_callback(user_query)
                        logger.info(f"Response: {response[:80]}...")
                        self.output.speak(response)
                    else:
                        logger.debug("No user query detected after wake word")
                else:
                    logger.debug(f"Ignored input: {text}")

            except KeyboardInterrupt:
                logger.info("Voice mode interrupted by user")
                self.output.speak("Goodbye!")
                break
            except Exception as e:
                logger.error(f"Error in conversational loop: {e}")
                try:
                    self.output.speak("An error occurred. Please try again.")
                except:
                    pass


def create_voice_manager() -> VoiceManager:
    """
    Create a voice manager instance.

    Returns:
        Configured VoiceManager instance.
    """
    return VoiceManager()
