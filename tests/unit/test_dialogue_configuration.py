from __future__ import annotations

import asyncio

import pytest

from app.core.config import get_settings
from app.providers.llm.fake import DeterministicFakeLLMProvider
from app.schemas.script_content import ScriptContent
from app.services.publishing.uploader import _with_voicevox_credits


def test_fake_script_omits_tsumugi_when_cast_is_two_characters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIALOGUE_CAST", "zundamon,metan")
    monkeypatch.setenv("DIALOGUE_SCRIPT_ENABLED", "true")
    get_settings.cache_clear()
    provider = DeterministicFakeLLMProvider()

    result = asyncio.run(
        provider.generate_structured(
            operation="generate_script",
            system_prompt="sys",
            user_prompt="usr",
            response_schema=ScriptContent,
            model_policy="mid",
            idempotency_key="cast-test",
        )
    )

    speakers = {
        line["speaker"]
        for section in result.data["sections"]
        for line in section.get("dialogue", [])
    }
    assert speakers == {"zundamon", "metan"}


def test_fake_script_has_no_dialogue_without_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIALOGUE_SCRIPT_ENABLED", "false")
    get_settings.cache_clear()
    provider = DeterministicFakeLLMProvider()

    result = asyncio.run(
        provider.generate_structured(
            operation="generate_script",
            system_prompt="sys",
            user_prompt="usr",
            response_schema=ScriptContent,
            model_policy="mid",
            idempotency_key="dialogue-disabled-test",
        )
    )

    assert all(not section.get("dialogue") for section in result.data["sections"])


def test_voicevox_credits_are_added_only_for_voicevox(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "sections": [
            {
                "dialogue": [
                    {"speaker": "zundamon", "text": "こんにちは"},
                    {"speaker": "metan", "text": "解説します"},
                ]
            }
        ]
    }
    monkeypatch.setenv("TTS_PROVIDER", "fake")
    get_settings.cache_clear()
    assert _with_voicevox_credits("説明", body) == "説明"

    monkeypatch.setenv("TTS_PROVIDER", "voicevox")
    monkeypatch.setenv("DIALOGUE_SCRIPT_ENABLED", "true")
    get_settings.cache_clear()
    description = _with_voicevox_credits("説明", body)
    assert "VOICEVOX:ずんだもん" in description
    assert "VOICEVOX:四国めたん" in description


def test_voicevox_credits_are_not_added_when_dialogue_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {"sections": [{"dialogue": [{"speaker": "zundamon", "text": "こんにちは"}]}]}
    monkeypatch.setenv("TTS_PROVIDER", "voicevox")
    monkeypatch.setenv("DIALOGUE_SCRIPT_ENABLED", "false")
    get_settings.cache_clear()

    assert _with_voicevox_credits("説明", body) == "説明"
