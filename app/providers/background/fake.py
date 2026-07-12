"""テスト向け背景プロバイダー。"""

from pathlib import Path

from PIL import Image


class FakeBackgroundProvider:
    """依存なしの単色画像を決定的に生成する。"""

    def generate(self, section: dict, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1920, 1080), (22, 29, 48)).save(output_path, "PNG")
        return output_path
