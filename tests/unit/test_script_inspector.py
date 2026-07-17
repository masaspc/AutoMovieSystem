from __future__ import annotations

import pytest

from app.models.script import Script
from app.services.scripts.inspector import (
    SEVERITY_BLOCKING,
    SEVERITY_WARNING,
    inspect_script,
)


def _base_body(**overrides: object) -> dict:
    body = {
        "title_candidates": ["タイトル案"],
        "target_audience": "視聴者",
        "viewer_problem": "課題",
        "promised_outcome": "得られる成果",
        "hook": "フック文",
        "sections": [
            {
                "heading": "導入",
                "narration": "今日は基礎を解説します。",
                "visual_instruction": "テロップ表示",
                "evidence_ids": [],
            }
        ],
        "conclusion": "まとめ",
        "call_to_action": "チャンネル登録してください",
        "description": "説明",
        "tags": ["tag"],
        "chapters": ["導入"],
    }
    body.update(overrides)
    return body


def _make_script(body: dict, *, source_manifest: dict | None = None, **kwargs: object) -> Script:
    return Script(
        id="script-1",
        topic_id="topic-1",
        version=kwargs.get("version", 1),
        title=kwargs.get("title", "タイトル"),
        hook=kwargs.get("hook", body.get("hook", "フック")),
        body=body,
        conclusion=body.get("conclusion"),
        call_to_action=body.get("call_to_action"),
        source_manifest=source_manifest or {"evidence_ids": []},
        status="draft",
    )


def test_empty_title_candidates_and_sections_are_blocking() -> None:
    body = _base_body(title_candidates=[], sections=[])
    script = _make_script(body)

    findings = inspect_script(script)
    codes = {f.code for f in findings}

    assert "empty_title_candidates" in codes
    assert "empty_sections" in codes
    assert all(f.severity == SEVERITY_BLOCKING for f in findings if f.code in codes)


def test_empty_hook_and_missing_promised_outcome_are_blocking() -> None:
    body = _base_body(promised_outcome="")
    script = _make_script(body, hook="")

    findings = inspect_script(script)
    codes = {f.code for f in findings}

    assert "empty_hook" in codes
    assert "missing_promised_outcome" in codes


def test_unsupported_number_without_evidence_is_warning() -> None:
    body = _base_body(
        sections=[
            {
                "heading": "本編",
                "narration": "参加者は120人でした。",
                "visual_instruction": "表示",
                "evidence_ids": [],
            }
        ]
    )
    script = _make_script(body)

    findings = inspect_script(script)
    codes = {f.code: f.severity for f in findings}

    assert codes.get("unsupported_number") == SEVERITY_WARNING


def test_unsupported_important_number_without_evidence_is_blocking() -> None:
    body = _base_body(
        sections=[
            {
                "heading": "本編",
                "narration": "売上が30%増加しました。",
                "visual_instruction": "表示",
                "evidence_ids": [],
            }
        ]
    )
    script = _make_script(body)

    findings = inspect_script(script)
    codes = {f.code: f.severity for f in findings}

    assert codes.get("unsupported_important_number") == SEVERITY_BLOCKING


def test_evidence_id_not_in_manifest_is_blocking() -> None:
    body = _base_body(
        sections=[
            {
                "heading": "本編",
                "narration": "調査によると効果があります。",
                "visual_instruction": "表示",
                "evidence_ids": ["evidence-not-in-manifest"],
            }
        ]
    )
    script = _make_script(body, source_manifest={"evidence_ids": ["evidence-x"]})

    findings = inspect_script(script)
    codes = {f.code for f in findings}

    assert "evidence_id_not_in_manifest" in codes


def test_evidence_id_in_manifest_suppresses_number_findings() -> None:
    body = _base_body(
        sections=[
            {
                "heading": "本編",
                "narration": "売上が30%増加しました。",
                "visual_instruction": "表示",
                "evidence_ids": ["evidence-x"],
            }
        ]
    )
    script = _make_script(body, source_manifest={"evidence_ids": ["evidence-x"]})

    findings = inspect_script(script)
    codes = {f.code for f in findings}

    assert "unsupported_important_number" not in codes
    assert "unsupported_number" not in codes


@pytest.mark.parametrize("expression", ["絶対に儲かる", "必ず儲かる", "絶対に上がる", "損しない"])
def test_prohibited_expression_is_blocking(expression: str) -> None:
    body = _base_body(
        sections=[
            {
                "heading": "本編",
                "narration": f"この方法なら{expression}という説明です。",
                "visual_instruction": "表示",
                "evidence_ids": [],
            }
        ]
    )
    script = _make_script(body)

    findings = inspect_script(script)
    codes = {f.code: f.severity for f in findings}

    assert codes.get("prohibited_expression") == SEVERITY_BLOCKING


def test_sentence_too_long_is_warning() -> None:
    long_sentence = "あ" * 201
    body = _base_body(
        sections=[
            {
                "heading": "本編",
                "narration": long_sentence,
                "visual_instruction": "表示",
                "evidence_ids": ["evidence-x"],
            }
        ]
    )
    script = _make_script(body, source_manifest={"evidence_ids": ["evidence-x"]})

    findings = inspect_script(script)
    codes = {f.code: f.severity for f in findings}

    assert codes.get("sentence_too_long") == SEVERITY_WARNING


def test_possible_secret_leak_is_blocking() -> None:
    body = _base_body(
        sections=[
            {
                "heading": "本編",
                "narration": "設定キーはsk-abcdefgh12345678です。",
                "visual_instruction": "表示",
                "evidence_ids": [],
            }
        ]
    )
    script = _make_script(body)

    findings = inspect_script(script)
    codes = {f.code: f.severity for f in findings}

    assert codes.get("possible_secret_leak") == SEVERITY_BLOCKING


def test_similar_to_previous_version_is_warning() -> None:
    body = _base_body()
    script = _make_script(body, version=2, title="同じタイトルです", hook="同じフックです")
    previous = _make_script(body, version=1, title="同じタイトルです", hook="同じフックです")

    findings = inspect_script(script, previous_scripts=[previous])
    codes = {f.code: f.severity for f in findings}

    assert codes.get("similar_to_previous_version") == SEVERITY_WARNING


def test_no_findings_for_clean_script() -> None:
    body = _base_body()
    script = _make_script(body)

    findings = inspect_script(script)

    assert findings == []
