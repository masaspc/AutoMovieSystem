"""LLMCache モデル。

`(operation, prompt_version, input_hash)` をキーにLLM構造化応答をキャッシュする。
ヒット時はプロバイダーを呼ばず、UsageRecordも増やさない(二重計上防止。
`app/services/llm_gateway.py` 参照)。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import utcnow_naive
from app.db.base import Base


class LLMCache(Base):
    __tablename__ = "llm_caches"
    __table_args__ = (
        UniqueConstraint(
            "operation",
            "prompt_version",
            "input_hash",
            name="uq_llm_caches_operation_version_hash",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    # StructuredLLMResult.data をJSON文字列としてそのまま保存する。
    response_json: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
