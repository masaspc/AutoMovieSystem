"""YouTubeコメントの差分同期と分類(Phase 6 MVP)。"""

from __future__ import annotations

import hashlib

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.comment import Comment
from app.models.publication import Publication
from app.providers.youtube.base import CommentData, YouTubeProvider
from app.services.comments.classifier import classify_comment


class PublicationNotFoundError(ValueError):
    """Publication が存在しない場合。"""


class PublicationNotUploadedError(ValueError):
    """youtube_video_id が未設定の場合。"""


def _author_hash(author_display_name: str) -> str:
    return hashlib.sha256(author_display_name.strip().lower().encode("utf-8")).hexdigest()


def _upsert_comment(session: Session, publication_id: str, item: CommentData) -> Comment:
    existing = (
        session.query(Comment)
        .filter(Comment.youtube_comment_id == item.youtube_comment_id)
        .one_or_none()
    )
    if existing is None:
        comment = Comment(
            publication_id=publication_id,
            youtube_comment_id=item.youtube_comment_id,
            author_hash=_author_hash(item.author_display_name),
            text=item.text,
            published_at=item.published_at,
            like_count=item.like_count,
        )
        try:
            with session.begin_nested():
                session.add(comment)
                session.flush()
        except IntegrityError:
            session.expunge(comment)
            comment = (
                session.query(Comment)
                .filter(Comment.youtube_comment_id == item.youtube_comment_id)
                .one()
            )
    else:
        comment = existing
        comment.text = item.text
        comment.like_count = item.like_count
        comment.published_at = item.published_at
        comment.moderation_status = "visible"

    classify_comment(comment)
    return comment


async def sync_comments(
    session: Session, *, publication_id: str, provider: YouTubeProvider
) -> list[Comment]:
    """YouTubeコメントを全ページ取得し、youtube_comment_idでupsertする。

    全ページ取得後、今回のレスポンスに含まれなかった既存コメントは
    `moderation_status="deleted"` として保持する。行は消さず、過去Insightの根拠を
    追跡できるようにする。
    """
    publication = session.get(Publication, publication_id)
    if publication is None:
        raise PublicationNotFoundError(f"Publication not found: {publication_id}")
    if not publication.youtube_video_id:
        raise PublicationNotUploadedError(f"Publication has no youtube_video_id: {publication_id}")

    synced: list[Comment] = []
    seen_comment_ids: set[str] = set()
    page_token: str | None = None
    while True:
        items, page_token = await provider.list_comments(
            youtube_video_id=publication.youtube_video_id, page_token=page_token
        )
        for item in items:
            seen_comment_ids.add(item.youtube_comment_id)
            synced.append(_upsert_comment(session, publication_id, item))
        if page_token is None:
            break

    stale_query = session.query(Comment).filter(Comment.publication_id == publication_id)
    if seen_comment_ids:
        stale_query = stale_query.filter(Comment.youtube_comment_id.notin_(seen_comment_ids))
    for stale_comment in stale_query.all():
        stale_comment.moderation_status = "deleted"

    session.flush()
    return synced
