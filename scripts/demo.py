"""ローカルデモスクリプト(Phase 7B)。

引数なしで実行可能。SQLite(既存 `DATABASE_URL` を尊重。未設定時は `sqlite:///./demo.db`)
+ Celery eager + 全Fakeプロバイダー(環境変数上書きではなく、明示的にFake実装を構築)で
企画(Topic)スコアリングからInsight生成までの全工程を1回実行し、結果サマリーを表示する。

使い方:
    uv run python scripts/demo.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# `DATABASE_URL`/`CELERY_TASK_ALWAYS_EAGER` は import前に設定する(app.core.config.get_settings
# はプロセス内でキャッシュされるため)。既存の環境変数(既存DATABASE_URL)を尊重し、
# 未設定の場合のみデフォルトを補う。
os.environ.setdefault("DATABASE_URL", "sqlite:///./demo.db")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.orm import Session  # noqa: E402

import app.models  # noqa: E402,F401  metadataにモデルを登録するため import
from app.core.subprocess_util import run_checked  # noqa: E402
from app.models.topic import Topic  # noqa: E402
from app.models.video_project import VideoProject  # noqa: E402
from app.providers.llm.fake import DeterministicFakeLLMProvider  # noqa: E402
from app.providers.tts.fake import FakeTTSProvider  # noqa: E402
from app.providers.youtube.fake import FakeYouTubeProvider  # noqa: E402
from app.services.feedback.insights import summarize_comment_categories  # noqa: E402
from app.services.media.probe import probe_video  # noqa: E402
from app.services.orchestration import PipelineProviders, run_full_pipeline  # noqa: E402
from scripts.seed import seed as seed_demo_data  # noqa: E402

_ALEMBIC_TIMEOUT_SECONDS = 120.0


def _migrate_schema() -> None:
    """`alembic upgrade head` 相当のスキーマ準備(引数配列・shell=False)。"""
    run_checked(
        [sys.executable, "-m", "alembic", "upgrade", "head"], timeout=_ALEMBIC_TIMEOUT_SECONDS
    )


def _print_summary(session: Session, report: object) -> None:
    from app.services.orchestration import PipelineRunReport

    assert isinstance(report, PipelineRunReport)

    topics_count = session.query(Topic).count()
    project = session.get(VideoProject, report.video_project_id)

    print("\n=== デモ実行サマリー ===")
    print(f"Topic数: {topics_count}")
    print(f"Script ID: {report.script_id}")

    if project is not None and project.output_path:
        output_path = Path(project.output_path)
        print(f"動画パス: {output_path} (exists={output_path.exists()})")
        if output_path.exists():
            try:
                probe_result = probe_video(output_path)
                print(
                    "ffprobe結果: "
                    f"duration={probe_result.duration_seconds:.2f}s "
                    f"{probe_result.width}x{probe_result.height} "
                    f"video_codec={probe_result.video_codec} "
                    f"audio_codec={probe_result.audio_codec} "
                    f"has_audio={probe_result.has_audio}"
                )
            except Exception as exc:  # noqa: BLE001 - デモ表示用。失敗しても続行する
                print(f"ffprobe結果: 取得失敗 ({exc})")
    else:
        print("動画パス: (未生成)")

    print(f"VideoProject状態: {report.video_project_status}")
    print(f"レビュー合否: {'合格' if report.review_passed else '不合格/未実施'}")
    print(f"承認: {'あり (id=' + report.approval_id + ')' if report.approval_id else 'なし'}")
    if report.publication_id:
        print(f"Publication: id={report.publication_id} youtube_video_id={report.youtube_video_id}")
    else:
        print("Publication: (未アップロード)")

    print(f"指標行数(VideoMetricDaily): {report.metrics_synced}")

    if report.publication_id:
        category_counts = summarize_comment_categories(
            session, publication_id=report.publication_id
        )
        print(f"コメント数: {report.comments_synced} 分類内訳: {category_counts}")
    else:
        print(f"コメント数: {report.comments_synced}")

    print(f"Insight数: {report.insights_generated}")
    print(f"派生Topic数: {len(report.derived_topic_ids)} (ids={report.derived_topic_ids})")
    if report.skipped_steps:
        print(f"スキップされたステップ: {report.skipped_steps}")


def main() -> None:
    _migrate_schema()

    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        channel, csv_result = seed_demo_data(session)
        print(
            f"channel_id={channel.id} topics_created={csv_result.created} "
            f"topics_skipped={csv_result.skipped}"
        )

        topic = (
            session.query(Topic)
            .filter(Topic.channel_id == channel.id)
            .order_by(Topic.created_at.asc())
            .first()
        )
        if topic is None:
            raise RuntimeError("seed後にTopicが1件も見つかりませんでした")

        providers = PipelineProviders(
            llm=DeterministicFakeLLMProvider(),
            tts=FakeTTSProvider(),
            youtube=FakeYouTubeProvider(),
        )

        report = asyncio.run(
            run_full_pipeline(
                session,
                channel_id=channel.id,
                topic_id=topic.id,
                providers=providers,
            )
        )
        session.commit()

        _print_summary(session, report)
    finally:
        session.close()


if __name__ == "__main__":
    main()
