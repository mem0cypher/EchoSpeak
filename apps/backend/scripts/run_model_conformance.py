"""Run bounded EchoSpeak 8.0 conformance against LM Studio or native Qwen."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.model_conformance import (  # noqa: E402
    OpenAICompatibleStreamingTransport,
    run_live_conformance,
    write_conformance_report,
)
from agent.native_model_runtime import (  # noqa: E402
    NativeLlamaCppRuntime,
    NativeRuntimeConfig,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("lmstudio", "echo-native"), required=True)
    parser.add_argument("--model", required=True, help="Exact LM Studio model id or approved Qwen GGUF path")
    parser.add_argument("--base-url", default="http://127.0.0.1:1234")
    parser.add_argument("--llama-server", default="")
    parser.add_argument(
        "--output",
        default="",
        help="Optional report path. Defaults to the canonical data/model_conformance store.",
    )
    parser.add_argument("--context-size", type=int, default=32768)
    parser.add_argument("--gpu-layers", type=int, default=-1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.provider == "lmstudio":
        transport = OpenAICompatibleStreamingTransport(
            base_url=args.base_url,
            model_id=args.model,
        )
        report = run_live_conformance(
            provider="lmstudio",
            model_id=args.model,
            transport=transport,
        )
    else:
        if not args.llama_server:
            raise SystemExit("--llama-server is required for echo-native")
        config = NativeRuntimeConfig(
            llama_server=Path(args.llama_server),
            approved_qwen_model=Path(args.model),
            context_size=args.context_size,
            gpu_layers=args.gpu_layers,
        )
        with NativeLlamaCppRuntime(config) as transport:
            report = run_live_conformance(
                provider="echo-native",
                model_id=Path(args.model).name,
                transport=transport,
            )
    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else None
    )
    written = write_conformance_report(report, output)
    print(f"wrote={written}")
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True, default=str))
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
