"""ローカルデモスクリプト(Phase 7B)。

引数なしで実行可能。既定では運用DBを汚染しないよう `DATABASE_URL` を
`sqlite:///./demo.db` へ強制上書きする(修正4)。既存の(.env含む)`DATABASE_URL` を
尊重したい場合のみ環境変数 `DEMO_USE_CURRENT_DB=1` を明示すること。
Celery eager + 全Fakeプロバイダー(環境変数上書きではなく、明示的にFake実装を構築)で
企画(Topic)スコアリングからInsight生成までの全工程を1回実行し、結果サマリーを表示する。

使い方:
    uv run python scripts/demo.py
    DEMO_USE_CURRENT_DB=1 uv run python scripts/demo.py  # 既存DATABASE_URLを尊重する場合
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

_DEMO_DEFAULT_DATABASE_URL = "sqlite:///./demo.db"

# `DATABASE_URL`/`CELERY_TASK_ALWAYS_EAGER` は import前に設定する(app.core.config.get_settings
# はプロセス内でキャッシュされるため)。DEMO_USE_CURRENT_DB=1 が明示されている場合のみ
# 既存の環境変数(.env含む)を尊重する。それ以外は運用DB誤汚染を防ぐため常に
# demo.db へ強制上書きする(修正4)。
_use_current_db = os.environ.get("DEMO_USE_CURRENT_DB") == "1"
if not _use_current_db:
    # 明示指定がない限り、.env含む既存のDATABASE_URLより常に優先して上書きする
    # (pydantic-settingsは環境変数を.envより優先するため、この上書きは確実に効く)。
    os.environ["DATABASE_URL"] = _DEMO_DEFAULT_DATABASE_URL
os.environ.setdefault("DATABASE_URL", _DEMO_DEFAULT_DATABASE_URL)
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")


def _mask_database_url(url: str) -> str:
    """接続文字列内の認証情報(user:password@)をマスクする(シークレットをログに出さない)。"""
    return re.sub(r"//([^:/@]+):([^@/]+)@", r"//\1:***@", url)


def _print_database_url_banner() -> None:
    resolved_url = os.environ.get("DATABASE_URL", _DEMO_DEFAULT_DATABASE_URL)
    print(f"接続先DB: {_mask_database_url(resolved_url)}")
    if _use_current_db:
        print("DEMO_USE_CURRENT_DB=1 が指定されたため、既存のDATABASE_URL(.env含む)を使用します。")
    else:
        print(
            f"DATABASE_URLを {_mask_database_url(resolved_url)} へ強制上書きしました"
            "(運用DB誤汚染防止。既存DBを使う場合はDEMO_USE_CURRENT_DB=1を指定してください)。"
        )


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
    _print_database_url_banner()
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
