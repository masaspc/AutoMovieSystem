"""デモ/開発環境向けシードスクリプト(Phase 7B)。

`デモチャンネル` 1件 + `sample_data/topics_sample.csv` の企画データを取り込む。
Channel は名前一致で get-or-create、Topic は既存の
`app.services.topics.importer.import_topics_csv`(行ハッシュによるget-or-create、
ADR-0004)を使うため、複数回実行しても重複作成しない。

使い方:
    uv run python scripts/seed.py
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

import app.models  # noqa: F401  metadataにモデルを登録するため import
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.channel import Channel
from app.services.topics.importer import CsvImportResult, import_topics_csv

DEMO_CHANNEL_NAME = "デモチャンネル"
SAMPLE_CSV_PATH = Path(__file__).resolve().parent.parent / "sample_data" / "topics_sample.csv"


def get_or_create_demo_channel(session: Session) -> Channel:
    """`デモチャンネル` の get-or-create(名前一致)。"""
    existing = session.query(Channel).filter(Channel.name == DEMO_CHANNEL_NAME).one_or_none()
    if existing is not None:
        return existing

    channel = Channel(name=DEMO_CHANNEL_NAME, default_privacy_status="private")
    session.add(channel)
    session.flush()
    return channel


def seed(session: Session, *, csv_path: Path = SAMPLE_CSV_PATH) -> tuple[Channel, CsvImportResult]:
    """デモチャンネル+サンプル企画CSVを取り込む(冪等)。"""
    channel = get_or_create_demo_channel(session)
    csv_text = csv_path.read_text(encoding="utf-8")
    result = import_topics_csv(session, channel_id=channel.id, csv_text=csv_text)
    session.commit()
    return channel, result


def main() -> None:
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        channel, result = seed(session)
        print(f"channel_id={channel.id} name={channel.name}")
        print(
            f"topics_total_rows={result.total_rows} "
            f"topics_created={result.created} topics_skipped={result.skipped}"
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
