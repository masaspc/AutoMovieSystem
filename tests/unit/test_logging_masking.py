from __future__ import annotations

from app.core.logging import mask_secrets_processor


def test_masks_values_for_sensitive_key_names() -> None:
    event_dict = {
        "event": "oauth_refresh",
        "token": "abc123def456",
        "authorization": "irrelevant-value",
        "user": "alice",
    }

    result = mask_secrets_processor(None, "info", dict(event_dict))

    assert result["token"] == "***"
    assert result["authorization"] == "***"
    assert result["user"] == "alice"


def test_masks_known_secret_patterns_embedded_in_values() -> None:
    event_dict = {
        "event": "llm_call",
        "message": "using key sk-abcdefghijklmnop for request",
    }

    result = mask_secrets_processor(None, "info", dict(event_dict))

    assert "sk-abcdefghijklmnop" not in result["message"]
    assert "***" in result["message"]


def test_masks_nested_dict_values() -> None:
    event_dict = {
        "event": "request",
        "headers": {"Authorization": "Bearer sometoken12345678", "Content-Type": "json"},
    }

    result = mask_secrets_processor(None, "info", dict(event_dict))

    assert result["headers"]["Authorization"] == "***"
    assert result["headers"]["Content-Type"] == "json"
