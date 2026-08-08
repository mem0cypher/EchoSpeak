"""Disposable live cancellation smoke for the Echo native Qwen proof."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.model_adapters import QwenAdapter  # noqa: E402
from agent.native_model_runtime import NativeLlamaCppRuntime, NativeRuntimeConfig  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llama-server", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--context-size", type=int, default=8192)
    parser.add_argument("--gpu-layers", type=int, default=0)
    args = parser.parse_args()
    runtime = NativeLlamaCppRuntime(NativeRuntimeConfig(
        llama_server=Path(args.llama_server),
        approved_qwen_model=Path(args.model),
        context_size=args.context_size,
        gpu_layers=args.gpu_layers,
    ))
    result: dict[str, object] = {}
    runtime.load()
    try:
        def invoke() -> None:
            try:
                result["response"] = runtime.complete(
                    messages=[
                        {"role": "system", "content": "EchoSpeak cancellation smoke."},
                        {"role": "user", "content": "Write a very long detailed essay about arithmetic."},
                    ],
                    tools=[],
                    tool_choice="auto",
                    adapter=QwenAdapter(),
                )
            except Exception as exc:  # surfaced below with safe type/message
                result["error"] = {"type": exc.__class__.__name__, "message": str(exc)}

        worker = threading.Thread(target=invoke, daemon=True)
        worker.start()
        deadline = time.monotonic() + 15.0
        while runtime.telemetry.requests < 1 and time.monotonic() < deadline:
            time.sleep(0.05)
        runtime.cancel()
        worker.join(timeout=30.0)
        response = result.get("response")
        passed = (
            not worker.is_alive()
            and response is not None
            and getattr(response, "finish_reason", "") == "cancelled"
            and runtime.telemetry.cancelled >= 1
        )
        report = {
            "passed": passed,
            "worker_stopped": not worker.is_alive(),
            "finish_reason": getattr(response, "finish_reason", ""),
            "error": result.get("error"),
            "health": runtime.health(),
        }
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 0 if passed else 2
    finally:
        runtime.unload()


if __name__ == "__main__":
    raise SystemExit(main())
