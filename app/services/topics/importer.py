"""企画(Topic)の取り込み: 手動作成 / CSVインポート(ADR-0004 get-or-create)。"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.topic import Topic

REQUIRED_CSV_COLUMNS = {"title", "description", "source_url"}


class InvalidCsvError(ValueError):
    """CSVの形式が不正な場合。"""


def _get_existing_topic(
    session: Session, channel_id: str, source_type: str, source_ref: str
) -> Topic | None:
    return (
        session.query(Topic)
        .filter(
            Topic.channel_id == channel_id,
            Topic.source_type == source_type,
            Topic.source_ref == source_ref,
        )
        .one_or_none()
    )


def create_manual_topic(
    session: Session,
    *,
    channel_id: str,
    title: str,
    description: str | None,
    client_key: str,
) -> Topic:
    """手動企画の get-or-create。

    同一 `client_key`(クライアント生成キー)で2回呼ばれた場合、2件目は新規作成せず
    既存Topicを返す(ADR-0004)。
    """
    existing = _get_existing_topic(session, channel_id, "manual", client_key)
    if existing is not None:
        return existing

    topic = Topic(
        channel_id=channel_id,
        title=title,
        description=description,
        source_type="manual",
        source_url=None,
        source_ref=client_key,
    )
    try:
        with session.begin_nested():
            session.add(topic)
            session.flush()
    except IntegrityError:
        # 並行実行で他が先に同じclient_keyを取り込んだ -> 既存を返す。
        session.expunge(topic)
        winner = _get_existing_topic(session, channel_id, "manual", client_key)
        if winner is None:  # pragma: no cover - 理論上到達しない防御的分岐
            raise
        return winner
    return topic


def _normalize_row_hash(title: str, description: str, source_url: str) -> str:
    """行の内容から正規化ハッシュ(source_ref)を作る。

    大小文字・前後空白の差異は同一行とみなして重複させないよう正規化する。
    """
    normalized = "␟".join(
        part.strip().lower() for part in (title, description, source_url)
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass
class CsvImportResult:
    created: int
    skipped: int
    total_rows: int


def import_topics_csv(session: Session, *, channel_id: str, csv_text: str) -> CsvImportResult:
    """CSVから企画を取り込む(ヘッダー: title, description, source_url。順不同)。

    行の正規化ハッシュを `source_ref` にすることで、同一CSVの再インポートは
    既存Topicをskipし重複を生まない(ADR-0004)。
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        raise InvalidCsvError("CSVヘッダーがありません")

    header_set = {name.strip() for name in reader.fieldnames}
    missing = REQUIRED_CSV_COLUMNS - header_set
    if missing:
        raise InvalidCsvError(f"CSVに必須列がありません: {sorted(missing)}")

    created = 0
    skipped = 0
    total_rows = 0

    for row in reader:
        total_rows += 1
        title = (row.get("title") or "").strip()
        description = (row.get("description") or "").strip()
        source_url = (row.get("source_url") or "").strip()

        if not title:
            raise InvalidCsvError(f"{total_rows}行目: title が空です")

        source_ref = _normalize_row_hash(title, description, source_url)
        existing = _get_existing_topic(session, channel_id, "csv", source_ref)
        if existing is not None:
            skipped += 1
            continue

        topic = Topic(
            channel_id=channel_id,
            title=title,
            description=description or None,
            source_type="csv",
            source_url=source_url or None,
            source_ref=source_ref,
        )
        try:
            with session.begin_nested():
                session.add(topic)
                session.flush()
        except IntegrityError:
            session.expunge(topic)
            skipped += 1
            continue
        created += 1

    return CsvImportResult(created=created, skipped=skipped, total_rows=total_rows)
