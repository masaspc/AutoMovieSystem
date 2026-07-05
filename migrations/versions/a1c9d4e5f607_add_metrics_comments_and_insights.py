"""add metrics comments and insights

Revision ID: a1c9d4e5f607
Revises: 8f38a5e7cce7
Create Date: 2026-07-05 20:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1c9d4e5f607"
down_revision: Union[str, Sequence[str], None] = "8f38a5e7cce7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "video_metric_daily",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("publication_id", sa.String(length=36), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False),
        sa.Column("watch_minutes", sa.Float(), nullable=False),
        sa.Column("average_view_duration", sa.Float(), nullable=False),
        sa.Column("average_view_percentage", sa.Float(), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False),
        sa.Column("ctr", sa.Float(), nullable=False),
        sa.Column("likes", sa.Integer(), nullable=False),
        sa.Column("comments_count", sa.Integer(), nullable=False),
        sa.Column("subscribers_gained", sa.Integer(), nullable=False),
        sa.Column("subscribers_lost", sa.Integer(), nullable=False),
        sa.Column("estimated_revenue", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publications.id"],
            name=op.f("fk_video_metric_daily_publication_id_publications"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_metric_daily")),
        sa.UniqueConstraint(
            "publication_id", "metric_date", name="uq_video_metric_daily_publication_date"
        ),
    )
    op.create_table(
        "comments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("publication_id", sa.String(length=36), nullable=False),
        sa.Column("youtube_comment_id", sa.String(length=128), nullable=False),
        sa.Column("author_hash", sa.String(length=64), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("like_count", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("sentiment", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("requested_topic", sa.String(length=255), nullable=True),
        sa.Column("requires_response", sa.Boolean(), nullable=False),
        sa.Column("moderation_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["publication_id"], ["publications.id"], name=op.f("fk_comments_publication_id_publications")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comments")),
        sa.UniqueConstraint("youtube_comment_id", name="uq_comments_youtube_comment_id"),
    )
    op.create_table(
        "insights",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("insight_type", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=False),
        sa.Column("finding", sa.String(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("recommended_action", sa.String(), nullable=False),
        sa.Column("human_review_reason", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_insights")),
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            "insight_type",
            "source_ref",
            name="uq_insights_source_type_id_type_ref",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("insights")
    op.drop_table("comments")
    op.drop_table("video_metric_daily")
