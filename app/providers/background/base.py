"""背景生成プロバイダーの契約。"""

from pathlib import Path
from typing import Protocol


class BackgroundProvider(Protocol):
    """台本セクションから教材背景を1枚生成する。"""

    def generate(self, section: dict, output_path: Path) -> Path:
        """背景を生成して保存先を返す。"""
        ...
