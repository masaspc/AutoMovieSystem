from __future__ import annotations

from PIL import Image, ImageDraw

from app.services.media.variety import (
    ACCENT_HUE_SHIFTS,
    HEADING_STYLES,
    KEN_BURNS_STYLES,
    TRANSITION_STYLES,
    animate_heading_overlay,
    ken_burns_filter,
    pick_visual_variety_plan,
    shift_accent_hue,
)


def test_visual_variety_plan_is_deterministic_and_uses_catalogs() -> None:
    first = pick_visual_variety_plan("project-42")
    assert first == pick_visual_variety_plan("project-42")
    assert first.ken_burns_style in KEN_BURNS_STYLES
    assert first.transition_style in TRANSITION_STYLES
    assert first.heading_style in HEADING_STYLES
    assert first.accent_hue_shift in ACCENT_HUE_SHIFTS


def test_visual_variety_seeds_produce_multiple_combinations() -> None:
    plans = {pick_visual_variety_plan(f"project-{index}") for index in range(24)}
    assert len(plans) >= 8
    assert len({plan.ken_burns_style for plan in plans}) == len(KEN_BURNS_STYLES)
    assert len({plan.transition_style for plan in plans}) == len(TRANSITION_STYLES)


def test_ken_burns_catalog_has_distinct_filters_and_fail_soft_default() -> None:
    filters = {style: ken_burns_filter(style) for style in KEN_BURNS_STYLES}
    assert len(set(filters.values())) == len(KEN_BURNS_STYLES)
    assert ken_burns_filter("unknown") == filters["zoom_in_left"]


def test_accent_hue_shift_is_deterministic_and_bounded() -> None:
    color = (66, 153, 225)
    shifted = shift_accent_hue(color, 12)
    assert shifted == shift_accent_hue(color, 12)
    assert shifted != color
    assert all(0 <= component <= 255 for component in shifted)


def test_heading_entrance_styles_change_over_time() -> None:
    heading = Image.new("RGBA", (400, 120), (0, 0, 0, 0))
    ImageDraw.Draw(heading).rectangle((40, 20, 360, 100), fill=(40, 180, 240, 255))
    for style in HEADING_STYLES:
        early = animate_heading_overlay(heading, style=style, progress=0.25)
        late = animate_heading_overlay(heading, style=style, progress=0.75)
        assert early.tobytes() != late.tobytes()
