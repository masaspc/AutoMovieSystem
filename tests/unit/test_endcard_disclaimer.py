"""エンディングCTAのチャンネル免責文表示テスト。"""

from pathlib import Path

from PIL import Image, ImageChops

from app.services.media.renderer import generate_endcard_image


def test_endcard_disclaimer_changes_only_configured_output(tmp_path: Path) -> None:
    without_path = tmp_path / "without.png"
    with_path = tmp_path / "with.png"

    generate_endcard_image(without_path, title="タイトル", channel_name="チャンネル")
    generate_endcard_image(
        with_path,
        title="タイトル",
        channel_name="チャンネル",
        disclaimer_text="本動画は情報提供を目的とし、投資助言ではありません。",
    )

    with Image.open(without_path) as without_image, Image.open(with_path) as with_image:
        assert with_image.size == (1920, 1080)
        assert ImageChops.difference(without_image, with_image).getbbox() is not None
