"""VideoProject.status の状態機械(docs/architecture.md 状態遷移 / ADR-0006準拠)。

`transition(project, to_state)` のみが `VideoProject.status` を変更できる。
遷移表外の変更は `InvalidTransitionError` を送出する。

正常系(順方向のみ、飛び越え禁止):
    TOPIC_CREATED -> TOPIC_SCORED -> RESEARCH_READY -> SCRIPT_GENERATED
    -> SCRIPT_REVIEWED -> ASSETS_READY -> VIDEO_RENDERED -> AUTOMATED_REVIEW_PASSED
    -> HUMAN_APPROVED -> UPLOAD_READY -> UPLOADED_PRIVATE -> SCHEDULED
    -> PUBLISHED -> METRICS_COLLECTING -> FEEDBACK_GENERATED

失敗状態は対応する処理の直前の正常状態からのみ遷移可能で、復旧エッジ
(失敗状態 -> その直前の正常状態)を遷移表に明示する(ADR-0006)。

追加エッジ:
- `REJECTED`: `AUTOMATED_REVIEW_PASSED` からの人間却下先(終端)。
- `UPLOADED_PRIVATE` は準終端。`SCHEDULED` を経由せず `PUBLISHED` へ直接進む
  即時公開エッジも許可する。

未対応: ADR-0006 は予算超過保留の `BUDGET_HELD` 状態にも言及しているが、本フェーズ
(Phase 3: メディアパイプライン)の実装スコープには含まれていない。導入するかは
主任判断事項(Phase 2Bの予算制御とVideoProjectの結合方法を含め要検討)。
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.video_project import VideoProject

logger = get_logger(__name__)

# 正常系状態(順方向一本道)。
NORMAL_STATUSES: tuple[str, ...] = (
    "TOPIC_CREATED",
    "TOPIC_SCORED",
    "RESEARCH_READY",
    "SCRIPT_GENERATED",
    "SCRIPT_REVIEWED",
    "ASSETS_READY",
    "VIDEO_RENDERED",
    "AUTOMATED_REVIEW_PASSED",
    "HUMAN_APPROVED",
    "UPLOAD_READY",
    "UPLOADED_PRIVATE",
    "SCHEDULED",
    "PUBLISHED",
    "METRICS_COLLECTING",
    "FEEDBACK_GENERATED",
)

# 失敗状態。
FAILED_STATUSES: tuple[str, ...] = (
    "RESEARCH_FAILED",
    "SCRIPT_FAILED",
    "ASSET_FAILED",
    "RENDER_FAILED",
    "REVIEW_FAILED",
    "UPLOAD_FAILED",
    "METRICS_FAILED",
)

# 終端状態(人間却下)。
TERMINAL_STATUSES: tuple[str, ...] = ("REJECTED",)

ALL_STATUSES: tuple[str, ...] = NORMAL_STATUSES + FAILED_STATUSES + TERMINAL_STATUSES

# 失敗状態 -> その処理の入力だった直前の正常状態(復旧エッジの宛先)。
_FAILURE_RECOVERY_TARGET: dict[str, str] = {
    "RESEARCH_FAILED": "TOPIC_SCORED",
    "SCRIPT_FAILED": "RESEARCH_READY",
    "ASSET_FAILED": "SCRIPT_REVIEWED",
    "RENDER_FAILED": "ASSETS_READY",
    "REVIEW_FAILED": "VIDEO_RENDERED",
    "UPLOAD_FAILED": "UPLOAD_READY",
    "METRICS_FAILED": "PUBLISHED",
}


def _build_transition_table() -> dict[str, frozenset[str]]:
    table: dict[str, set[str]] = {status: set() for status in ALL_STATUSES}

    # 正常系: 一本道の順方向エッジ。
    for current_state, next_state in zip(NORMAL_STATUSES, NORMAL_STATUSES[1:], strict=False):
        table[current_state].add(next_state)

    # UPLOADED_PRIVATE は準終端。SCHEDULEDを経由しない即時公開エッジも許可する。
    table["UPLOADED_PRIVATE"].add("PUBLISHED")

    # 失敗エッジ: 直前の正常状態 -> 対応する失敗状態。
    for failed_state, recovery_target in _FAILURE_RECOVERY_TARGET.items():
        table[recovery_target].add(failed_state)
        # 復旧エッジ: 失敗状態 -> その処理の入力だった直前の正常状態。
        table[failed_state].add(recovery_target)

    # 人間却下: AUTOMATED_REVIEW_PASSED -> REJECTED(終端)。
    table["AUTOMATED_REVIEW_PASSED"].add("REJECTED")

    return {status: frozenset(targets) for status, targets in table.items()}


TRANSITION_TABLE: dict[str, frozenset[str]] = _build_transition_table()

# モジュールロード時に全状態が遷移表に存在することを検証する。
_missing_states = [status for status in ALL_STATUSES if status not in TRANSITION_TABLE]
if _missing_states:  # pragma: no cover - 設定ミスの防御的検証
    raise RuntimeError(f"遷移表に存在しない状態があります: {_missing_states}")


class InvalidTransitionError(ValueError):
    """遷移表に存在しない状態遷移が要求された場合。"""

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"許可されていない状態遷移です: {from_state} -> {to_state}")


class UnknownStateError(ValueError):
    """未知の状態が指定された場合。"""


def get_allowed_transitions(from_state: str) -> frozenset[str]:
    """`from_state` から遷移可能な状態集合を返す。"""
    if from_state not in TRANSITION_TABLE:
        raise UnknownStateError(f"未知の状態です: {from_state}")
    return TRANSITION_TABLE[from_state]


def transition(project: VideoProject, to_state: str) -> VideoProject:
    """`project.status` を `to_state` へ遷移させる(遷移表のみが status を変更できる)。

    Args:
        project: 対象の VideoProject。
        to_state: 遷移先の状態。

    Raises:
        UnknownStateError: `project.status` または `to_state` が未知の状態の場合。
        InvalidTransitionError: 遷移表に存在しない遷移が要求された場合。
    """
    current_state = project.status
    if current_state not in TRANSITION_TABLE:
        raise UnknownStateError(f"未知の状態です: {current_state}")
    if to_state not in ALL_STATUSES:
        raise UnknownStateError(f"未知の状態です: {to_state}")

    allowed = TRANSITION_TABLE[current_state]
    if to_state not in allowed:
        raise InvalidTransitionError(current_state, to_state)

    project.status = to_state
    return project


def apply_failure_transition_in_new_session(*, video_project_id: str, to_state: str) -> None:
    """D-017: Celeryタスクラッパーの except 節専用ヘルパー。

    サービス層内で行われた失敗状態遷移(例: RENDER_FAILED)は呼び出し元セッションの
    flushのみで、タスクラッパーが `session.rollback()` すると消える。本関数は
    **新規セッション・新規トランザクション**で現在の `VideoProject.status` を読み直し、
    遷移表上 `to_state` へ遷移可能な場合のみ適用してcommitする。遷移元状態が既に
    想定と異なる(二重適用・不整合)場合は何もせずログのみ出す(fail-safe)。
    """
    from app.db.session import SessionLocal  # 遅延import(テストでの差し替えを可能にする)

    new_session = SessionLocal()
    try:
        project = new_session.get(VideoProject, video_project_id)
        if project is None:
            logger.warning(
                "failure_transition_video_project_not_found",
                video_project_id=video_project_id,
                to_state=to_state,
            )
            return
        try:
            transition(project, to_state)
        except (InvalidTransitionError, UnknownStateError) as exc:
            logger.info(
                "failure_transition_skipped_current_state_mismatch",
                video_project_id=video_project_id,
                to_state=to_state,
                current_state=project.status,
                error=str(exc),
            )
            return
        new_session.commit()
        logger.info(
            "failure_transition_applied_in_new_session",
            video_project_id=video_project_id,
            to_state=to_state,
        )
    except Exception:
        new_session.rollback()
        raise
    finally:
        new_session.close()
