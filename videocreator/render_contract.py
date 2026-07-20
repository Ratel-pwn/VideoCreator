from __future__ import annotations

from typing import Any


def ms_to_frame(milliseconds: int, fps: int) -> int:
    return round(milliseconds * fps / 1000)


def normalize_scenes(
    visual_plan: dict[str, Any],
    assets_by_scene: dict[str, dict[str, Any]],
    *,
    fps: int,
    spoken_end_ms: int,
) -> list[dict[str, Any]]:
    source = visual_plan["segments"]
    scenes: list[dict[str, Any]] = []
    for index, segment in enumerate(source):
        start_ms = 0 if index == 0 else int(segment["start_ms"])
        end_ms = (
            int(source[index + 1]["start_ms"])
            if index + 1 < len(source)
            else spoken_end_ms
        )
        from_frame = ms_to_frame(start_ms, fps)
        end_frame = ms_to_frame(end_ms, fps)
        if scenes and from_frame < (
            scenes[-1]["fromFrame"] + scenes[-1]["durationInFrames"]
        ):
            raise ValueError(f"{segment['segment_id']}: scene overlaps previous scene")
        if end_frame <= from_frame:
            raise ValueError(f"{segment['segment_id']}: scene has no positive duration")

        scene_id = str(segment["segment_id"])
        subtitle_only = segment.get("material_type") == "subtitle_only"
        record = assets_by_scene.get(scene_id, {})
        if not subtitle_only and not record:
            raise ValueError(f"{scene_id}: approved asset is missing")
        scenes.append(
            {
                "id": scene_id,
                "fromFrame": from_frame,
                "durationInFrames": end_frame - from_frame,
                "assetType": "subtitle_only"
                if subtitle_only
                else record["asset_type"],
                "assetPath": record.get("local_path", ""),
                "fitMode": record.get("fit_mode", "cover"),
                "trimBeforeFrames": ms_to_frame(
                    int(record.get("trim_start_ms", 0)), fps
                ),
                "mediaDurationInFrames": ms_to_frame(
                    int(record.get("duration_ms") or 0), fps
                ),
                "shortVideoPolicy": record.get("short_video_policy", "reject"),
                "motionPreset": "push-left" if index % 2 == 0 else "push-right",
            }
        )
    return scenes


def build_render_input(
    *,
    video_id: str,
    scenes: list[dict[str, Any]],
    audio_path: str,
    subtitle_path: str,
    fps: int = 25,
) -> dict[str, Any]:
    if not scenes:
        raise ValueError("At least one scene is required")
    last_scene = scenes[-1]
    duration = last_scene["fromFrame"] + last_scene["durationInFrames"]
    return {
        "videoId": video_id,
        "width": 1920,
        "height": 1080,
        "fps": fps,
        "durationInFrames": duration,
        "audioPath": audio_path,
        "subtitlePath": subtitle_path,
        "backgroundColor": "#080b0f",
        "scenes": scenes,
    }
