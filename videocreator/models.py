from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AssetRecord:
    scene_id: str
    asset_type: str
    local_path: str
    source_page_url: str
    direct_download_url: str
    provider: str
    license: str
    credit: str
    retrieved_at: str
    duration_ms: int | None
    fit_mode: str
    trim_start_ms: int
    short_video_policy: str
    review_status: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AssetRecord":
        return cls(
            scene_id=str(value.get("scene_id", "")),
            asset_type=str(value.get("asset_type", "")),
            local_path=str(value.get("local_path", "")),
            source_page_url=str(value.get("source_page_url", "")),
            direct_download_url=str(value.get("direct_download_url", "")),
            provider=str(value.get("provider", "")),
            license=str(value.get("license", "")),
            credit=str(value.get("credit", "")),
            retrieved_at=str(value.get("retrieved_at", "")),
            duration_ms=value.get("duration_ms"),
            fit_mode=str(value.get("fit_mode", "cover")),
            trim_start_ms=int(value.get("trim_start_ms", 0)),
            short_video_policy=str(value.get("short_video_policy", "reject")),
            review_status=str(value.get("review_status", "pending")),
        )


@dataclass
class AssetAuditResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    approved_scene_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors
