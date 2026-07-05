from __future__ import annotations

from pathlib import Path

import pytest

from app.core.paths import resolve_generated_path
from app.services.media import renderer
from app.services.media.probe import VideoProbeResult


def _make_inputs(
    video_project_id: str, input_checksum: str, tmp_path: Path
) -> renderer.RenderInputs:
    return renderer.RenderInputs(
        video_project_id=video_project_id,
        aspect_ratio="16:9",
        section_audio_paths=[tmp_path / "a.wav"],
        background_image_path=tmp_path / "bg.png",
        subtitle_srt_path=tmp_path / "sub.srt",
        title="タイトル",
        channel_name="チャンネル",
        input_checksum=input_checksum,
    )


def test_render_video_skips_when_output_and_sidecar_checksum_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path))

    input_checksum = "abcdef0123456789" * 4
    output_path = resolve_generated_path(renderer.output_relative_path("proj-1", input_checksum))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"fake mp4 bytes")

    sidecar_path = renderer.sidecar_checksum_path(output_path)
    sidecar_path.write_text(input_checksum, encoding="utf-8")

    def _fake_probe_video(path: Path) -> VideoProbeResult:
        assert path == output_path
        return VideoProbeResult(
            duration_seconds=5.0,
            width=1920,
            height=1080,
            fps=30.0,
            video_codec="h264",
            audio_codec="aac",
            has_audio=True,
            size_bytes=100,
        )

    monkeypatch.setattr(renderer, "probe_video", _fake_probe_video)

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("冪等スキップのはずがFFmpegが呼ばれた")

    monkeypatch.setattr(renderer, "_render_impl", _fail_if_called)

    inputs = _make_inputs("proj-1", input_checksum, tmp_path)
    result = renderer.render_video(inputs)

    assert result.skipped is True
    assert result.duration_seconds == 5.0
    assert result.output_path == output_path


def test_render_video_rerenders_when_sidecar_checksum_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path))

    input_checksum = "a" * 64
    output_path = resolve_generated_path(renderer.output_relative_path("proj-2", input_checksum))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"old content")

    sidecar_path = renderer.sidecar_checksum_path(output_path)
    sidecar_path.write_text("different-checksum-from-before", encoding="utf-8")

    call_count = {"n": 0}

    def _fake_render_impl(
        render_inputs: renderer.RenderInputs, settings: object, out_path: Path, work_dir: Path
    ) -> renderer.RenderResult:
        call_count["n"] += 1
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"new content")
        return renderer.RenderResult(
            output_path=out_path, checksum="newsum", duration_seconds=9.0, skipped=False
        )

    monkeypatch.setattr(renderer, "_render_impl", _fake_render_impl)

    inputs = _make_inputs("proj-2", input_checksum, tmp_path)
    result = renderer.render_video(inputs)

    assert call_count["n"] == 1
    assert result.skipped is False
    assert result.checksum == "newsum"


def test_render_video_rerenders_when_output_missing_even_with_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GENERATED_DIR", str(tmp_path))

    input_checksum = "b" * 64
    output_path = resolve_generated_path(renderer.output_relative_path("proj-3", input_checksum))
    # 出力ファイルなし、サイドカーだけ存在するケース(破損/削除後の想定)。
    sidecar_path = renderer.sidecar_checksum_path(output_path)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(input_checksum, encoding="utf-8")

    call_count = {"n": 0}

    def _fake_render_impl(
        render_inputs: renderer.RenderInputs, settings: object, out_path: Path, work_dir: Path
    ) -> renderer.RenderResult:
        call_count["n"] += 1
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"content")
        return renderer.RenderResult(
            output_path=out_path, checksum="sum", duration_seconds=3.0, skipped=False
        )

    monkeypatch.setattr(renderer, "_render_impl", _fake_render_impl)

    inputs = _make_inputs("proj-3", input_checksum, tmp_path)
    result = renderer.render_video(inputs)

    assert call_count["n"] == 1
    assert result.skipped is False


def test_render_video_raises_not_implemented_for_9_16(tmp_path: Path) -> None:
    inputs = renderer.RenderInputs(
        video_project_id="proj-4",
        aspect_ratio="9:16",
        section_audio_paths=[tmp_path / "a.wav"],
        background_image_path=tmp_path / "bg.png",
        subtitle_srt_path=tmp_path / "sub.srt",
        title="t",
        channel_name="c",
        input_checksum="x" * 64,
    )
    with pytest.raises(NotImplementedError):
        renderer.render_video(inputs)


def test_render_video_rejects_empty_section_audio_paths(tmp_path: Path) -> None:
    inputs = renderer.RenderInputs(
        video_project_id="proj-5",
        aspect_ratio="16:9",
        section_audio_paths=[],
        background_image_path=tmp_path / "bg.png",
        subtitle_srt_path=tmp_path / "sub.srt",
        title="t",
        channel_name="c",
        input_checksum="y" * 64,
    )
    with pytest.raises(ValueError, match="空にできません"):
        renderer.render_video(inputs)
