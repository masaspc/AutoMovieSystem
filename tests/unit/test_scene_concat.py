from __future__ import annotations

from pathlib import Path

from app.services.media import renderer


def test_scene_track_uses_one_concat_input_for_many_frames(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    frames = [
        renderer.SceneFrame(path=tmp_path / f"frame_{index:04d}.png", duration_seconds=0.18)
        for index in range(1_000)
    ]
    captured: dict[str, list[str]] = {}

    def fake_run_ffmpeg(_ffmpeg: str, args: list[str], *, timeout: float) -> None:
        captured["args"] = args

    monkeypatch.setattr(renderer, "_run_ffmpeg", fake_run_ffmpeg)
    renderer._build_scene_video_track("ffmpeg", frames, tmp_path / "scene.mp4", timeout=30)

    assert captured["args"].count("-i") == 1
    assert "-f" in captured["args"]
    concat_list = tmp_path / "scene_frames.ffconcat"
    assert concat_list.exists()
    assert (
        sum(
            1
            for line in concat_list.read_text(encoding="utf-8").splitlines()
            if line.startswith("file ")
        )
        == 1_000
    )
