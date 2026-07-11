"""`app.schemas.production_settings.ProductionSettings` の単体テスト(Phase 2)。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.production_settings import ProductionSettings


def test_default_construction_is_short_preset() -> None:
    """引数なし構築は preset="short" 相当(現行挙動)になる。"""
    settings = ProductionSettings()
    assert settings.preset == "short"
    assert settings.video_format == "short"
    assert settings.target_duration_seconds == 45
    assert settings.min_duration_seconds == 30
    assert settings.max_duration_seconds == 60
    assert settings.min_sections == 1
    assert settings.max_sections == 3


def test_short_preset_values() -> None:
    settings = ProductionSettings.from_preset("short")
    assert settings.video_format == "short"
    assert (
        settings.target_duration_seconds,
        settings.min_duration_seconds,
        settings.max_duration_seconds,
    ) == (45, 30, 60)
    assert (settings.min_sections, settings.max_sections) == (1, 3)


def test_standard_3min_preset_values() -> None:
    settings = ProductionSettings.from_preset("standard_3min")
    assert settings.video_format == "standard"
    assert (
        settings.target_duration_seconds,
        settings.min_duration_seconds,
        settings.max_duration_seconds,
    ) == (180, 150, 210)
    assert (settings.min_sections, settings.max_sections) == (3, 5)


def test_standard_5min_preset_values() -> None:
    settings = ProductionSettings.from_preset("standard_5min")
    assert settings.video_format == "standard"
    assert (
        settings.target_duration_seconds,
        settings.min_duration_seconds,
        settings.max_duration_seconds,
    ) == (300, 270, 330)
    assert (settings.min_sections, settings.max_sections) == (4, 7)


def test_standard_8min_preset_values() -> None:
    settings = ProductionSettings.from_preset("standard_8min")
    assert settings.video_format == "standard"
    assert (
        settings.target_duration_seconds,
        settings.min_duration_seconds,
        settings.max_duration_seconds,
    ) == (480, 420, 540)
    assert (settings.min_sections, settings.max_sections) == (5, 9)


def test_custom_preset_derives_range_from_target_duration() -> None:
    """customは target_duration_seconds ±15% を min/max に自動展開する。"""
    settings = ProductionSettings(preset="custom", target_duration_seconds=200)
    assert settings.video_format == "custom"
    assert settings.min_duration_seconds == round(200 * 0.85)
    assert settings.max_duration_seconds == round(200 * 1.15)


def test_custom_preset_explicit_min_max_are_not_overridden() -> None:
    settings = ProductionSettings(
        preset="custom",
        target_duration_seconds=200,
        min_duration_seconds=100,
        max_duration_seconds=400,
    )
    assert settings.min_duration_seconds == 100
    assert settings.max_duration_seconds == 400


def test_checksum_is_stable_for_identical_settings() -> None:
    settings_a = ProductionSettings.from_preset("short")
    settings_b = ProductionSettings.from_preset("short")
    assert settings_a.checksum() == settings_b.checksum()


def test_checksum_changes_when_field_changes() -> None:
    baseline = ProductionSettings.from_preset("short")
    changed = ProductionSettings.from_preset("short").model_copy(update={"tone": "元気で明るい"})
    assert baseline.checksum() != changed.checksum()


def test_checksum_changes_across_presets() -> None:
    short_settings = ProductionSettings.from_preset("short")
    long_settings = ProductionSettings.from_preset("standard_5min")
    assert short_settings.checksum() != long_settings.checksum()


def test_target_character_count_defaults_to_duration_derived_value() -> None:
    """target_character_count未指定時は 300文字/分 × 分 × speaking_rate で導出する。"""
    settings = ProductionSettings.from_preset("short")  # target=45秒
    assert settings.resolved_target_character_count() == round(45 / 60 * 300)


def test_target_character_count_reflects_speaking_rate() -> None:
    settings = ProductionSettings(target_duration_seconds=60, speaking_rate=1.2)
    assert settings.resolved_target_character_count() == round(60 / 60 * 300 * 1.2)


def test_target_character_count_explicit_value_is_not_overridden() -> None:
    settings = ProductionSettings(target_character_count=999)
    assert settings.resolved_target_character_count() == 999


@pytest.mark.parametrize(
    "kwargs",
    [
        {"speaking_rate": 0},
        {"min_duration_seconds": 50, "target_duration_seconds": 45},
        {"target_duration_seconds": 70, "max_duration_seconds": 60},
        {"min_sections": 4, "max_sections": 3},
        {"preset": "short", "video_format": "custom"},
    ],
)
def test_invalid_production_settings_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ProductionSettings(**kwargs)
