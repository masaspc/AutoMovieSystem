"""YouTube視聴維持率をレンダリング時の場面タイムラインへ結びつける。"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.insight import Insight
from app.models.publication import Publication
from app.providers.youtube.base import AudienceRetentionPoint, YouTubeProvider


def _scene_at(scenes: list[dict], seconds: float) -> dict | None:
    return next(
        (
            scene
            for scene in scenes
            if float(scene.get("start_seconds", 0)) <= seconds < float(scene.get("end_seconds", 0))
        ),
        None,
    )


def correlate_retention_dips(
    manifest: dict, points: list[AudienceRetentionPoint], *, threshold: float = 0.45
) -> list[dict]:
    """低維持率ポイントを場面へ割り当て、同一場面を重複なく返す。"""
    duration = float(manifest.get("duration_seconds") or 0)
    scenes = list(manifest.get("scenes") or [])
    dips: dict[int, dict] = {}
    for point in points:
        if point.audience_watch_ratio >= threshold:
            continue
        seconds = point.elapsed_ratio * duration
        scene = _scene_at(scenes, seconds)
        if scene is None:
            continue
        section_index = int(scene.get("section_index", 0))
        candidate = {
            **scene,
            "elapsed_seconds": round(seconds, 2),
            "audience_watch_ratio": point.audience_watch_ratio,
            "relative_retention_performance": point.relative_retention_performance,
        }
        current = dips.get(section_index)
        if current is None or candidate["audience_watch_ratio"] < current["audience_watch_ratio"]:
            dips[section_index] = candidate
    return list(dips.values())


async def sync_retention_insights(
    session: Session, *, publication_id: str, provider: YouTubeProvider
) -> list[Insight]:
    publication = session.get(Publication, publication_id)
    if publication is None or not publication.youtube_video_id:
        return []
    manifest_asset = (
        session.query(Asset)
        .filter(
            Asset.video_project_id == publication.video_project_id,
            Asset.role == "scene_manifest",
        )
        .one_or_none()
    )
    if manifest_asset is None or not Path(manifest_asset.file_path).is_file():
        return []
    manifest = json.loads(Path(manifest_asset.file_path).read_text(encoding="utf-8"))
    points = await provider.get_audience_retention(youtube_video_id=publication.youtube_video_id)
    insights: list[Insight] = []
    for dip in correlate_retention_dips(manifest, points):
        section_index = int(dip["section_index"])
        source_ref = f"retention:{publication.id}:section:{section_index}"
        insight = (
            session.query(Insight)
            .filter(
                Insight.source_type == "publication",
                Insight.source_id == publication.id,
                Insight.insight_type == "retention_scene_dip",
                Insight.source_ref == source_ref,
            )
            .one_or_none()
        )
        if insight is None:
            insight = Insight(
                source_type="publication",
                source_id=publication.id,
                insight_type="retention_scene_dip",
                source_ref=source_ref,
                finding="視聴維持率が低い場面があります",
                evidence=dip,
                confidence=0.8,
                recommended_action=(
                    f"{dip.get('visual_type', 'dialogue')}場面の説明量・画面切替・字幕を見直す"
                ),
                human_review_reason="離脱理由は内容と演出の両面があるため人が映像を確認する",
            )
            session.add(insight)
        else:
            insight.evidence = dip
        insights.append(insight)
    session.flush()
    return insights
