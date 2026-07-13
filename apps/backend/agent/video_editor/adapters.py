"""Capability-based adapter contracts for local and cloud video models.

Runtime asks what a model can do. Editing logic must not hardcode model names.
Basic manual/agentic editing works with zero generative adapters installed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class VideoAdapterCapabilities:
    adapter_id: str
    display_name: str
    location: str  # local | cloud
    available: bool = False
    # Capability/operation tokens (capability-first; product names stay in display_name).
    operations: tuple[str, ...] = ()
    max_duration_seconds: Optional[float] = None
    supports_cancel: bool = False
    supports_candidates: bool = True
    supports_retry: bool = True
    requires_gpu: bool = False
    minimum_vram_gb: Optional[float] = None
    license: str = ""
    notes: tuple[str, ...] = ()
    # Explicit cost/upload gates for cloud adapters.
    requires_cloud_upload_approval: bool = False
    requires_cost_approval: bool = False


class VideoModelAdapter(ABC):
    """Unified contract for understanding, analysis, and generation adapters."""

    @abstractmethod
    def capabilities(self) -> VideoAdapterCapabilities: ...

    @abstractmethod
    def estimate(self, parameters: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def submit(self, job_id: str, parameters: dict[str, Any]) -> str: ...

    @abstractmethod
    def poll(self, provider_job_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def cancel(self, provider_job_id: str) -> bool: ...

    def fetch_candidates(self, provider_job_id: str) -> list[dict[str, Any]]:
        return []

    def verify_output(self, candidate: dict[str, Any]) -> dict[str, Any]:
        return {"verified": False, "reason": "adapter does not implement verify_output"}


# Backward-compatible alias used by earlier foundation code.
VideoGenerationAdapter = VideoModelAdapter


class VideoAdapterRegistry:
    _adapters: dict[str, VideoModelAdapter] = {}
    # Declared shells — available=False until a real adapter is registered + probed.
    _declared: dict[str, VideoAdapterCapabilities] = {
        "deterministic-local": VideoAdapterCapabilities(
            adapter_id="deterministic-local",
            display_name="Deterministic Editor Runtime",
            location="local",
            available=True,
            operations=("timeline_edit",),
            supports_candidates=False,
            supports_cancel=False,
            license="EchoSpeak",
            notes=(
                "Always available. Manual and agentic timeline operations require no generative model.",
            ),
        ),
        "ffprobe-local": VideoAdapterCapabilities(
            adapter_id="ffprobe-local",
            display_name="ffprobe media inspect",
            location="local",
            available=False,  # flipped by probe at report time when binary exists
            operations=("media_probe",),
            supports_candidates=False,
            license="FFmpeg/LGPL-or-GPL",
            notes=("Used only for immutable media metadata; never mutates sources.",),
        ),
        "wan22-local": VideoAdapterCapabilities(
            adapter_id="wan22-local",
            display_name="Wan 2.2 (local experimental)",
            location="local",
            operations=("text_to_video", "image_to_video"),
            requires_gpu=True,
            license="Apache-2.0",
            notes=("Not installed by EchoSpeak; capability probe required.",),
        ),
        "ltx-cloud": VideoAdapterCapabilities(
            adapter_id="ltx-cloud",
            display_name="LTX API",
            location="cloud",
            operations=("text_to_video", "image_to_video", "audio_to_video", "extend", "retake"),
            supports_cancel=True,
            license="provider-terms",
            requires_cloud_upload_approval=True,
            requires_cost_approval=True,
            notes=("Requires explicit cloud upload/cost approval.",),
        ),
        "runway-cloud": VideoAdapterCapabilities(
            adapter_id="runway-cloud",
            display_name="Runway API",
            location="cloud",
            operations=("text_to_video", "image_to_video", "video_to_video"),
            supports_cancel=True,
            license="provider-terms",
            requires_cloud_upload_approval=True,
            requires_cost_approval=True,
            notes=("Requires explicit cloud upload/cost approval.",),
        ),
        "analysis-shell": VideoAdapterCapabilities(
            adapter_id="analysis-shell",
            display_name="Analysis worker shell",
            location="local",
            available=False,
            operations=("understand", "scene_detect", "transcribe", "track"),
            supports_candidates=False,
            license="EchoSpeak",
            notes=("Durable job records only until a real analysis worker is installed.",),
        ),
    }

    @classmethod
    def register(cls, adapter: VideoModelAdapter) -> None:
        capabilities = adapter.capabilities()
        cls._adapters[capabilities.adapter_id] = adapter

    @classmethod
    def get(cls, adapter_id: str) -> Optional[VideoModelAdapter]:
        return cls._adapters.get(str(adapter_id or ""))

    @classmethod
    def capabilities(cls) -> list[dict[str, Any]]:
        rows = dict(cls._declared)
        # Live probe for ffprobe without claiming generation.
        try:
            import shutil

            if shutil.which("ffprobe") and "ffprobe-local" in rows:
                base = rows["ffprobe-local"]
                rows["ffprobe-local"] = VideoAdapterCapabilities(
                    adapter_id=base.adapter_id,
                    display_name=base.display_name,
                    location=base.location,
                    available=True,
                    operations=base.operations,
                    supports_candidates=base.supports_candidates,
                    license=base.license,
                    notes=base.notes,
                )
        except Exception:
            pass
        for adapter_id, adapter in cls._adapters.items():
            rows[adapter_id] = adapter.capabilities()
        return [asdict(item) for item in rows.values()]

    @classmethod
    def adapters_for_capability(cls, capability: str) -> list[dict[str, Any]]:
        capability = str(capability or "").strip()
        hits = []
        for row in cls.capabilities():
            ops = {str(op) for op in (row.get("operations") or ())}
            if capability in ops or any(capability in str(op) for op in ops):
                hits.append(row)
        return hits
