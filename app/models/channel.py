"""Channel モデル(仕様§6)。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow_naive
from app.db.base import Base


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    youtube_channel_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="ja")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Tokyo")
    default_privacy_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="private"
    )
    auto_publish_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )
