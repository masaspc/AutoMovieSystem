#!/usr/bin/env pwsh
# Windows開発用ラッパー(D-005)。Makefileと同一ターゲット名を提供する。
# 使い方: ./scripts/dev.ps1 <target>

param(
    [Parameter(Position = 0, Mandatory = $true)]
    [string]$Target
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Invoke-Checked {
    param([string[]]$CommandParts)
    Write-Host "+ $($CommandParts -join ' ')" -ForegroundColor Cyan
    & $CommandParts[0] @($CommandParts[1..($CommandParts.Length - 1)])
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $($CommandParts -join ' ')"
    }
}

switch ($Target) {
    "setup" {
        Invoke-Checked @("uv", "sync")
    }
    "up" {
        Invoke-Checked @("docker", "compose", "up", "-d", "--build")
    }
    "down" {
        Invoke-Checked @("docker", "compose", "down")
    }
    "migrate" {
        Invoke-Checked @("uv", "run", "alembic", "upgrade", "head")
    }
    "seed" {
        Invoke-Checked @("uv", "run", "python", "scripts/seed.py")
    }
    "lint" {
        Invoke-Checked @("uv", "run", "ruff", "check", ".")
    }
    "typecheck" {
        Invoke-Checked @("uv", "run", "mypy", "app")
    }
    "test" {
        Invoke-Checked @("uv", "run", "pytest", "-q")
    }
    "test-unit" {
        Invoke-Checked @("uv", "run", "pytest", "tests/unit", "-q")
    }
    "test-integration" {
        Invoke-Checked @("uv", "run", "pytest", "tests/integration", "-q", "-m", "integration")
    }
    "test-e2e" {
        Invoke-Checked @("uv", "run", "pytest", "tests/e2e", "-q", "-m", "e2e")
    }
    "demo" {
        Invoke-Checked @("uv", "run", "python", "scripts/demo.py")
    }
    "security-check" {
        Invoke-Checked @("uv", "run", "pip", "check")
        Write-Host "TODO: add pip-audit once dependency scanning is wired up"
    }
    "clean-generated" {
        Invoke-Checked @(
            "uv", "run", "python", "-c",
            "import shutil; from pathlib import Path; p = Path('generated'); shutil.rmtree(p, ignore_errors=True); p.mkdir(exist_ok=True)"
        )
    }
    default {
        throw "Unknown target: $Target"
    }
}
