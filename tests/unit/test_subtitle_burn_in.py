"""字幕焼き込みの設定化(デフォルトOFF)と、焼き込み時のスタイル修正の検証。

運用フィードバック: 焼き込み字幕が画面からはみ出していた。原因はSRT焼き込み時の
force_styleがlibassの既定PlayRes(384x288)基準で解釈され、1080pで約3.75倍に
拡大されることを考慮していなかったため。デフォルトはOFF(YouTube字幕に委ねる)とし、
ONにする場合もPlayRes基準の正しい値を使う。
"""

from __future__ import annotations

from pathlib import Path

from app.services.media.renderer import build_mux_args


def test_mux_without_subtitle_has_no_video_filter(tmp_path: Path) -> None:
    args = build_mux_args(
        tmp_path / "video.mp4", tmp_path / "audio.m4a", tmp_path / "out.mp4", subtitle_path=None
    )
    assert "-vf" not in args
    assert not any("subtitles" in arg for arg in args)


def test_mux_with_subtitle_burns_in_with_playres_scaled_style(tmp_path: Path) -> None:
    args = build_mux_args(
        tmp_path / "video.mp4",
        tmp_path / "audio.m4a",
        tmp_path / "out.mp4",
        subtitle_path=tmp_path / "subs.srt",
    )
    assert "-vf" in args
    vf = args[args.index("-vf") + 1]
    assert "subtitles=" in vf
    # PlayRes(384x288)基準の値であること(1080p実寸のピクセル値をそのまま
    # 書き戻すとフォント・マージンが約3.75倍になり、はみ出しが再発する)。
    assert "FontSize=13" in vf
    assert "MarginL=24" in vf
    assert "MarginV=13" in vf
