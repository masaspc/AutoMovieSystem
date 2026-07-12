"""シリーズ統一サムネイル自動生成(CTR向上・§動画ごとに3案生成し管理画面で選択)。

同一シリーズの動画は `app.services.media.branding.SeriesBranding` により常に同じ配色・
エピソード番号バッジを持つ「番組」として統一される。描画は全て決定的(乱数不使用、または
`video_project_id` をシードにした決定的擬似乱数)であり、同一入力からの再生成は常に
同一チェックサムのファイルを生成する。

`THUMBNAIL_SPEC_VERSION` を上げるとレイアウト実装変更を全既存動画へ反映できる
(既存Assetの `meta.spec_version` が現行と不一致になり再生成される)。
"""

from __future__ import annotations

import math
import random
import textwrap
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.paths import resolve_generated_path
from app.models.asset import Asset
from app.models.channel import Channel
from app.models.episode_plan import EpisodePlan
from app.models.series_plan import SeriesPlan
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.services.media import characters
from app.services.media.branding import SeriesBranding, resolve_branding
from app.services.media.pipeline import _get_script, _get_video_project, _upsert_asset
from app.services.media.renderer import compute_file_checksum, find_japanese_font

THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720
# レイアウト実装(色・座標・合成方法等)を変えたら必ずインクリメントする。
# 既存Assetの meta.spec_version と不一致になり、次回 generate_thumbnail_candidates 実行時
# に再生成される(prepare_assets自体の冪等キーは変えない)。
THUMBNAIL_SPEC_VERSION = 1
THUMBNAIL_CANDIDATE_COUNT = 3
THUMBNAIL_ROLE_PREFIX = "thumbnail:candidate:"
THUMBNAIL_ROLE_SELECTED = "thumbnail"

# 候補indexごとに固定されたレイアウト(spec: A=bold, B=clean, C=pop)。
_LAYOUTS: tuple[str, ...] = ("bold", "clean", "pop")

__all__ = [
    "THUMBNAIL_CANDIDATE_COUNT",
    "THUMBNAIL_HEIGHT",
    "THUMBNAIL_ROLE_PREFIX",
    "THUMBNAIL_ROLE_SELECTED",
    "THUMBNAIL_SPEC_VERSION",
    "THUMBNAIL_WIDTH",
    "ThumbnailNotFoundError",
    "generate_thumbnail_candidates",
    "select_thumbnail",
    "thumbnail_relative_path",
]


class ThumbnailNotFoundError(ValueError):
    """指定された候補サムネイルが存在しない場合。"""


def thumbnail_relative_path(video_project_id: str, filename: str) -> str:
    return f"videos/{video_project_id}/thumbnails/{filename}"


def _candidate_role(index: int) -> str:
    return f"{THUMBNAIL_ROLE_PREFIX}{index}"


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = find_japanese_font()
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


@dataclass(frozen=True)
class _SeriesContext:
    series_name: str | None
    position: int | None


def _resolve_series_and_channel(
    session: Session, project: VideoProject
) -> tuple[SeriesPlan | None, EpisodePlan | None, Channel | None]:
    topic = session.get(Topic, project.topic_id)
    channel = session.get(Channel, topic.channel_id) if topic is not None else None
    episode = (
        session.query(EpisodePlan).filter(EpisodePlan.topic_id == project.topic_id).one_or_none()
    )
    series = session.get(SeriesPlan, episode.series_plan_id) if episode is not None else None
    return series, episode, channel


def _resolve_branding_and_context(
    session: Session, project: VideoProject
) -> tuple[SeriesBranding, _SeriesContext]:
    series, episode, channel = _resolve_series_and_channel(session, project)
    if series is not None and episode is not None:
        branding = resolve_branding(series.name, series.branding)
        context = _SeriesContext(series_name=series.name, position=episode.position)
        return branding, context

    branding_key = channel.name if channel is not None else "default-channel"
    return resolve_branding(branding_key, None), _SeriesContext(series_name=None, position=None)


def _resolve_thumbnail_texts(body: dict) -> list[str]:
    raw_texts = [str(t).strip() for t in (body.get("thumbnail_texts") or []) if str(t).strip()]
    if not raw_texts:
        candidates = body.get("title_candidates") or []
        fallback = str(candidates[0]).strip()[:12] if candidates else "今日のポイント"
        raw_texts = [fallback or "今日のポイント"]
    while len(raw_texts) < THUMBNAIL_CANDIDATE_COUNT:
        raw_texts.append(raw_texts[-1])
    return raw_texts[:THUMBNAIL_CANDIDATE_COUNT]


def _pick_speaker(body: dict) -> str:
    for section in body.get("sections") or []:
        for line in section.get("dialogue") or []:
            speaker = line.get("speaker")
            if speaker in ("zundamon", "metan", "tsumugi"):
                return str(speaker)
    return "zundamon"


def _load_character_portrait(settings: Settings, speaker: str) -> Image.Image | None:
    """立ち絵素材が利用可能な場合のみ読み込む。失敗させずNoneを返す(テキストのみへ自動調整)。"""
    if not settings.CHARACTER_RENDER_ENABLED:
        return None
    try:
        portrait_path = characters._portrait_path(  # noqa: SLF001 - 同一ドメイン内の内部ヘルパー再利用
            settings, speaker, "neutral", talking=False
        )
    except characters.CharacterAssetError:
        return None
    try:
        with Image.open(portrait_path) as source:
            return source.convert("RGBA").copy()
    except OSError:
        return None


def _wrap_punchline(text: str, *, max_chars_per_line: int, max_lines: int) -> list[str]:
    lines = textwrap.wrap(text, width=max_chars_per_line) or [text]
    return lines[:max_lines]


def _draw_outlined_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    *,
    fill: tuple[int, int, int],
    outline: tuple[int, int, int] = (0, 0, 0),
    outline_width: int = 8,
) -> None:
    x, y = xy
    step = max(2, outline_width // 2)
    for dx in range(-outline_width, outline_width + 1, step):
        for dy in range(-outline_width, outline_width + 1, step):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


def _fit_portrait(portrait: Image.Image, *, max_size: tuple[int, int]) -> Image.Image:
    resized = portrait.copy()
    resized.thumbnail(max_size, Image.Resampling.LANCZOS)
    return resized


def _vertical_gradient(
    width: int, height: int, top: tuple[int, int, int], bottom: tuple[int, int, int]
) -> Image.Image:
    image = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(round(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    return image


def _draw_episode_badge(
    image: Image.Image,
    *,
    position: int,
    series_name: str,
    secondary_rgb: tuple[int, int, int],
    text_color: tuple[int, int, int],
) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    cx, cy, radius = 115, 115, 85
    # 背景がsecondary色のレイアウト(bold)でも円が溶けないよう白アウトラインを付ける。
    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        fill=(*secondary_rgb, 235),
        outline=(255, 255, 255, 255),
        width=5,
    )
    label = f"#{position}"
    font_number = _font(56)
    bbox = draw.textbbox((0, 0), label, font=font_number)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        (cx - text_w / 2 - bbox[0], cy - text_h / 2 - bbox[1]),
        label,
        font=font_number,
        fill=text_color,
    )
    name_font = _font(26)
    draw.rounded_rectangle(
        (10, cy + radius + 8, 10 + min(len(series_name) * 26 + 20, 420), cy + radius + 48),
        radius=8,
        fill=(0, 0, 0, 170),
    )
    draw.text((20, cy + radius + 12), series_name[:16], font=name_font, fill=text_color)


def _render_bold(
    text: str,
    *,
    accent: tuple[int, int, int],
    secondary: tuple[int, int, int],
    text_color: tuple[int, int, int],
    portrait: Image.Image | None,
) -> Image.Image:
    image = Image.new("RGB", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), secondary)
    draw = ImageDraw.Draw(image, "RGBA")
    # 斜めのアクセント色帯。
    draw.polygon(
        [
            (0, THUMBNAIL_HEIGHT * 0.35),
            (THUMBNAIL_WIDTH, 0),
            (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT * 0.15),
            (0, THUMBNAIL_HEIGHT * 0.55),
        ],
        fill=(*accent, 235),
    )
    draw.polygon(
        [
            (0, THUMBNAIL_HEIGHT),
            (0, THUMBNAIL_HEIGHT * 0.75),
            (THUMBNAIL_WIDTH * 0.6, THUMBNAIL_HEIGHT),
        ],
        fill=(*accent, 160),
    )

    if portrait is not None:
        fitted = _fit_portrait(portrait, max_size=(560, 700))
        x = THUMBNAIL_WIDTH - fitted.width - 20
        y = THUMBNAIL_HEIGHT - fitted.height
        image.paste(fitted, (x, y), fitted)
        text_area_width = 26
    else:
        text_area_width = 15

    lines = _wrap_punchline(text, max_chars_per_line=text_area_width, max_lines=2)
    font = _font(96 if len(lines) == 1 else 72)
    # エピソードバッジ+シリーズ名ラベル(左上、〜y≒250)と重ならない高さから開始する。
    y = 280
    for line in lines:
        _draw_outlined_text(draw, (70, y), line, font, fill=text_color)
        y += 130
    return image


def _render_clean(
    text: str,
    *,
    accent: tuple[int, int, int],
    secondary: tuple[int, int, int],
    text_color: tuple[int, int, int],
    portrait: Image.Image | None,
) -> Image.Image:
    image = _vertical_gradient(THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT, secondary, accent)
    draw = ImageDraw.Draw(image, "RGBA")

    if portrait is not None:
        fitted = _fit_portrait(portrait, max_size=(300, 420))
        image.paste(fitted, (40, THUMBNAIL_HEIGHT - fitted.height - 20), fitted)

    lines = _wrap_punchline(text, max_chars_per_line=12, max_lines=2)
    font = _font(80)
    total_height = len(lines) * 100
    y = (THUMBNAIL_HEIGHT - total_height) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = round((THUMBNAIL_WIDTH - text_w) / 2)
        _draw_outlined_text(draw, (x, y), line, font, fill=text_color, outline_width=6)
        y += 100
    return image


def _render_pop(
    text: str,
    *,
    accent: tuple[int, int, int],
    secondary: tuple[int, int, int],
    text_color: tuple[int, int, int],
    portrait: Image.Image | None,
    seed: str,
) -> Image.Image:
    image = Image.new("RGB", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), accent)
    draw = ImageDraw.Draw(image, "RGBA")

    # 白の集中線風三角形(video_project_idシードの決定的擬似乱数、暗号用途ではない)。
    rng = random.Random(f"thumbnail-pop:{seed}")  # noqa: S311
    center_x, center_y = THUMBNAIL_WIDTH // 2, THUMBNAIL_HEIGHT // 2
    max_radius = 1400
    for i in range(24):
        angle = (360 / 24) * i + rng.uniform(-4, 4)
        radians = math.radians(angle)
        width_deg = rng.uniform(4, 7)
        r1 = math.radians(angle - width_deg)
        r2 = math.radians(angle + width_deg)
        p1 = (
            center_x + max_radius * math.cos(radians) * 0.05,
            center_y + max_radius * math.sin(radians) * 0.05,
        )
        p2 = (center_x + max_radius * math.cos(r1), center_y + max_radius * math.sin(r1))
        p3 = (center_x + max_radius * math.cos(r2), center_y + max_radius * math.sin(r2))
        if i % 2 == 0:
            draw.polygon([p1, p2, p3], fill=(255, 255, 255, 40))

    draw.rounded_rectangle(
        (140, 250, THUMBNAIL_WIDTH - 140, 470), radius=30, fill=(*secondary, 230)
    )

    if portrait is not None:
        fitted = _fit_portrait(portrait, max_size=(360, 520))
        image.paste(
            fitted, (THUMBNAIL_WIDTH - fitted.width - 30, THUMBNAIL_HEIGHT - fitted.height), fitted
        )

    lines = _wrap_punchline(text, max_chars_per_line=11, max_lines=2)
    font = _font(70)
    y = 280
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = round((THUMBNAIL_WIDTH - text_w) / 2)
        _draw_outlined_text(draw, (x, y), line, font, fill=text_color, outline_width=6)
        y += 95
    return image


def _render_thumbnail(
    output_path: Path,
    *,
    text: str,
    layout: str,
    branding: SeriesBranding,
    context: _SeriesContext,
    portrait: Image.Image | None,
    video_project_id: str,
) -> None:
    accent = _hex_to_rgb(branding.accent_color)
    secondary = _hex_to_rgb(branding.secondary_color)
    text_color = _hex_to_rgb(branding.text_color)

    if layout == "clean":
        image = _render_clean(
            text, accent=accent, secondary=secondary, text_color=text_color, portrait=portrait
        )
    elif layout == "pop":
        image = _render_pop(
            text,
            accent=accent,
            secondary=secondary,
            text_color=text_color,
            portrait=portrait,
            seed=video_project_id,
        )
    else:
        image = _render_bold(
            text, accent=accent, secondary=secondary, text_color=text_color, portrait=portrait
        )

    if context.series_name is not None and context.position is not None:
        _draw_episode_badge(
            image,
            position=context.position,
            series_name=context.series_name,
            secondary_rgb=secondary,
            text_color=text_color,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output_path, "PNG")


def generate_thumbnail_candidates(session: Session, *, video_project_id: str) -> list[Asset]:
    """サムネイル候補3案を生成しAsset登録する(role="thumbnail:candidate:{i}"、冪等)。

    既存Assetの `meta.spec_version` が `THUMBNAIL_SPEC_VERSION` と一致し、かつファイルが
    存在する場合は再生成をスキップする(決定的描画のため、再生成しても内容は同一になる)。
    """
    project = _get_video_project(session, video_project_id)
    script = _get_script(session, project)
    settings = get_settings()

    body = script.body or {}
    texts = _resolve_thumbnail_texts(body)
    branding, context = _resolve_branding_and_context(session, project)
    speaker = _pick_speaker(body)
    portrait = _load_character_portrait(settings, speaker)

    assets: list[Asset] = []
    for index in range(THUMBNAIL_CANDIDATE_COUNT):
        role = _candidate_role(index)
        layout = _LAYOUTS[index % len(_LAYOUTS)]
        text = texts[index]
        relative_path = thumbnail_relative_path(video_project_id, f"candidate_{index}.png")
        output_path = resolve_generated_path(relative_path)

        existing = (
            session.query(Asset)
            .filter(Asset.video_project_id == video_project_id, Asset.role == role)
            .one_or_none()
        )
        if (
            existing is not None
            and (existing.meta or {}).get("spec_version") == THUMBNAIL_SPEC_VERSION
            and output_path.exists()
        ):
            assets.append(existing)
            continue

        _render_thumbnail(
            output_path,
            text=text,
            layout=layout,
            branding=branding,
            context=context,
            portrait=portrait,
            video_project_id=video_project_id,
        )
        checksum = compute_file_checksum(output_path)
        asset = _upsert_asset(
            session,
            video_project_id=video_project_id,
            asset_type="image",
            role=role,
            file_path=output_path,
            checksum=checksum,
            meta={"spec_version": THUMBNAIL_SPEC_VERSION, "text": text, "layout": layout},
        )
        assets.append(asset)

    session.flush()
    return assets


def select_thumbnail(session: Session, *, video_project_id: str, candidate_index: int) -> Asset:
    """候補ファイルを `selected.png` へコピーし role="thumbnail" でupsertする。"""
    if not 0 <= candidate_index < THUMBNAIL_CANDIDATE_COUNT:
        raise ThumbnailNotFoundError(
            f"candidate_index は0〜{THUMBNAIL_CANDIDATE_COUNT - 1}の範囲である必要があります: "
            f"{candidate_index}"
        )
    candidate_role = _candidate_role(candidate_index)
    candidate = (
        session.query(Asset)
        .filter(Asset.video_project_id == video_project_id, Asset.role == candidate_role)
        .one_or_none()
    )
    if candidate is None:
        raise ThumbnailNotFoundError(
            f"サムネイル候補がありません(video_project_id={video_project_id}, "
            f"candidate_index={candidate_index})。素材準備を先に実行してください"
        )
    source_path = Path(candidate.file_path)
    if not source_path.exists():
        raise ThumbnailNotFoundError(f"サムネイル候補ファイルが見つかりません: {source_path}")

    selected_path = resolve_generated_path(
        thumbnail_relative_path(video_project_id, "selected.png")
    )
    selected_path.parent.mkdir(parents=True, exist_ok=True)
    selected_path.write_bytes(source_path.read_bytes())
    checksum = compute_file_checksum(selected_path)

    asset = _upsert_asset(
        session,
        video_project_id=video_project_id,
        asset_type="image",
        role=THUMBNAIL_ROLE_SELECTED,
        file_path=selected_path,
        checksum=checksum,
        meta={
            "spec_version": THUMBNAIL_SPEC_VERSION,
            "candidate_index": candidate_index,
            "source_role": candidate_role,
        },
    )
    session.flush()
    return asset
