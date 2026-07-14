"""YouTubeアップロードサービス(仕様§12・ADR-0005)。

`HUMAN_APPROVED` を経た `UPLOAD_READY` の VideoProject をYouTubeへ(デフォルト private)
アップロードする。2段階記録+reconcile方式(ADR-0005)で二重投稿を防ぐ:

1. 前提検証(状態=UPLOAD_READY、レビュー合格、Approval存在、出力ファイルchecksum一致)
2. `idempotency_key = upload:{video_project_id}:{checksum}` で `Publication` を
   get-or-create(`upload_status=started`)。既に`completed`+`youtube_video_id`ありなら
   即返却(`run_idempotent_async` の JobRun 已succeeded スキップ経由)。
3. `youtube_video_id` が未記録なら、実行直前に `provider.list_recent_uploads()` で
   description内の idempotency マーカー(`amx-idem:{idempotency_key}`)を突合する
   (reconcile-before-upload)。見つかれば再アップロードせずそのvideo_idを記録する。
   見つからなければ実際にアップロードする。
4. 成功: `youtube_video_id` 記録+`completed`、状態 `UPLOAD_READY`->`UPLOADED_PRIVATE`。
5. 失敗: `upload_status=failed`+`last_error`(シークレット除去)、状態->`UPLOAD_FAILED`。
   `QuotaExceededError` はリトライせずそのまま失敗として伝播する。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger, mask_secrets_in_text
from app.models.approval import Approval
from app.models.asset import Asset
from app.models.job_run import JobRun
from app.models.publication import Publication
from app.models.review import Review
from app.models.script import Script
from app.models.video_project import VideoProject
from app.providers.youtube.base import UploadRequest, YouTubeProvider
from app.services.jobs import JobInProgressError, run_idempotent_async
from app.services.media.characters import character_credits
from app.services.media.dialogue import dialogue_script_enabled, extract_speech_lines
from app.services.media.renderer import compute_file_checksum
from app.services.state_machine import transition

logger = get_logger(__name__)

_MAX_LAST_ERROR_LENGTH = 2000
_RECONCILE_MAX_RESULTS = 50


class VideoProjectNotFoundError(ValueError):
    """指定されたVideoProjectが存在しない場合。"""


class ScriptNotFoundError(ValueError):
    """VideoProjectに紐づくScriptが存在しない、または未設定の場合。"""


class UploadPreconditionError(ValueError):
    """アップロード前提条件(状態・レビュー合格・承認・チェックサム)を満たさない場合。fail-closed。"""


def build_upload_idempotency_key(video_project_id: str, checksum: str) -> str:
    return f"upload:{video_project_id}:{checksum}"


def build_idempotency_marker(idempotency_key: str) -> str:
    """description末尾に埋め込む不可視マーカー(ADR-0005)。"""
    return f"amx-idem:{idempotency_key}"


def _with_voicevox_credits(description: str, body: dict) -> str:
    """掛け合い台本の話者クレジットを概要欄へ重複なく追記する。"""
    settings = get_settings()
    if settings.TTS_PROVIDER != "voicevox" or not dialogue_script_enabled(settings):
        return description
    credits = character_credits(extract_speech_lines(body, settings=settings))
    if not credits:
        return description
    missing = [credit for credit in credits if credit not in description]
    if not missing:
        return description
    separator = "\n\n" if description.strip() else ""
    return f"{description.rstrip()}{separator}" + "\n".join(missing)


def _with_auto_chapters(description: str, source_manifest: dict) -> str:
    """レンダリング時に実測尺から生成したチャプターを概要欄へ重複なく追記する。

    YouTubeのチャプター有効化要件(先頭が0:00・3個以上)を満たす場合のみ追記する。
    """
    chapters = [str(c) for c in (source_manifest.get("auto_chapters") or []) if str(c).strip()]
    if len(chapters) < 3 or not chapters[0].startswith("0:00"):
        return description
    if chapters[0] in description:
        return description
    block = "\n".join(chapters)
    separator = "\n\n" if description.strip() else ""
    return f"{description.rstrip()}{separator}{block}"


def _with_bgm_credit(description: str, session: Session, video_project_id: str) -> str:
    """使用BGMのクレジット(Asset role="bgm" のmeta)を概要欄へ重複なく追記する。

    CC-BY等の表記義務がある音源のクレジット文はレンダリング時にAssetへ記録されている
    (assets/bgm/README.md参照)。クレジット不要の音源(credit空文字)は追記しない。
    """
    bgm_asset = (
        session.query(Asset)
        .filter(Asset.video_project_id == video_project_id, Asset.role == "bgm")
        .one_or_none()
    )
    if bgm_asset is None:
        return description
    credit = str((bgm_asset.meta or {}).get("credit") or "").strip()
    if not credit or credit in description:
        return description
    separator = "\n\n" if description.strip() else ""
    return f"{description.rstrip()}{separator}Music: {credit}"


def _get_video_project(session: Session, video_project_id: str) -> VideoProject:
    project = session.get(VideoProject, video_project_id)
    if project is None:
        raise VideoProjectNotFoundError(f"VideoProject not found: {video_project_id}")
    return project


def _get_script(session: Session, project: VideoProject) -> Script:
    if project.script_id is None:
        raise ScriptNotFoundError(f"VideoProject({project.id})にScriptが紐づいていません")
    script = session.get(Script, project.script_id)
    if script is None:
        raise ScriptNotFoundError(f"Script not found: {project.script_id}")
    return script


def _latest_review(session: Session, video_project_id: str, reviewer_type: str) -> Review | None:
    return (
        session.query(Review)
        .filter(Review.video_project_id == video_project_id, Review.reviewer_type == reviewer_type)
        .order_by(Review.review_version.desc())
        .first()
    )


def _validate_upload_preconditions(session: Session, project: VideoProject) -> None:
    """前提検証(fail-closed)。1つでも欠ければ `UploadPreconditionError` を送出する。"""
    reasons: list[str] = []

    if project.status != "UPLOAD_READY":
        reasons.append(f"video project status is not UPLOAD_READY (actual={project.status})")

    machine_review = _latest_review(session, project.id, "machine")
    content_review = _latest_review(session, project.id, "content")
    if machine_review is None or not machine_review.passed:
        reasons.append("machine review not passed or missing")
    if content_review is None or not content_review.passed:
        reasons.append("content review not passed or missing")

    approved = (
        session.query(Approval)
        .filter(Approval.video_project_id == project.id, Approval.decision == "approved")
        .first()
    )
    if approved is None:
        reasons.append("human approval required but missing")

    if not project.checksum or not project.output_path:
        reasons.append("checksum or output path missing (render not completed)")
    else:
        output_path = Path(project.output_path)
        if not output_path.exists():
            reasons.append("output file missing")
        elif compute_file_checksum(output_path) != project.checksum:
            reasons.append("checksum mismatch")

    if reasons:
        raise UploadPreconditionError("; ".join(reasons))


def _get_or_create_publication(
    session: Session,
    project: VideoProject,
    idempotency_key: str,
    *,
    title: str,
    description: str,
    tags: list[str],
    privacy_status: str,
) -> Publication:
    existing = (
        session.query(Publication)
        .filter(Publication.idempotency_key == idempotency_key)
        .one_or_none()
    )
    if existing is not None:
        return existing

    publication = Publication(
        video_project_id=project.id,
        title=title,
        description=description,
        tags=tags,
        privacy_status=privacy_status,
        idempotency_key=idempotency_key,
        upload_status="started",
    )
    try:
        with session.begin_nested():
            session.add(publication)
            session.flush()
    except IntegrityError:
        session.expunge(publication)
        winner = (
            session.query(Publication)
            .filter(Publication.idempotency_key == idempotency_key)
            .one_or_none()
        )
        if winner is None:  # pragma: no cover - 理論上到達しない防御的分岐
            raise
        return winner
    return publication


async def _reconcile_existing_upload(
    provider: YouTubeProvider, marker: str, *, max_results: int = _RECONCILE_MAX_RESULTS
) -> str | None:
    """直近アップロード一覧からidempotencyマーカーを突合する(ADR-0005 reconcile-before-upload)。"""
    uploads = await provider.list_recent_uploads(max_results=max_results)
    for video in uploads:
        if marker in video.description:
            return video.youtube_video_id
    return None


async def _apply_thumbnail_if_available(
    session: Session, project: VideoProject, provider: YouTubeProvider, youtube_video_id: str
) -> None:
    """role="thumbnail"(選択済みサムネイル)があればYouTubeへ設定する。

    サムネイル設定はアップロード本体の成否・公開ゲートとは無関係なため、失敗しても
    アップロード自体は成功として扱う(warningに留めて継続)。
    """
    thumbnail_asset = (
        session.query(Asset)
        .filter(Asset.video_project_id == project.id, Asset.role == "thumbnail")
        .one_or_none()
    )
    if thumbnail_asset is None:
        return
    try:
        await provider.set_thumbnail(
            youtube_video_id=youtube_video_id, image_path=Path(thumbnail_asset.file_path)
        )
    except Exception as exc:  # noqa: BLE001 - サムネイル設定失敗でアップロード全体を失敗させない
        logger.warning(
            "upload_set_thumbnail_failed",
            video_project_id=project.id,
            youtube_video_id=youtube_video_id,
            error=mask_secrets_in_text(str(exc)),
        )


async def _upload_or_reconcile(
    session: Session,
    project: VideoProject,
    publication: Publication,
    provider: YouTubeProvider,
    idempotency_key: str,
    *,
    made_for_kids: bool,
    contains_synthetic_media: bool,
) -> Publication:
    marker = build_idempotency_marker(idempotency_key)

    if not publication.youtube_video_id:
        reconciled_id = await _reconcile_existing_upload(provider, marker)
        if reconciled_id is not None:
            logger.info(
                "upload_reconciled_existing_video",
                video_project_id=project.id,
                publication_id=publication.id,
            )
            publication.youtube_video_id = reconciled_id
            publication.upload_status = "completed"
            publication.last_error = None
            transition(project, "UPLOADED_PRIVATE")
            session.flush()
            await _apply_thumbnail_if_available(session, project, provider, reconciled_id)
            return publication

    assert project.output_path is not None
    request = UploadRequest(
        file_path=project.output_path,
        title=publication.title,
        description=publication.description,
        tags=list(publication.tags),
        privacy_status=publication.privacy_status,
        idempotency_marker=marker,
        made_for_kids=made_for_kids,
        contains_synthetic_media=contains_synthetic_media,
    )
    result = await provider.upload_video(request=request)

    publication.youtube_video_id = result.youtube_video_id
    publication.upload_status = "completed"
    publication.last_error = None
    transition(project, "UPLOADED_PRIVATE")
    session.flush()
    await _apply_thumbnail_if_available(session, project, provider, result.youtube_video_id)
    return publication


async def upload_video(
    session: Session,
    *,
    video_project_id: str,
    provider: YouTubeProvider,
    made_for_kids: bool = False,
    # このシステムはLLM台本+TTS音声合成による生成コンテンツのため、既定でAI開示を行う。
    contains_synthetic_media: bool = True,
) -> Publication:
    """VideoProjectをYouTubeへアップロードする(冪等。ADR-0005 reconcile対応)。"""
    project = _get_video_project(session, video_project_id)
    script = _get_script(session, project)

    if not project.checksum or not project.output_path:
        raise UploadPreconditionError("checksum or output path missing (render not completed)")

    idempotency_key = build_upload_idempotency_key(video_project_id, project.checksum)

    body = script.body or {}
    title = script.title
    description = _with_voicevox_credits(str(body.get("description") or ""), body)
    description = _with_auto_chapters(description, script.source_manifest or {})
    description = _with_bgm_credit(description, session, video_project_id)
    tags = list(body.get("tags") or [])
    privacy_status = get_settings().YOUTUBE_DEFAULT_PRIVACY_STATUS

    async def _do_upload(_job_run: JobRun) -> Publication:
        _validate_upload_preconditions(session, project)

        publication = _get_or_create_publication(
            session,
            project,
            idempotency_key,
            title=title,
            description=description,
            tags=tags,
            privacy_status=privacy_status,
        )

        if publication.upload_status == "completed" and publication.youtube_video_id:
            return publication

        try:
            return await _upload_or_reconcile(
                session,
                project,
                publication,
                provider,
                idempotency_key,
                made_for_kids=made_for_kids,
                contains_synthetic_media=contains_synthetic_media,
            )
        except Exception as exc:
            publication.upload_status = "failed"
            publication.last_error = mask_secrets_in_text(str(exc))[:_MAX_LAST_ERROR_LENGTH]
            transition(project, "UPLOAD_FAILED")
            session.flush()
            exc.publication_idempotency_key = idempotency_key  # type: ignore[attr-defined]
            raise

    job_result = await run_idempotent_async(
        session,
        job_type="upload_video",
        entity_type="video_project",
        entity_id=video_project_id,
        idempotency_key=idempotency_key,
        fn=_do_upload,
    )
    if job_result.status == "in_progress":
        raise JobInProgressError(f"upload_video already in progress: {idempotency_key}")
    if job_result.status == "skipped":
        existing = (
            session.query(Publication).filter(Publication.idempotency_key == idempotency_key).one()
        )
        return existing
    assert job_result.result is not None
    return job_result.result


def restart_upload(session: Session, *, video_project_id: str) -> VideoProject:
    """再試行: `UPLOAD_FAILED` -> `UPLOAD_READY`(復旧エッジ)。"""
    project = _get_video_project(session, video_project_id)
    transition(project, "UPLOAD_READY")
    session.flush()
    return project


def record_publication_failure_in_new_session(
    *, video_project_id: str, idempotency_key: str, error: BaseException
) -> None:
    """Persist Publication failure after a Celery task rollback.

    `upload_video()` records `Publication.upload_status="failed"` inside the caller's
    transaction. Celery task wrappers roll that transaction back before re-raising, so
    this helper recreates or updates the Publication in a fresh transaction.
    """
    from app.db.session import SessionLocal

    new_session = SessionLocal()
    try:
        project = new_session.get(VideoProject, video_project_id)
        if project is None:
            logger.warning(
                "publication_failure_project_not_found",
                video_project_id=video_project_id,
                idempotency_key=idempotency_key,
            )
            return

        script = new_session.get(Script, project.script_id) if project.script_id else None
        body = script.body if script is not None and script.body else {}
        title = script.title if script is not None else f"VideoProject {video_project_id}"
        description = _with_voicevox_credits(str(body.get("description") or ""), body)
        description = _with_auto_chapters(
            description, (script.source_manifest if script is not None else None) or {}
        )
        description = _with_bgm_credit(description, new_session, video_project_id)
        tags = list(body.get("tags") or [])
        privacy_status = get_settings().YOUTUBE_DEFAULT_PRIVACY_STATUS

        publication = (
            new_session.query(Publication)
            .filter(Publication.idempotency_key == idempotency_key)
            .one_or_none()
        )
        if publication is None:
            publication = Publication(
                video_project_id=video_project_id,
                title=title,
                description=description,
                tags=tags,
                privacy_status=privacy_status,
                idempotency_key=idempotency_key,
                upload_status="failed",
            )
            new_session.add(publication)
        else:
            publication.upload_status = "failed"

        publication.last_error = mask_secrets_in_text(str(error))[:_MAX_LAST_ERROR_LENGTH]
        new_session.commit()
        logger.info(
            "publication_failure_persisted_in_new_session",
            video_project_id=video_project_id,
            idempotency_key=idempotency_key,
        )
    except Exception:
        new_session.rollback()
        logger.error(
            "record_publication_failure_in_new_session_failed",
            video_project_id=video_project_id,
            idempotency_key=idempotency_key,
        )
        raise
    finally:
        new_session.close()
