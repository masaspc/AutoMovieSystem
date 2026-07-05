from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.core.subprocess_util import CompletedProcessResult
from app.models.asset import Asset
from app.models.channel import Channel
from app.models.script import Script
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.services.media.probe import ProbeError, VideoProbeResult
from app.services.reviews import machine


def _good_probe(**overrides: object) -> VideoProbeResult:
    defaults: dict[str, object] = {
        "duration_seconds": 10.0,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac",
        "has_audio": True,
        "size_bytes": 1000,
    }
    defaults.update(overrides)
    return VideoProbeResult(**defaults)  # type: ignore[arg-type]


def _make_project(
    db_session: Session,
    tmp_path: Path,
    *,
    target_duration_seconds: float | None = 10,
    description: str | None = "説明文",
    title: str = "テスト動画",
    with_subtitle: bool = True,
    srt_content: str = "1\n00:00:00,000 --> 00:00:05,000\nhello\n",
    checksum: str = "a" * 64,
) -> VideoProject:
    channel = Channel(name="ch")
    db_session.add(channel)
    db_session.flush()

    topic = Topic(channel_id=channel.id, title="topic", source_type="manual", source_ref="r-1")
    db_session.add(topic)
    db_session.flush()

    body: dict[str, object] = {"title_candidates": [title]}
    if description is not None:
        body["description"] = description
    script = Script(
        topic_id=topic.id,
        version=1,
        title=title,
        body=body,
        source_manifest={},
        status="reviewed",
    )
    db_session.add(script)
    db_session.flush()

    tmp_path.mkdir(parents=True, exist_ok=True)
    video_path = tmp_path / "out.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    project = VideoProject(
        topic_id=topic.id,
        script_id=script.id,
        status="VIDEO_RENDERED",
        generation=1,
        target_duration_seconds=target_duration_seconds,
        output_path=str(video_path),
        checksum=checksum,
    )
    db_session.add(project)
    db_session.flush()

    if with_subtitle:
        srt_path = tmp_path / "sub.srt"
        srt_path.write_text(srt_content, encoding="utf-8")
        asset = Asset(
            video_project_id=project.id,
            asset_type="subtitle",
            role="subtitle:srt",
            file_path=str(srt_path),
            checksum="s" * 8,
            meta={"kind": "srt"},
        )
        db_session.add(asset)
        db_session.flush()

    return project


def _fake_run_checked_clean(
    args: list[str], *, timeout: float, check: bool = True
) -> CompletedProcessResult:
    """silencedetect/volumedetectともに問題なしのffmpeg stderrを返す。"""
    if "silencedetect" in " ".join(args):
        stderr = ""
    else:
        stderr = "[Parsed_volumedetect_0 @ 0x1] mean_volume: -15.0 dB\n"
    return CompletedProcessResult(args=tuple(args), returncode=0, stdout="", stderr=stderr)


@pytest.fixture(autouse=True)
def _no_silence_or_volume_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    """デフォルトではsilencedetect/volumedetectを問題なしにモックする(subprocess_util経由)。"""
    monkeypatch.setattr(machine, "run_checked", _fake_run_checked_clean)


def test_missing_output_path_is_blocking(db_session: Session, tmp_path: Path) -> None:
    project = _make_project(db_session, tmp_path)
    project.output_path = None

    findings = machine.inspect_machine(db_session, project)

    assert any(f.code == "missing_output_path" and f.severity == "blocking" for f in findings)


def test_empty_file_is_blocking(db_session: Session, tmp_path: Path) -> None:
    project = _make_project(db_session, tmp_path)
    Path(project.output_path).write_bytes(b"")

    findings = machine.inspect_machine(db_session, project)

    assert any(f.code == "empty_or_missing_file" for f in findings)


def test_unreadable_video_is_blocking(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(db_session, tmp_path)

    def _fail_probe(path: Path) -> VideoProbeResult:
        raise ProbeError("boom")

    monkeypatch.setattr(machine, "probe_video", _fail_probe)

    findings = machine.inspect_machine(db_session, project)

    assert any(f.code == "unreadable_video" for f in findings)


def test_duration_out_of_range_is_blocking(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(db_session, tmp_path, target_duration_seconds=10)
    monkeypatch.setattr(machine, "probe_video", lambda path: _good_probe(duration_seconds=100.0))

    findings = machine.inspect_machine(db_session, project)

    assert any(f.code == "duration_out_of_range" and f.severity == "blocking" for f in findings)


def test_missing_target_duration_is_blocking(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(db_session, tmp_path, target_duration_seconds=None)
    monkeypatch.setattr(machine, "probe_video", lambda path: _good_probe())

    findings = machine.inspect_machine(db_session, project)

    assert any(f.code == "missing_target_duration" and f.severity == "blocking" for f in findings)


def test_missing_audio_track_is_blocking(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(db_session, tmp_path)
    monkeypatch.setattr(machine, "probe_video", lambda path: _good_probe(has_audio=False))

    findings = machine.inspect_machine(db_session, project)

    assert any(f.code == "missing_audio_track" and f.severity == "blocking" for f in findings)


def test_resolution_too_small_is_blocking(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(db_session, tmp_path)
    monkeypatch.setattr(machine, "probe_video", lambda path: _good_probe(width=1280, height=720))

    findings = machine.inspect_machine(db_session, project)

    assert any(f.code == "resolution_too_small" and f.severity == "blocking" for f in findings)


def test_missing_subtitle_asset_is_blocking(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(db_session, tmp_path, with_subtitle=False)
    monkeypatch.setattr(machine, "probe_video", lambda path: _good_probe())

    findings = machine.inspect_machine(db_session, project)

    assert any(f.code == "missing_subtitle_asset" and f.severity == "blocking" for f in findings)


def test_subtitle_exceeds_video_duration_is_blocking(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(
        db_session,
        tmp_path,
        target_duration_seconds=10,
        srt_content="1\n00:00:00,000 --> 00:00:59,000\nhello\n",
    )
    monkeypatch.setattr(machine, "probe_video", lambda path: _good_probe(duration_seconds=10.0))

    findings = machine.inspect_machine(db_session, project)

    assert any(
        f.code == "subtitle_exceeds_video_duration" and f.severity == "blocking" for f in findings
    )


def test_missing_script_metadata_is_blocking(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(db_session, tmp_path, description=None)
    monkeypatch.setattr(machine, "probe_video", lambda path: _good_probe())

    findings = machine.inspect_machine(db_session, project)

    assert any(
        f.code == "missing_script_description" and f.severity == "blocking" for f in findings
    )


def test_duplicate_output_checksum_is_blocking(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared_checksum = "b" * 64
    _make_project(db_session, tmp_path / "other", checksum=shared_checksum)
    project = _make_project(db_session, tmp_path / "mine", checksum=shared_checksum)
    monkeypatch.setattr(machine, "probe_video", lambda path: _good_probe())

    findings = machine.inspect_machine(db_session, project)

    assert any(f.code == "duplicate_output_checksum" and f.severity == "blocking" for f in findings)


def test_all_rules_pass_produces_no_blocking_findings(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(db_session, tmp_path, target_duration_seconds=10)
    monkeypatch.setattr(machine, "probe_video", lambda path: _good_probe(duration_seconds=10.0))

    findings = machine.inspect_machine(db_session, project)

    assert not [f for f in findings if f.severity == "blocking"]


def test_silencedetect_parses_stderr_for_long_silence(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(db_session, tmp_path)

    def _fake_run_checked(
        args: list[str], *, timeout: float, check: bool = True
    ) -> CompletedProcessResult:
        if "silencedetect" in " ".join(args):
            stderr = (
                "[silencedetect @ 0x1] silence_start: 1.0\n"
                "[silencedetect @ 0x1] silence_end: 15.0 | silence_duration: 14.0\n"
            )
        else:
            stderr = "[Parsed_volumedetect_0 @ 0x1] mean_volume: -15.0 dB\n"
        return CompletedProcessResult(args=tuple(args), returncode=0, stdout="", stderr=stderr)

    monkeypatch.setattr(machine, "run_checked", _fake_run_checked)
    monkeypatch.setattr(machine, "probe_video", lambda path: _good_probe())

    findings = machine.inspect_machine(db_session, project)

    assert any(f.code == "long_silence_detected" and f.severity == "blocking" for f in findings)


def test_volumedetect_parses_stderr_for_out_of_range_volume(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(db_session, tmp_path)

    def _fake_run_checked(
        args: list[str], *, timeout: float, check: bool = True
    ) -> CompletedProcessResult:
        if "silencedetect" in " ".join(args):
            stderr = ""
        else:
            stderr = "[Parsed_volumedetect_0 @ 0x1] mean_volume: -45.0 dB\n"
        return CompletedProcessResult(args=tuple(args), returncode=0, stdout="", stderr=stderr)

    monkeypatch.setattr(machine, "run_checked", _fake_run_checked)
    monkeypatch.setattr(machine, "probe_video", lambda path: _good_probe())

    findings = machine.inspect_machine(db_session, project)

    assert any(f.code == "volume_out_of_range" and f.severity == "warning" for f in findings)
