"""ローカルPillow背景プロバイダー。"""

from pathlib import Path

from app.services.media.visuals import generate_section_visual


class PillowBackgroundProvider:
    """外部APIなしで教材用の構造化背景を生成する。"""

    def generate(self, section: dict, output_path: Path) -> Path:
        return generate_section_visual(section, output_path)
