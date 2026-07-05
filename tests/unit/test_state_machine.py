from __future__ import annotations

import pytest

from app.models.video_project import VideoProject
from app.services.state_machine import (
    ALL_STATUSES,
    FAILED_STATUSES,
    NORMAL_STATUSES,
    TRANSITION_TABLE,
    InvalidTransitionError,
    UnknownStateError,
    get_allowed_transitions,
    transition,
)


def _project(status: str) -> VideoProject:
    project = VideoProject(topic_id="topic-1", status=status)
    return project


def test_all_states_present_in_transition_table() -> None:
    """全状態が遷移表に存在すること(モジュールロード時検証と整合)。"""
    for status in ALL_STATUSES:
        assert status in TRANSITION_TABLE


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    list(zip(NORMAL_STATUSES, NORMAL_STATUSES[1:], strict=False)),
)
def test_normal_forward_edges_are_allowed(from_state: str, to_state: str) -> None:
    project = _project(from_state)
    transition(project, to_state)
    assert project.status == to_state


def test_uploaded_private_can_go_directly_to_published() -> None:
    """UPLOADED_PRIVATEは準終端。SCHEDULEDを経由しない即時公開エッジも許可する。"""
    project = _project("UPLOADED_PRIVATE")
    transition(project, "PUBLISHED")
    assert project.status == "PUBLISHED"


def test_uploaded_private_allows_both_scheduled_and_published() -> None:
    """UPLOADED_PRIVATEは準終端。SCHEDULED経由・即時公開の両方を許可する。"""
    allowed = get_allowed_transitions("UPLOADED_PRIVATE")
    assert allowed == frozenset({"SCHEDULED", "PUBLISHED"})


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        ("TOPIC_CREATED", "RESEARCH_READY"),  # 飛び越え
        ("TOPIC_CREATED", "SCRIPT_GENERATED"),
        ("RESEARCH_READY", "ASSETS_READY"),
        ("SCHEDULED", "TOPIC_CREATED"),  # 逆行
        ("PUBLISHED", "SCRIPT_REVIEWED"),
        ("FEEDBACK_GENERATED", "PUBLISHED"),  # 終端からの遷移
    ],
)
def test_skipping_states_is_rejected(from_state: str, to_state: str) -> None:
    project = _project(from_state)
    with pytest.raises(InvalidTransitionError):
        transition(project, to_state)
    assert project.status == from_state  # 失敗時はstatusが変更されない


@pytest.mark.parametrize(
    ("failed_state", "preceding_normal_state"),
    [
        ("RESEARCH_FAILED", "TOPIC_SCORED"),
        ("SCRIPT_FAILED", "RESEARCH_READY"),
        ("ASSET_FAILED", "SCRIPT_REVIEWED"),
        ("RENDER_FAILED", "ASSETS_READY"),
        ("REVIEW_FAILED", "VIDEO_RENDERED"),
        ("UPLOAD_FAILED", "UPLOAD_READY"),
        ("METRICS_FAILED", "PUBLISHED"),
    ],
)
def test_failure_edges_and_recovery_edges(failed_state: str, preceding_normal_state: str) -> None:
    """直前の正常状態->失敗状態(失敗エッジ)、失敗状態->直前の正常状態(復旧エッジ)。"""
    project = _project(preceding_normal_state)
    transition(project, failed_state)
    assert project.status == failed_state

    transition(project, preceding_normal_state)
    assert project.status == preceding_normal_state


def test_all_failed_states_covered_by_recovery_test() -> None:
    tested = {
        "RESEARCH_FAILED",
        "SCRIPT_FAILED",
        "ASSET_FAILED",
        "RENDER_FAILED",
        "REVIEW_FAILED",
        "UPLOAD_FAILED",
        "METRICS_FAILED",
    }
    assert tested == set(FAILED_STATUSES)


def test_rejected_from_automated_review_passed_is_terminal() -> None:
    project = _project("AUTOMATED_REVIEW_PASSED")
    transition(project, "REJECTED")
    assert project.status == "REJECTED"

    with pytest.raises(InvalidTransitionError):
        transition(project, "HUMAN_APPROVED")


def test_reject_not_allowed_from_other_states() -> None:
    project = _project("VIDEO_RENDERED")
    with pytest.raises(InvalidTransitionError):
        transition(project, "REJECTED")


def test_unknown_from_state_raises() -> None:
    project = _project("NOT_A_REAL_STATE")
    with pytest.raises(UnknownStateError):
        transition(project, "TOPIC_SCORED")


def test_unknown_to_state_raises() -> None:
    project = _project("TOPIC_CREATED")
    with pytest.raises(UnknownStateError):
        transition(project, "NOT_A_REAL_STATE")


def test_get_allowed_transitions_unknown_state_raises() -> None:
    with pytest.raises(UnknownStateError):
        get_allowed_transitions("NOT_A_REAL_STATE")
