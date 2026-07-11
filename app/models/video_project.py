"""VideoProject モデル(仕様§6)。Topicから生成される動画1本分(世代管理あり)。

`(topic_id, generation)` UNIQUE により1 Topicから複数世代の作り直しに対応する
(MVPは generation=1 固定運用: D-010/ADR-0004)。`status` は
`app/services/state_machine.py` の遷移表経由でのみ変更すること。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow_naive
from app.db.base import Base

ASPECT_RATIOS = ("16:9", "9:16")


class VideoProject(Base):
    __tablename__ = "video_projects"
    __table_args__ = (
        UniqueConstraint("topic_id", "generation", name="uq_video_projects_topic_generation"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    topic_id: Mapped[str] = mapped_column(String(36), ForeignKey("topics.id"), nullable=False)
    script_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scripts.id"), nullable=True
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="TOPIC_CREATED")
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    template_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    aspect_ratio: Mapped[str] = mapped_column(String(8), nullable=False, default="16:9")
    # TTS実測から確定した期待尺(秒)。レビューの尺検査(app/services/reviews/machine.py)に
    # 使用する。`app/services/media/pipeline.py` の synthesize_audio が音声Asset合計秒数+
    # エンドカード秒で常に上書き設定する。ユーザー希望尺は production_settings 内の
    # target_duration_seconds を参照すること(責務が異なる)。
    target_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ユーザーが選んだ制作方針一式(尺プリセット・台本テンプレート等)。
    # `app.schemas.production_settings.ProductionSettings` を `model_dump()` して保存する。
    # SQLite互換のため `sqlalchemy.JSON` を使う(JSONB等PG専用型は使わない)。
    production_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    output_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )
