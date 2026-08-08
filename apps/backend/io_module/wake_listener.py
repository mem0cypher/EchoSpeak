from typing import Callable, Optional


_REMOVED_MESSAGE = (
    "Wake-word activation is not implemented. Use EchoSpeak's explicit local "
    "microphone control until the Phase 6 single-microphone owner is available."
)


class WakeListener:
    def __init__(
        self,
        on_wake: Optional[Callable[[], None]] = None,
        on_transcript: Optional[Callable[[str], None]] = None,
        **kwargs,
    ):
        raise RuntimeError(_REMOVED_MESSAGE)


def create_wake_listener(
    on_wake: Optional[Callable[[], None]] = None,
    on_transcript: Optional[Callable[[str], None]] = None,
    **kwargs,
) -> WakeListener:
    raise RuntimeError(_REMOVED_MESSAGE)
