"""Console voice I/O removed.

Microphone input / voice loop in the console is intentionally not supported.
Use the Web UI for mic input.
"""


class VoiceOutput:  # pragma: no cover
    def __init__(self, *args, **kwargs):
        raise RuntimeError("Console voice output has been removed")


class VoiceInput:  # pragma: no cover
    def __init__(self, *args, **kwargs):
        raise RuntimeError("Console voice input has been removed")


class VoiceManager:  # pragma: no cover
    def __init__(self, *args, **kwargs):
        raise RuntimeError("Console voice features have been removed")


def create_voice_manager(*args, **kwargs):  # pragma: no cover
    raise RuntimeError("Console voice features have been removed")
