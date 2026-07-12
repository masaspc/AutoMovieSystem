"""音響(Phase A): BGM選曲・SE配置・ダッキングミックスのffmpeg引数の検証。

実素材・実APIは使わない(tmp_path上のダミーファイルと引数ビルダーの純関数検証)。
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.services.media import bgm
from app.services.media.renderer import AudioMixSpec, build_audio_mix_args


def _settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        BGM_ASSETS_DIR=str(tmp_path / "bgm"),
        SE_ASSETS_DIR=str(tmp_path / "se"),
    )


def _make_bgm(tmp_path: Path, mood: str, names: list[str]) -> None:
    mood_dir = tmp_path / "bgm" / mood
    mood_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (mood_dir / f"{name}.mp3").write_bytes(f"audio-{name}".encode())
        (mood_dir / f"{name}.mp3.credit.txt").write_text(
            f'"{name}" Kevin MacLeod (incompetech.com) CC-BY 4.0', encoding="utf-8"
        )


def test_select_bgm_is_deterministic_for_same_project(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _make_bgm(tmp_path, "calm", ["a", "b", "c"])

    first = bgm.select_bgm(video_project_id="project-1", mood="calm", settings=settings)
    second = bgm.select_bgm(video_project_id="project-1", mood="calm", settings=settings)

    assert first is not None and second is not None
    assert first.path == second.path
    assert first.checksum == second.checksum
    assert "Kevin MacLeod" in first.credit


def test_select_bgm_returns_none_for_none_mood_or_missing_assets(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert bgm.select_bgm(video_project_id="p", mood="none", settings=settings) is None
    # 素材未配置(CI等)でも例外にせずNone(BGMなしで完走する)。
    assert bgm.select_bgm(video_project_id="p", mood="calm", settings=settings) is None


def test_transition_effects_skip_leading_offset_and_missing_assets(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert bgm.transition_effects([0.0, 5.0], settings) == []

    se_dir = tmp_path / "se"
    se_dir.mkdir(parents=True)
    (se_dir / "transition.wav").write_bytes(b"se")
    effects = bgm.transition_effects([0.0, 5.0, 12.5], settings)
    assert [e.offset_seconds for e in effects] == [5.0, 12.5]


def test_mix_args_voice_only_has_no_bgm_filters(tmp_path: Path) -> None:
    args = build_audio_mix_args([tmp_path / "v0.wav"], tmp_path / "out.m4a", mix=None)
    joined = " ".join(args)
    assert "sidechaincompress" not in joined
    assert "loudnorm" in joined


def test_mix_args_with_bgm_include_loop_volume_and_ducking(tmp_path: Path) -> None:
    mix = AudioMixSpec(bgm_path=tmp_path / "bgm.mp3", bgm_volume_db=-19.0)
    args = build_audio_mix_args(
        [tmp_path / "v0.wav", tmp_path / "v1.wav"], tmp_path / "out.m4a", mix=mix
    )
    joined = " ".join(args)
    # BGMは無限ループ入力+音量指定+セリフキーのサイドチェイン圧縮(ダッキング)。
    assert "-stream_loop -1" in joined
    assert "volume=-19.0dB" in joined
    assert "sidechaincompress" in joined
    # ミックスはセリフ長基準(BGMループで尺が伸びない)。
    assert "duration=first" in joined


def test_mix_args_with_effects_include_adelay_at_offsets(tmp_path: Path) -> None:
    mix = AudioMixSpec(
        effects=[
            (tmp_path / "transition.wav", 5.0, -10.0),
            (tmp_path / "transition.wav", 12.5, -10.0),
        ]
    )
    args = build_audio_mix_args([tmp_path / "v0.wav"], tmp_path / "out.m4a", mix=mix)
    joined = " ".join(args)
    assert "adelay=5000:all=1" in joined
    assert "adelay=12500:all=1" in joined
    assert "volume=-10.0dB" in joined


def test_effects_fingerprint_changes_with_offsets(tmp_path: Path) -> None:
    se = tmp_path / "transition.wav"
    se.write_bytes(b"se")
    a = bgm.effects_fingerprint([bgm.SoundEffect(path=se, offset_seconds=5.0)])
    b = bgm.effects_fingerprint([bgm.SoundEffect(path=se, offset_seconds=6.0)])
    assert a != b
    assert bgm.effects_fingerprint([]) == "se-disabled"
