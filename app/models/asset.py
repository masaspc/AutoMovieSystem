"""Asset モデル(仕様§6)。VideoProjectに紐づく生成物(音声・字幕・画像等)。

`metadata` は SQLAlchemy Declarative の予約属性名のため、列名は `meta` にする。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

ASSET_TYPES = ("audio", "subtitle", "image", "bgm", "endcard", "other")
ASSET_SOURCES = ("generated", "upload", "stock")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    video_project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("video_projects.id"), nullable=False
    )

    asset_type: Mapped[str] = mapped_column(String(16), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="generated")
    license: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
