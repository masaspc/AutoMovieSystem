"""BGM・SE素材の選択(Phase A: 音響)。

`assets/bgm/<mood>/` から video_project_id ベースで決定的に1曲選ぶ(乱数不使用)。
素材が無い環境(CI・素材未配置)では None を返し、レンダリングはBGMなしで完走する
(fail-soft。テスト・Fake運用を壊さない)。

各音源ファイルの隣の `<name>.credit.txt`(1行のクレジット文)は、アップロード時に
動画概要欄へ自動追記される(CC-BY等の表記義務を自動で満たす。assets/bgm/README.md参照)。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, get_settings

_AUDIO_EXTENSIONS = (".mp3", ".wav", ".ogg")


@dataclass(frozen=True)
class BGMTrack:
    """選択されたBGM音源。"""

    path: Path
    mood: str
    credit: str
    checksum: str


@dataclass(frozen=True)
class SoundEffect:
    """SE音源と挿入位置(秒)。"""

    path: Path
    offset_seconds: float


def _file_checksum(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_credit(audio_path: Path) -> str:
    credit_path = audio_path.with_suffix(audio_path.suffix + ".credit.txt")
    alt_credit_path = audio_path.with_name(f"{audio_path.stem}.credit.txt")
    for candidate in (credit_path, alt_credit_path):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip()
    return ""


def list_bgm_tracks(mood: str, settings: Settings | None = None) -> list[Path]:
    """指定moodの音源をソート済みで返す(決定的な選択の土台)。"""
    settings = settings or get_settings()
    mood_dir = Path(settings.BGM_ASSETS_DIR) / mood
    if not mood_dir.is_dir():
        return []
    return sorted(
        p for p in mood_dir.iterdir() if p.suffix.lower() in _AUDIO_EXTENSIONS and p.is_file()
    )


def select_bgm(
    *, video_project_id: str, mood: str, settings: Settings | None = None
) -> BGMTrack | None:
    """video_project_idから決定的にBGMを1曲選ぶ。mood="none"や素材なしはNone。"""
    if mood == "none":
        return None
    tracks = list_bgm_tracks(mood, settings)
    if not tracks:
        return None
    digest = hashlib.sha256(f"bgm:{video_project_id}".encode()).digest()
    path = tracks[digest[0] % len(tracks)]
    return BGMTrack(
        path=path,
        mood=mood,
        credit=_read_credit(path),
        checksum=_file_checksum(path),
    )


def transition_effects(
    section_start_offsets: list[float], settings: Settings | None = None
) -> list[SoundEffect]:
    """セクション切替位置(先頭を除く)に挿入するSEを返す。素材なしなら空。"""
    settings = settings or get_settings()
    se_path = Path(settings.SE_ASSETS_DIR) / "transition.wav"
    if not se_path.is_file():
        return []
    return [
        SoundEffect(path=se_path, offset_seconds=offset)
        for offset in section_start_offsets
        if offset > 0
    ]


def effects_fingerprint(effects: list[SoundEffect]) -> str:
    """SE構成(ファイル内容+挿入位置)のハッシュ(レンダリング冪等キー用)。"""
    if not effects:
        return "se-disabled"
    hasher = hashlib.sha256()
    seen: set[Path] = set()
    for effect in effects:
        hasher.update(f"{effect.offset_seconds:.3f}|".encode())
        if effect.path not in seen:
            hasher.update(_file_checksum(effect.path).encode("utf-8"))
            seen.add(effect.path)
    return hasher.hexdigest()
