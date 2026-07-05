"""OAuthToken モデル(仕様§6)。YouTube OAuth 2.0 リフレッシュトークンの暗号化保存。

`encrypted_refresh_token`/`encrypted_access_token` は必ず `app/core/crypto.py`
(Fernet, `SECRET_ENCRYPTION_KEY` 由来の鍵)で暗号化した文字列を保存する。
平文トークンを直接この列に書き込んではならない。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="youtube")
    channel_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("channels.id"), nullable=True
    )

    encrypted_refresh_token: Mapped[str] = mapped_column(String, nullable=False)
    encrypted_access_token: Mapped[str | None] = mapped_column(String, nullable=True)
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
