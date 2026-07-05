"""レビュー共通のFinding型(仕様§12)。

`app/services/scripts/inspector.py` / `app/services/media/probe.py` の Finding と
同じ `code`/`severity`/`message` を持ち、Review モデルへの保存用に `detail` を追加した形。
"""

from __future__ import annotations

from dataclasses import dataclass

SEVERITY_BLOCKING = "blocking"
SEVERITY_WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str
    detail: str | None = None


def blocking_findings(findings: list[Finding]) -> list[Finding]:
    """severity=="blocking" のFindingのみを抽出する。"""
    return [f for f in findings if f.severity == SEVERITY_BLOCKING]


def to_dict(finding: Finding) -> dict:
    """Review.findings/blocking_findings のJSON保存形式に変換する。"""
    return {
        "code": finding.code,
        "severity": finding.severity,
        "message": finding.message,
        "detail": finding.detail,
    }
