"""Comment モデル(仕様§6・§14)。YouTubeコメントの差分同期と分類結果。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

COMMENT_CATEGORIES = (
    "QUESTION",
    "NEXT_TOPIC_REQUEST",
    "PROBLEM_REPORT",
    "CORRECTION",
    "COMPARISON_REQUEST",
    "POSITIVE",
    "NEGATIVE",
    "SPAM",
    "RIGHTS_REQUEST",
    "URGENT",
    "OTHER",
)


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        UniqueConstraint("youtube_comment_id", name="uq_comments_youtube_comment_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    publication_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("publications.id"), nullable=False
    )
    youtube_comment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    author_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    category: Mapped[str] = mapped_column(String(32), nullable=False, default="OTHER")
    sentiment: Mapped[str] = mapped_column(String(16), nullable=False, default="neutral")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requested_topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requires_response: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    moderation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="visible")

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
