"""背景生成プロバイダーの生成口。"""

from app.providers.background.base import BackgroundProvider
from app.providers.background.pillow import PillowBackgroundProvider


def get_background_provider() -> BackgroundProvider:
    """現在のローカル実装を返す。将来ここで画像生成APIへ切り替える。"""
    return PillowBackgroundProvider()
