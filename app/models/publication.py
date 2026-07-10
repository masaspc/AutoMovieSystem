"""Publication モデル(仕様§6・ADR-0005)。VideoProjectのYouTubeアップロード記録。

アップロードは2段階(`upload_status`: started -> completed/failed)で記録する。
`idempotency_key` UNIQUE により同一VideoProject・同一チェックサムでの再試行は
同一行を再利用する。`youtube_video_id` UNIQUE(NULL許容)により、完了後の
二重投稿は行レベルでも検出できる(ただし主たる二重投稿防止はADR-0005のreconcile)。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow_naive
from app.db.base import Base

PUBLICATION_PRIVACY_STATUSES = ("private", "unlisted", "public")
PUBLICATION_UPLOAD_STATUSES = ("started", "completed", "failed")


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_publications_idempotency_key"),
        UniqueConstraint("youtube_video_id", name="uq_publications_youtube_video_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    video_project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("video_projects.id"), nullable=False
    )

    youtube_video_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    privacy_status: Mapped[str] = mapped_column(String(16), nullable=False, default="private")

    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    upload_status: Mapped[str] = mapped_column(String(16), nullable=False, default="started")
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )
