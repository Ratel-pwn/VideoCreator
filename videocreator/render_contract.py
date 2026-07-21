from __future__ import annotations

from typing import Any


def ms_to_frame(milliseconds: int, fps: int) -> int:
    return round(milliseconds * fps / 1000)


def normalize_frame_config(presentation: dict[str, Any]) -> dict[str, str]:
    fields = {
        "frame_preset": "preset",
        "video_title": "videoTitle",
        "publication_date": "publicationDate",
        "creator_handle": "creatorHandle",
    }
    missing = [
        source
        for source in fields
        if not str(presentation.get(source, "")).strip()
    ]
    if missing:
        raise ValueError(f"Presentation config is missing: {', '.join(missing)}")
    return {
        target: str(presentation[source]).strip()
        for source, target in fields.items()
    }


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


def _render_media(record: dict[str, Any], fps: int) -> dict[str, Any]:
    return {
        "assetType": record["asset_type"],
        "assetPath": record["local_path"],
        "fitMode": record.get("fit_mode", "cover"),
        "trimBeforeFrames": ms_to_frame(int(record.get("trim_start_ms", 0)), fps),
        "mediaDurationInFrames": ms_to_frame(int(record.get("duration_ms") or 0), fps),
        "shortVideoPolicy": record.get("short_video_policy", "reject"),
    }


def normalize_v2_scenes(
    visual_plan: dict[str, Any],
    assets_by_request: dict[str, dict[str, Any]],
    *,
    fps: int,
    spoken_end_ms: int,
) -> list[dict[str, Any]]:
    source = visual_plan["segments"]
    scenes: list[dict[str, Any]] = []
    for index, segment in enumerate(source):
        start_ms = 0 if index == 0 else int(segment["start_ms"])
        end_ms = int(source[index + 1]["start_ms"]) if index + 1 < len(source) else spoken_end_ms
        from_frame = ms_to_frame(start_ms, fps)
        end_frame = ms_to_frame(end_ms, fps)
        if end_frame <= from_frame:
            raise ValueError(f"{segment['segment_id']}: scene has no positive duration")
        scene_id = str(segment["segment_id"])
        mode = segment["presentation_mode"]
        scene: dict[str, Any] = {
            "id": scene_id,
            "fromFrame": from_frame,
            "durationInFrames": end_frame - from_frame,
            "presentationMode": mode,
        }
        slots = {
            slot["role"]: assets_by_request.get(f"{scene_id}:{slot['role']}")
            for slot in segment.get("slots", [])
        }
        missing = [role for role, record in slots.items() if record is None]
        if missing:
            raise ValueError(f"{scene_id}: missing asset slots: {', '.join(missing)}")
        if mode in {"footage", "still"}:
            scene["primaryAsset"] = _render_media(slots["primary"], fps)
        elif mode == "entity_card":
            scene["backgroundAsset"] = _render_media(slots["background"], fps)
            scene["displayAsset"] = _render_media(slots["display"], fps)
            entity = segment["entity"]
            scene["entity"] = {
                "primaryLabel": entity["primary_label"],
                "secondaryLabel": entity.get("secondary_label"),
            }
        elif mode == "explainer":
            scene["backgroundAsset"] = _render_media(slots["background"], fps)
            scene["explainer"] = segment["explainer"]
        scenes.append(scene)
    return scenes


def build_render_input(
    *,
    video_id: str,
    scenes: list[dict[str, Any]],
    audio_path: str,
    subtitle_path: str,
    fps: int = 25,
    presentation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not scenes:
        raise ValueError("At least one scene is required")
    last_scene = scenes[-1]
    duration = last_scene["fromFrame"] + last_scene["durationInFrames"]
    render_input = {
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
    if presentation is not None:
        render_input["frame"] = normalize_frame_config(presentation)
    return render_input
