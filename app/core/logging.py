"""structlog によるJSON構造化ログ設定 + シークレットマスキング。"""

from __future__ import annotations

import logging
import re
from typing import Any

import structlog
from structlog.types import EventDict, Processor

# キー名にこれらの語を含む場合、値全体をマスクする(大小文字無視)。
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(token|secret|key|password|passwd|authorization|cookie|api[_-]?key|access[_-]?token|refresh[_-]?token)",
    re.IGNORECASE,
)

# 値の中に埋め込まれた既知のシークレット形式(APIキー等)を検知して置換する。
_SENSITIVE_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{8,}", re.IGNORECASE),
    re.compile(r"ya29\.[A-Za-z0-9_-]{8,}"),  # Google OAuth access token prefix
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    # DSN内の認証情報(例: postgresql://user:pass@host)。user:pass部分のみマスク。
    re.compile(r"(?<=://)[^:/@\s]+:[^@\s]+(?=@)"),
]

_MASK = "***"


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        masked = value
        for pattern in _SENSITIVE_VALUE_PATTERNS:
            masked = pattern.sub(_MASK, masked)
        return masked
    if isinstance(value, dict):
        return {
            k: _MASK if _SENSITIVE_KEY_PATTERN.search(str(k)) else _mask_value(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_mask_value(v) for v in value)
    return value


def mask_secrets_in_text(text: str) -> str:
    """プレーンな文字列中の既知シークレットパターンをマスクする。

    `last_error` 等、structlog経由ではなくDBへ直接保存する文字列に対して使う
    (シークレットをログ・DBへ残さないためのセキュリティ要点対応)。
    """
    masked = _mask_value(text)
    return masked if isinstance(masked, str) else text


def mask_secrets_processor(_logger: Any, _method_name: str, event_dict: EventDict) -> EventDict:
    """イベント辞書内のシークレットらしき値をマスクするstructlogプロセッサ。"""
    for key, value in list(event_dict.items()):
        if _SENSITIVE_KEY_PATTERN.search(str(key)):
            event_dict[key] = _MASK
            continue
        event_dict[key] = _mask_value(value)
    return event_dict


def configure_logging(json_logs: bool = True) -> None:
    """structlog + 標準loggingを構造化JSON出力で初期化する。"""
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        mask_secrets_processor,
    ]

    renderer: Processor = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
