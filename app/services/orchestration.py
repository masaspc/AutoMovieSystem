"""全工程オーケストレーション(Phase 7B)。

企画(Topic)スコアリングから台本生成・メディア生成・自動レビュー・人間承認・
YouTubeアップロード・指標/コメント同期・Insight生成・派生Topic候補生成までを
既存の各ドメインサービスをそのまま呼び出して実行する薄いオーケストレーター。

新しいドメインロジックはここには実装しない。`VideoProject.status` の変更は
必ず `app/services/state_machine.py` の `transition()` を経由する既存サービス
(または本モジュールの `_advance_status`/`_recover_if_failed`)からのみ行う。

冪等性: 同一 `(channel_id, topic_id)` に対して本関数を複数回呼び出しても、
各ステップは既存サービスのJobRun冪等性キー・get-or-createにより重複実行・
重複レコード作成が起きない(Topic/VideoProject/Script/Publication/Comment/
UsageRecordはいずれも増えない)。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.approval import Approval
from app.models.evidence import Evidence
from app.models.publication import Publication
from app.models.review import Review
from app.models.video_project import VideoProject
from app.providers.llm.base import LLMProvider
from app.providers.tts.base import TTSProvider
from app.providers.youtube.base import CommentData, VideoStatistics, YouTubeProvider
from app.providers.youtube.fake import FakeYouTubeProvider
from app.services.analytics.sync import sync_video_metrics
from app.services.comments.sync import sync_comments
from app.services.feedback.insights import generate_publication_insights
from app.services.media.pipeline import (
    prepare_assets,
    render_video,
    restart_render,
    synthesize_audio,
)
from app.services.publishing.uploader import restart_upload, upload_video
from app.services.reviews.approval import approve
from app.services.reviews.service import restart_review, run_automated_review
from app.services.scripts.generator import generate_script
from app.services.scripts.inspector import inspect_script_with_history
from app.services.state_machine import NORMAL_STATUSES, transition
from app.services.topics.scoring import score_topic

logger = get_logger(__name__)

DEMO_EVIDENCE_SOURCE_URL = "https://example.com/evidence"
DEFAULT_ENDCARD_DURATION_SECONDS = 3.0

_MEDIA_PIPELINE_ENTRY_STATUSES: tuple[str, ...] = ("SCRIPT_REVIEWED", "ASSETS_READY")

# 失敗状態 -> その復旧(巻き戻し)を行う既存サービス関数。
_RECOVERY_ACTIONS: dict[str, Callable[[Session, str], VideoProject]] = {
    "RENDER_FAILED": lambda session, video_project_id: restart_render(
        session, video_project_id=video_project_id
    ),
    "REVIEW_FAILED": lambda session, video_project_id: restart_review(
        session, video_project_id=video_project_id
    ),
    "UPLOAD_FAILED": lambda session, video_project_id: restart_upload(
        session, video_project_id=video_project_id
    ),
}


@dataclass(frozen=True)
class PipelineProviders:
    """`run_full_pipeline` へ渡す外部プロバイダー一式(すべてProtocol経由)。"""

    llm: LLMProvider
    tts: TTSProvider
    youtube: YouTubeProvider


@dataclass
class PipelineRunReport:
    """`run_full_pipeline` の実行結果サマリー。"""

    topic_id: str
    video_project_id: str
    script_id: str | None = None
    evidence_id: str | None = None
    video_project_status: str = ""
    review_passed: bool = False
    approval_id: str | None = None
    publication_id: str | None = None
    youtube_video_id: str | None = None
    metrics_synced: int = 0
    comments_synced: int = 0
    insights_generated: int = 0
    derived_topic_ids: list[str] = field(default_factory=list)
    skipped_steps: list[str] = field(default_factory=list)


def _get_or_create_video_project(
    session: Session, *, topic_id: str, generation: int = 1
) -> VideoProject:
    """`(topic_id, generation)` の get-or-create(ADR-0004準拠)。"""
    existing = (
        session.query(VideoProject)
        .filter(VideoProject.topic_id == topic_id, VideoProject.generation == generation)
        .one_or_none()
    )
    if existing is not None:
        return existing

    project = VideoProject(topic_id=topic_id, generation=generation, status="TOPIC_CREATED")
    session.add(project)
    session.flush()
    return project


def _recover_if_failed(session: Session, project: VideoProject) -> None:
    """現在の状態が失敗状態なら、対応する既存の復旧関数で巻き戻す(再実行=巻き戻し)。"""
    action = _RECOVERY_ACTIONS.get(project.status)
    if action is not None:
        action(session, project.id)
        session.flush()


def _advance_status(project: VideoProject, target: str) -> None:
    """正常系の一本道に沿って `project.status` を `target` まで前進させる(冪等)。

    既に `target` 以降まで進んでいる場合、または現在の状態が正常系でない場合は
    何もしない(失敗状態からの復旧は `_recover_if_failed` の責務)。
    """
    if project.status not in NORMAL_STATUSES or target not in NORMAL_STATUSES:
        return
    target_index = NORMAL_STATUSES.index(target)
    while NORMAL_STATUSES.index(project.status) < target_index:
        next_status = NORMAL_STATUSES[NORMAL_STATUSES.index(project.status) + 1]
        transition(project, next_status)


def _ensure_dummy_evidence(session: Session, *, topic_id: str) -> Evidence:
    """RESEARCH_READY前提のダミーリサーチ根拠を1件get-or-createする。"""
    existing = (
        session.query(Evidence)
        .filter(Evidence.topic_id == topic_id, Evidence.source_url == DEMO_EVIDENCE_SOURCE_URL)
        .one_or_none()
    )
    if existing is not None:
        return existing

    excerpt_hash = hashlib.sha256(f"{topic_id}:{DEMO_EVIDENCE_SOURCE_URL}".encode()).hexdigest()
    evidence = Evidence(
        topic_id=topic_id,
        source_url=DEMO_EVIDENCE_SOURCE_URL,
        source_title="デモ用リサーチ根拠",
        publisher="example.com",
        claim="デモ実行用のダミー根拠クレームです。",
        excerpt_hash=excerpt_hash,
        verification_status="verified",
    )
    session.add(evidence)
    session.flush()
    return evidence


def _latest_reviews_passed(session: Session, video_project_id: str) -> bool:
    machine = (
        session.query(Review)
        .filter(Review.video_project_id == video_project_id, Review.reviewer_type == "machine")
        .order_by(Review.review_version.desc())
        .first()
    )
    content = (
        session.query(Review)
        .filter(Review.video_project_id == video_project_id, Review.reviewer_type == "content")
        .order_by(Review.review_version.desc())
        .first()
    )
    return bool(machine is not None and machine.passed and content is not None and content.passed)


def _existing_approval(session: Session, video_project_id: str) -> Approval | None:
    return (
        session.query(Approval)
        .filter(Approval.video_project_id == video_project_id, Approval.decision == "approved")
        .first()
    )


_SAMPLE_COMMENTS: tuple[tuple[str, str], ...] = (
    # 同一内容の次回企画要望を3件(NEXT_TOPIC_REQUEST閾値=3)投入し、
    # コメント由来Insight+派生Topic候補が生成されることを確認できるようにする。
    ("demo-comment-1", "次回はRustの所有権システムについて解説してほしいです"),
    ("demo-comment-2", "次回はRustの所有権システムについて解説してほしいです"),
    ("demo-comment-3", "次回はRustの所有権システムについて解説してほしいです"),
    ("demo-comment-4", "とても分かりやすかったです。ありがとうございます"),
    ("demo-comment-5", "この機能はどうやって使うんですか?"),
)


def _seed_youtube_fake(provider: YouTubeProvider, *, youtube_video_id: str) -> None:
    """FakeYouTubeProviderへ決定的な統計・コメントをシードする(実APIには存在しない拡張)。"""
    if not isinstance(provider, FakeYouTubeProvider):
        return

    provider.seed_statistics(
        VideoStatistics(
            youtube_video_id=youtube_video_id,
            view_count=1_200,
            like_count=85,
            comment_count=len(_SAMPLE_COMMENTS),
            collected_at=datetime.now(UTC),
        )
    )

    base_time = datetime.now(UTC)
    comments = [
        CommentData(
            youtube_comment_id=f"{youtube_video_id}:{comment_id}",
            author_display_name=f"viewer-{index}",
            text=text,
            published_at=base_time - timedelta(minutes=index),
            like_count=index,
        )
        for index, (comment_id, text) in enumerate(_SAMPLE_COMMENTS)
    ]
    provider.seed_comments(youtube_video_id, comments)


async def run_full_pipeline(
    session: Session,
    *,
    channel_id: str,
    topic_id: str,
    providers: PipelineProviders,
    approve_by: str = "demo-operator",
    metrics_days: int = 2,
) -> PipelineRunReport:
    """企画(Topic)からInsight生成までの全工程を実行する(冪等・既存サービスをそのまま呼ぶ)。"""
    del channel_id  # Topic.channel_idで既知。将来の権限検証拡張用に引数として残す。

    skipped_steps: list[str] = []

    # 1. スコアリング(冪等: JobRun idempotency_key="score_topic:{topic_id}")
    topic = score_topic(session, topic_id)

    # 2. VideoProject get-or-create + 失敗状態からの復旧
    project = _get_or_create_video_project(session, topic_id=topic.id)
    _recover_if_failed(session, project)

    # TOPIC_CREATED -> TOPIC_SCORED -> RESEARCH_READY(Evidence登録)
    _advance_status(project, "TOPIC_SCORED")
    evidence = _ensure_dummy_evidence(session, topic_id=topic.id)
    _advance_status(project, "RESEARCH_READY")
    session.flush()

    # 3. 台本生成(冪等) -> SCRIPT_GENERATED -> 検査通過で SCRIPT_REVIEWED
    script = await generate_script(session, topic_id=topic.id, provider=providers.llm)
    if project.script_id is None:
        project.script_id = script.id
    _advance_status(project, "SCRIPT_GENERATED")

    findings = inspect_script_with_history(session, script)
    blocking_findings = [f for f in findings if f.severity == "blocking"]
    if blocking_findings:
        skipped_steps.append("script_review_blocking_findings")
    else:
        if script.status == "draft":
            script.status = "reviewed"
        _advance_status(project, "SCRIPT_REVIEWED")
    session.flush()
    session.commit()

    # 4. アセット準備 + TTS音声合成 + FFmpegレンダリング(いずれも冪等)
    if project.status in _MEDIA_PIPELINE_ENTRY_STATUSES:
        prepare_assets(session, video_project_id=project.id)
        session.commit()

        audio_assets = await synthesize_audio(
            session, video_project_id=project.id, provider=providers.tts
        )
        session.commit()

        if project.target_duration_seconds is None:
            total_audio_seconds = sum(
                float((a.meta or {}).get("duration_seconds", 0.0)) for a in audio_assets
            )
            project.target_duration_seconds = round(
                total_audio_seconds + DEFAULT_ENDCARD_DURATION_SECONDS
            )
            session.flush()
            session.commit()

        render_video(session, video_project_id=project.id)
        session.commit()
    else:
        skipped_steps.append("media_pipeline_skipped")

    # 5. 自動レビュー(machine+content。冪等: checksumベースのidempotency_key)
    if project.status == "VIDEO_RENDERED":
        await run_automated_review(session, video_project_id=project.id, provider=providers.llm)
        session.commit()
    else:
        skipped_steps.append("automated_review_skipped")

    review_passed = _latest_reviews_passed(session, project.id)

    # 6. 人間承認(既にApprovalがあれば再承認しない)
    approval = _existing_approval(session, project.id)
    if approval is None:
        if project.status == "AUTOMATED_REVIEW_PASSED":
            approve(session, video_project_id=project.id, decided_by=approve_by)
            session.commit()
            approval = _existing_approval(session, project.id)
        else:
            skipped_steps.append("approval_skipped_review_not_passed")

    # 7. YouTubeアップロード(Fake、private。冪等: checksumベースのidempotency_key)
    publication: Publication | None = None
    if project.checksum and project.output_path:
        publication = await upload_video(
            session, video_project_id=project.id, provider=providers.youtube
        )
        session.commit()
    else:
        skipped_steps.append("upload_skipped_render_incomplete")

    # 8. FakeYouTubeへ統計・コメントをシード(次回企画要望を含む決定的サンプル)
    if publication is not None and publication.youtube_video_id:
        _seed_youtube_fake(providers.youtube, youtube_video_id=publication.youtube_video_id)

    # 9. 指標同期 + コメント同期/分類 + Insight生成(+ コメント由来Topic候補生成)
    metrics_synced = 0
    comments_synced: list = []
    insights_generated: list = []
    if publication is not None and publication.youtube_video_id:
        today = datetime.now(UTC).date()
        for day_offset in range(max(1, metrics_days)):
            metric_date = today - timedelta(days=day_offset)
            await sync_video_metrics(
                session,
                publication_id=publication.id,
                provider=providers.youtube,
                metric_date=metric_date,
            )
            metrics_synced += 1
        session.commit()

        comments_synced = await sync_comments(
            session, publication_id=publication.id, provider=providers.youtube
        )
        session.commit()

        insights_generated = generate_publication_insights(session, publication_id=publication.id)
        session.commit()
    else:
        skipped_steps.append("feedback_sync_skipped_not_uploaded")

    derived_topic_ids = sorted(
        {
            insight.evidence.get("topic_id")
            for insight in insights_generated
            if insight.evidence.get("topic_id")
        }
    )

    logger.info(
        "pipeline_run_completed",
        topic_id=topic.id,
        video_project_id=project.id,
        video_project_status=project.status,
        skipped_steps=skipped_steps,
    )

    return PipelineRunReport(
        topic_id=topic.id,
        video_project_id=project.id,
        script_id=script.id,
        evidence_id=evidence.id,
        video_project_status=project.status,
        review_passed=review_passed,
        approval_id=approval.id if approval is not None else None,
        publication_id=publication.id if publication is not None else None,
        youtube_video_id=publication.youtube_video_id if publication is not None else None,
        metrics_synced=metrics_synced,
        comments_synced=len(comments_synced),
        insights_generated=len(insights_generated),
        derived_topic_ids=list(derived_topic_ids),
        skipped_steps=skipped_steps,
    )
