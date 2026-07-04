from __future__ import annotations

import pytest

from app.core.paths import resolve_generated_path


def test_rejects_absolute_path() -> None:
    with pytest.raises(ValueError):
        resolve_generated_path("/etc/passwd")


def test_rejects_parent_traversal() -> None:
    with pytest.raises(ValueError):
        resolve_generated_path("../outside.txt")


def test_rejects_nested_parent_traversal() -> None:
    with pytest.raises(ValueError):
        resolve_generated_path("videos/../../outside.txt")


def test_allows_valid_relative_path() -> None:
    path = resolve_generated_path("videos/sample.mp4")
    assert path.name == "sample.mp4"
    assert "generated" in path.parts
