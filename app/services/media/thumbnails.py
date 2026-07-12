"""シリーズ統一サムネイル自動生成(CTR向上・動画ごとに3案生成し管理画面で選択)。

デザイン方針(運用フィードバック反映):
- YouTubeトレンドに合わせた「テキスト主体・インパクト重視」。立ち絵は使わない。
- 文字は実測幅ベースの自動折り返し+自動縮小で描画するため、見切れは構造的に起きない。
- 同一シリーズは `app.services.media.branding.SeriesBranding` により常に同じ配色・
  エピソード番号バッジを持つ「番組」として統一される。

描画は全て決定的(乱数はvideo_project_idシードの決定的擬似乱数のみ)であり、
同一入力からの再生成は常に同一チェックサムのファイルを生成する。
`THUMBNAIL_SPEC_VERSION` を上げるとレイアウト実装変更を全既存動画へ反映できる
(既存Assetの `meta.spec_version` が現行と不一致になり再生成される)。
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from app.core.paths import resolve_generated_path
from app.models.asset import Asset
from app.models.channel import Channel
from app.models.episode_plan import EpisodePlan
from app.models.series_plan import SeriesPlan
from app.models.topic import Topic
from app.models.video_project import VideoProject
from app.services.media.branding import SeriesBranding, resolve_branding
from app.services.media.pipeline import _get_script, _get_video_project, _upsert_asset
from app.services.media.renderer import compute_file_checksum, find_japanese_font

THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720
# レイアウト実装(色・座標・合成方法等)を変えたら必ずインクリメントする。
# 既存Assetの meta.spec_version と不一致になり、次回 generate_thumbnail_candidates 実行時
# に再生成される(prepare_assets自体の冪等キーは変えない)。
# v2: テキスト主体デザインへ刷新(立ち絵廃止・自動フィットで見切れ解消・
#     impact/split/minimalの3レイアウト)。
# v3: キーワード部分色強調(《》マーカー+数字の自動強調をアクセントイエローで描画)。
THUMBNAIL_SPEC_VERSION = 3
THUMBNAIL_CANDIDATE_COUNT = 3
THUMBNAIL_ROLE_PREFIX = "thumbnail:candidate:"
THUMBNAIL_ROLE_SELECTED = "thumbnail"

# 候補indexごとに固定されたレイアウト。
_LAYOUTS: tuple[str, ...] = ("impact", "split", "minimal")

# エピソードバッジ等が占有する上部の予約領域(テキストはこの下から描画する)。
_TOP_RESERVED = 170
_SIDE_MARGIN = 64

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
        # 旧台本(thumbnail_texts無し)はタイトル全文を使う。切り詰めはしない
        # (描画側の自動フィットが折り返し・縮小で必ず収める)。
        candidates = body.get("title_candidates") or []
        fallback = str(candidates[0]).strip() if candidates else "今日のポイント"
        raw_texts = [fallback or "今日のポイント"]
    while len(raw_texts) < THUMBNAIL_CANDIDATE_COUNT:
        raw_texts.append(raw_texts[-1])
    return raw_texts[:THUMBNAIL_CANDIDATE_COUNT]


# ---------------------------------------------------------------------------
# 自動フィットのテキストエンジン(見切れを構造的に不可能にする)
# ---------------------------------------------------------------------------

# キーワード強調色(YouTubeサムネイル定番の高視認イエロー)。
_EMPHASIS_COLOR = (255, 233, 74)
# LLMが《》で囲んだ語句を強調する。マーカーが無い場合は数字(+単位1文字)を自動強調。
_MARKER_RE = re.compile(r"《([^》]*)》")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?[%%万円分秒倍個回]?")


def parse_emphasis(text: str) -> tuple[str, list[bool]]:
    """《》マーカーを解釈し、(プレーン文字列, 文字ごとの強調フラグ) を返す。

    マーカーが1つも無い場合は数字(単位1文字を含む)を自動で強調対象にする
    (「9割が知らない」→「9割」が強調色になる)。
    """
    if _MARKER_RE.search(text):
        plain_chars: list[str] = []
        flags: list[bool] = []
        emphasized = False
        for char in text:
            if char == "《":
                emphasized = True
                continue
            if char == "》":
                emphasized = False
                continue
            plain_chars.append(char)
            flags.append(emphasized)
        return "".join(plain_chars), flags

    flags = [False] * len(text)
    for match in _NUMBER_RE.finditer(text):
        for index in range(match.start(), match.end()):
            flags[index] = True
    return text, flags


def _wrap_by_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    """実測幅で貪欲に折り返す(CJKは1文字単位、英数字は単語単位を優先)。"""
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


@dataclass(frozen=True)
class _FittedText:
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    lines: list[str]
    line_height: int

    @property
    def total_height(self) -> int:
        return self.line_height * len(self.lines)


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    max_width: int,
    max_height: int,
    max_lines: int,
    start_size: int = 170,
    min_size: int = 40,
) -> _FittedText:
    """幅・高さ・行数の制約内で「行数が少ない」ことを優先して最大フォントを探す。

    行数優先にすることで「変数を完全理/解」のような1文字だけ次行へ落ちる
    不格好な折り返しを避ける(1行で収まるサイズがあればそちらを選ぶ)。
    最小サイズでも収まらない場合は最小サイズの結果を返す(実用上、min_size=40で
    max_lines行に収まらない日本語パンチラインはthumbnail_textsの想定外の長文のみ)。
    """
    for target_lines in range(1, max_lines + 1):
        size = start_size
        while size >= min_size:
            font = _font(size)
            lines = _wrap_by_width(draw, text, font, max_width)
            line_height = round(size * 1.22)
            if len(lines) <= target_lines and line_height * len(lines) <= max_height:
                return _FittedText(font=font, lines=lines, line_height=line_height)
            size -= 8
    font = _font(min_size)
    lines = _wrap_by_width(draw, text, font, max_width)[:max_lines]
    return _FittedText(font=font, lines=lines, line_height=round(min_size * 1.22))


def _draw_outlined_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    *,
    fill: tuple[int, int, int],
    outline: tuple[int, int, int] = (0, 0, 0),
    outline_width: int = 10,
) -> None:
    x, y = xy
    step = max(2, outline_width // 3)
    for dx in range(-outline_width, outline_width + 1, step):
        for dy in range(-outline_width, outline_width + 1, step):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


def _draw_fitted_block(
    draw: ImageDraw.ImageDraw,
    fitted: _FittedText,
    *,
    top: int,
    align: str,
    text_color: tuple[int, int, int],
    outline: tuple[int, int, int] = (0, 0, 0),
    highlight: tuple[int, int, int] | None = None,
    emphasis_flags: list[bool] | None = None,
) -> None:
    """折り返し済みテキストブロックを描画する。

    highlight指定時は行背景帯を敷く。emphasis_flags(プレーン文字列に対する文字ごとの
    強調フラグ)指定時は、該当部分だけ強調色(_EMPHASIS_COLOR)で描く。
    """
    y = top
    consumed = 0
    for line in fitted.lines:
        bbox = draw.textbbox((0, 0), line, font=fitted.font)
        line_width = int(bbox[2] - bbox[0])
        x = (THUMBNAIL_WIDTH - line_width) // 2 if align == "center" else _SIDE_MARGIN
        if highlight is not None:
            pad = 14
            draw.rectangle(
                (x - pad, y - 4, x + line_width + pad, y + fitted.line_height - 8),
                fill=highlight,
            )
        line_flags = (
            emphasis_flags[consumed : consumed + len(line)]
            if emphasis_flags is not None
            else [False] * len(line)
        )
        if any(line_flags):
            # 同一強調状態の連続区間(run)ごとに色を切り替えて描画する。
            cursor = float(x)
            run_start = 0
            for index in range(1, len(line) + 1):
                if index == len(line) or line_flags[index] != line_flags[run_start]:
                    run = line[run_start:index]
                    color = _EMPHASIS_COLOR if line_flags[run_start] else text_color
                    _draw_outlined_text(
                        draw, (round(cursor), y), run, fitted.font, fill=color, outline=outline
                    )
                    cursor += draw.textlength(run, font=fitted.font)
                    run_start = index
        else:
            _draw_outlined_text(draw, (x, y), line, fitted.font, fill=text_color, outline=outline)
        consumed += len(line)
        y += fitted.line_height


# ---------------------------------------------------------------------------
# レイアウト(テキスト主体・インパクト重視)
# ---------------------------------------------------------------------------


def _text_area(context: _SeriesContext) -> tuple[int, int]:
    """テキストブロックに使える(top, height)。シリーズバッジがある場合は上部を予約する。"""
    top = _TOP_RESERVED if context.position is not None else 90
    return top, THUMBNAIL_HEIGHT - top - 70


def _render_impact(
    text: str,
    *,
    emphasis_flags: list[bool],
    accent: tuple[int, int, int],
    secondary: tuple[int, int, int],
    text_color: tuple[int, int, int],
    context: _SeriesContext,
) -> Image.Image:
    """濃色背景+極太テキスト+斜めアクセント帯。"""
    image = Image.new("RGB", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), secondary)
    draw = ImageDraw.Draw(image, "RGBA")
    # 下部の斜めアクセント帯(テキストの背後で視線を集める)。
    draw.polygon(
        [
            (0, THUMBNAIL_HEIGHT),
            (0, THUMBNAIL_HEIGHT * 0.62),
            (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT * 0.82),
            (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT),
        ],
        fill=(*accent, 255),
    )
    top, height = _text_area(context)
    fitted = _fit_text(
        draw,
        text,
        max_width=THUMBNAIL_WIDTH - _SIDE_MARGIN * 2,
        max_height=height,
        max_lines=3,
    )
    block_top = top + max(0, (height - fitted.total_height) // 2)
    _draw_fitted_block(
        draw,
        fitted,
        top=block_top,
        align="left",
        text_color=text_color,
        emphasis_flags=emphasis_flags,
    )
    return image


def _render_split(
    text: str,
    *,
    emphasis_flags: list[bool],
    accent: tuple[int, int, int],
    secondary: tuple[int, int, int],
    text_color: tuple[int, int, int],
    context: _SeriesContext,
) -> Image.Image:
    """アクセント色ベタ+行ハイライト帯(マーカー風)の中央寄せ。"""
    image = Image.new("RGB", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), accent)
    draw = ImageDraw.Draw(image, "RGBA")
    top, height = _text_area(context)
    fitted = _fit_text(
        draw,
        text,
        max_width=THUMBNAIL_WIDTH - _SIDE_MARGIN * 2 - 40,
        max_height=height,
        max_lines=3,
        start_size=150,
    )
    block_top = top + max(0, (height - fitted.total_height) // 2)
    _draw_fitted_block(
        draw,
        fitted,
        top=block_top,
        align="center",
        text_color=text_color,
        highlight=secondary,
        emphasis_flags=emphasis_flags,
    )
    return image


def _render_minimal(
    text: str,
    *,
    emphasis_flags: list[bool],
    accent: tuple[int, int, int],
    secondary: tuple[int, int, int],
    text_color: tuple[int, int, int],
    context: _SeriesContext,
    seed: str,
) -> Image.Image:
    """ほぼ黒背景+集中線(控えめ)+中央極太テキスト+下部アクセントバー。"""
    base = (16, 18, 24)
    image = Image.new("RGB", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), base)
    draw = ImageDraw.Draw(image, "RGBA")

    # 控えめな集中線(video_project_idシードの決定的擬似乱数、暗号用途ではない)。
    rng = random.Random(f"thumbnail-minimal:{seed}")  # noqa: S311
    center_x, center_y = THUMBNAIL_WIDTH // 2, THUMBNAIL_HEIGHT // 2
    for i in range(20):
        angle = (360 / 20) * i + rng.uniform(-5, 5)
        width_deg = rng.uniform(2.5, 5)
        r1 = math.radians(angle - width_deg)
        r2 = math.radians(angle + width_deg)
        p2 = (center_x + 1400 * math.cos(r1), center_y + 1400 * math.sin(r1))
        p3 = (center_x + 1400 * math.cos(r2), center_y + 1400 * math.sin(r2))
        if i % 2 == 0:
            draw.polygon([(center_x, center_y), p2, p3], fill=(*secondary, 55))

    draw.rectangle(
        (0, THUMBNAIL_HEIGHT - 26, THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), fill=(*accent, 255)
    )
    top, height = _text_area(context)
    fitted = _fit_text(
        draw,
        text,
        max_width=THUMBNAIL_WIDTH - _SIDE_MARGIN * 2,
        max_height=height,
        max_lines=3,
    )
    block_top = top + max(0, (height - fitted.total_height) // 2)
    _draw_fitted_block(
        draw,
        fitted,
        top=block_top,
        align="center",
        text_color=text_color,
        outline=accent,
        emphasis_flags=emphasis_flags,
    )
    return image


def _draw_episode_badge(
    image: Image.Image,
    *,
    position: int,
    series_name: str,
    accent_rgb: tuple[int, int, int],
    text_color: tuple[int, int, int],
) -> None:
    """左上のエピソード番号+シリーズ名(上部の予約領域内に収める)。"""
    draw = ImageDraw.Draw(image, "RGBA")
    label = f"#{position}"
    number_font = _font(72)
    bbox = draw.textbbox((0, 0), label, font=number_font)
    number_width = bbox[2] - bbox[0]

    badge_width = number_width + 56
    draw.rounded_rectangle(
        (28, 26, 28 + badge_width, 128),
        radius=18,
        fill=(*accent_rgb, 255),
        outline=(255, 255, 255, 255),
        width=4,
    )
    draw.text((28 + 28 - bbox[0], 26 + (102 - (bbox[3] - bbox[1])) // 2 - bbox[1]), label,
              font=number_font, fill=text_color)

    name_font = _font(30)
    name = series_name[:18]
    name_bbox = draw.textbbox((0, 0), name, font=name_font)
    name_width = name_bbox[2] - name_bbox[0]
    x0 = 28 + badge_width + 16
    draw.rounded_rectangle(
        (x0, 44, x0 + name_width + 32, 110), radius=12, fill=(0, 0, 0, 190)
    )
    draw.text((x0 + 16, 58), name, font=name_font, fill=(255, 255, 255))


def _render_thumbnail(
    output_path: Path,
    *,
    text: str,
    layout: str,
    branding: SeriesBranding,
    context: _SeriesContext,
    video_project_id: str,
) -> None:
    accent = _hex_to_rgb(branding.accent_color)
    secondary = _hex_to_rgb(branding.secondary_color)
    text_color = _hex_to_rgb(branding.text_color)
    plain_text, emphasis_flags = parse_emphasis(text)

    if layout == "split":
        image = _render_split(
            plain_text,
            emphasis_flags=emphasis_flags,
            accent=accent,
            secondary=secondary,
            text_color=text_color,
            context=context,
        )
    elif layout == "minimal":
        image = _render_minimal(
            plain_text,
            emphasis_flags=emphasis_flags,
            accent=accent,
            secondary=secondary,
            text_color=text_color,
            context=context,
            seed=video_project_id,
        )
    else:
        image = _render_impact(
            plain_text,
            emphasis_flags=emphasis_flags,
            accent=accent,
            secondary=secondary,
            text_color=text_color,
            context=context,
        )

    if context.series_name is not None and context.position is not None:
        _draw_episode_badge(
            image,
            position=context.position,
            series_name=context.series_name,
            accent_rgb=accent,
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

    body = script.body or {}
    texts = _resolve_thumbnail_texts(body)
    branding, context = _resolve_branding_and_context(session, project)

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
            meta={
                "spec_version": THUMBNAIL_SPEC_VERSION,
                "text": parse_emphasis(text)[0],
                "layout": layout,
            },
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
