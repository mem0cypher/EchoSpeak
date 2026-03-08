from typing import Callable, Optional


_REMOVED_MESSAGE = "Wake listener voice activation has been removed. Use browser speech controls instead."


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
