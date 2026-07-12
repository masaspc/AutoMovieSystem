"""add series and episode plans

Revision ID: 9b21f86d4c10
Revises: 3800fe4e4532
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b21f86d4c10"
down_revision: str | Sequence[str] | None = "3800fe4e4532"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "series_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("channel_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("target_audience", sa.String(), nullable=False),
        sa.Column("starting_knowledge", sa.String(), nullable=False),
        sa.Column("final_goal", sa.String(), nullable=False),
        sa.Column("series_prompt", sa.String(), nullable=False, server_default=""),
        sa.Column("shared_rules", sa.String(), nullable=False, server_default=""),
        sa.Column("technology_version", sa.String(128), nullable=False),
        sa.Column("development_environment", sa.String(255), nullable=False),
        sa.Column("planned_episode_count", sa.Integer(), nullable=False),
        sa.Column("curriculum_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"]),
        sa.UniqueConstraint("channel_id", "name", name="uq_series_plans_channel_name"),
    )
    op.create_table(
        "episode_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("series_plan_id", sa.String(36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("learning_objectives", sa.JSON(), nullable=False),
        sa.Column("prerequisite_positions", sa.JSON(), nullable=False),
        sa.Column("new_concepts", sa.JSON(), nullable=False),
        sa.Column("review_concepts", sa.JSON(), nullable=False),
        sa.Column("excluded_concepts", sa.JSON(), nullable=False),
        sa.Column("demo_outline", sa.String(), nullable=False),
        sa.Column("exercise_outline", sa.String(), nullable=False),
        sa.Column("next_episode_bridge", sa.String(), nullable=False),
        sa.Column("target_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("topic_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["series_plan_id"], ["series_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"]),
        sa.UniqueConstraint("series_plan_id", "position", name="uq_episode_plans_series_position"),
        sa.UniqueConstraint("topic_id", name="uq_episode_plans_topic_id"),
    )


def downgrade() -> None:
    op.drop_table("episode_plans")
    op.drop_table("series_plans")
