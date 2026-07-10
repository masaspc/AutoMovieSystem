#!/usr/bin/env pwsh
# Windows + Docker Desktop + Ollama 向けのローカルAI一括セットアップ。
# 使い方: .\scripts\setup-local-ai.ps1
# 軽量モデルにする場合: .\scripts\setup-local-ai.ps1 -Model qwen3:8b

[CmdletBinding()]
param(
    # RTX 4090 (24GB VRAM) では Qwen3 32B の量子化モデルを第一候補にする。
    [string]$Model = "qwen3:32b",
    # 立ち絵を配置済みの場合だけ有効にする。素材が不足していれば設定前に停止する。
    [switch]$EnableCharacterVideo,
    # つむぎを外す場合: -DialogueCast "zundamon,metan"
    [string]$DialogueCast = "zundamon,metan,tsumugi",
    # Ollamaの導入とモデル取得だけ行い、Docker Composeは起動しない。
    [switch]$SkipCompose,
    # 既にモデルを取得済みの場合に使用する。
    [switch]$SkipModelPull
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$EnvPath = Join-Path $RepoRoot ".env"
$EnvExamplePath = Join-Path $RepoRoot ".env.example"

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Get-OllamaExecutable {
    $command = Get-Command ollama -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"),
        (Join-Path $env:ProgramFiles "Ollama\ollama.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

function Test-OllamaRunning {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 `
            "http://localhost:11434/api/tags"
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Wait-ForOllama {
    param([int]$TimeoutSeconds = 45)

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-OllamaRunning) {
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "Ollamaを http://localhost:11434 で起動できませんでした。Ollamaアプリを起動してから再実行してください。"
}

function Set-DotEnvValue {
    param(
        [string]$Key,
        [string]$Value
    )

    $content = Get-Content -LiteralPath $EnvPath -Raw
    $newline = if ($content.Contains("`r`n")) { "`r`n" } else { "`n" }
    $keyPattern = [regex]::Escape($Key)
    $pattern = "(?m)^$keyPattern=.*(?:\r?\n|$)"
    $replacement = "$Key=$Value$newline"

    if ([regex]::IsMatch($content, $pattern)) {
        $content = [regex]::Replace($content, $pattern, $replacement, 1)
    } else {
        $content = $content.TrimEnd([char[]]"`r`n") + $newline + $replacement
    }
    Set-Content -LiteralPath $EnvPath -Value $content -NoNewline -Encoding utf8
}

function Get-DotEnvValue {
    param([string]$Key)

    $content = Get-Content -LiteralPath $EnvPath -Raw
    $keyPattern = [regex]::Escape($Key)
    $match = [regex]::Match($content, "(?m)^$keyPattern=(.*)$")
    if ($match.Success) {
        return $match.Groups[1].Value.Trim()
    }
    return ""
}

function Add-ComposeProfile {
    param([string]$Profile)

    $profiles = @((Get-DotEnvValue "COMPOSE_PROFILES") -split "," | Where-Object { $_ })
    if ($profiles -notcontains $Profile) {
        $profiles += $Profile
        Set-DotEnvValue "COMPOSE_PROFILES" ($profiles -join ",")
    }
}

function Assert-DockerReady {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $docker) {
        throw "Docker Desktopが見つかりません。Docker Desktopをインストールして起動してから再実行してください。"
    }

    & $docker.Source info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Engineに接続できません。Docker Desktopを起動し、初回利用規約に同意してから再実行してください。"
    }
    return $docker.Source
}

function Assert-CharacterAssets {
    param([string[]]$Characters)

    if (("zundamon" -notin $Characters) -or ("metan" -notin $Characters)) {
        throw "DialogueCastには zundamon と metan を含めてください。"
    }

    $missing = foreach ($character in $Characters) {
        $normal = Join-Path $RepoRoot "assets\characters\$character\normal.png"
        if (-not (Test-Path -LiteralPath $normal)) {
            $normal
        }
    }
    if ($missing) {
        $missingList = $missing -join "`n - "
        throw "立ち絵を有効化する前に、利用条件を確認済みの公式素材を配置してください:`n - $missingList"
    }
}

Set-Location $RepoRoot

if ($EnableCharacterVideo) {
    $characters = @($DialogueCast.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    Assert-CharacterAssets $characters
}

$docker = $null
if (-not $SkipCompose) {
    Write-Step "Docker Desktopを確認します"
    $docker = Assert-DockerReady
}

Write-Step "Ollamaを確認します"
$ollama = Get-OllamaExecutable
if ($null -eq $ollama) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($null -eq $winget) {
        throw "Ollamaが未導入でwingetも見つかりません。Ollamaを手動でインストールしてから再実行してください。"
    }

    Write-Step "Ollamaをインストールします"
    & $winget.Source install --id Ollama.Ollama --exact --accept-package-agreements `
        --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) {
        throw "Ollamaのインストールに失敗しました。"
    }
    $ollama = Get-OllamaExecutable
    if ($null -eq $ollama) {
        throw "Ollamaはインストールされましたが、実行ファイルを見つけられません。PowerShellを開き直してから再実行してください。"
    }
}

if (-not (Test-OllamaRunning)) {
    Write-Step "Ollamaサーバーを起動します"
    Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
    Wait-ForOllama
}

if (-not $SkipModelPull) {
    Write-Step "モデル $Model を取得します。初回は数GBから数十GBのダウンロードになります"
    & $ollama pull $Model
    if ($LASTEXITCODE -ne 0) {
        throw "モデル $Model の取得に失敗しました。"
    }
}

if (-not (Test-Path -LiteralPath $EnvPath)) {
    if (-not (Test-Path -LiteralPath $EnvExamplePath)) {
        throw ".env.example が見つかりません。プロジェクト直下から実行してください。"
    }
    Write-Step ".env を作成します"
    Copy-Item -LiteralPath $EnvExamplePath -Destination $EnvPath
}

Write-Step "外部LLM APIを使わないローカルLLM設定を反映します"
Set-DotEnvValue "LLM_PROVIDER" "local"
Set-DotEnvValue "LLM_PROVIDER_LOW" "local"
Set-DotEnvValue "LLM_PROVIDER_MID" "local"
Set-DotEnvValue "LLM_PROVIDER_HIGH" "local"
Set-DotEnvValue "LOCAL_LLM_BASE_URL" "http://host.docker.internal:11434/v1"
Set-DotEnvValue "LOCAL_LLM_API_KEY" ""
Set-DotEnvValue "LOCAL_LLM_MODEL_LOW" $Model
Set-DotEnvValue "LOCAL_LLM_MODEL_MID" $Model
Set-DotEnvValue "LOCAL_LLM_MODEL_HIGH" $Model

if ($EnableCharacterVideo) {
    Write-Step "VOICEVOXと立ち絵掛け合い動画を有効にします"
    Add-ComposeProfile "voicevox"
    Set-DotEnvValue "TTS_PROVIDER" "voicevox"
    Set-DotEnvValue "DIALOGUE_SCRIPT_ENABLED" "true"
    Set-DotEnvValue "CHARACTER_RENDER_ENABLED" "true"
    Set-DotEnvValue "DIALOGUE_CAST" $DialogueCast
}

if ($SkipCompose) {
    Write-Host "`n設定を保存しました。Docker起動は -SkipCompose により省略しました。" -ForegroundColor Yellow
    exit 0
}

Write-Step "Docker Composeを再構築して起動します"
& $docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    throw "Docker Composeの起動に失敗しました。docker compose logs app で詳細を確認してください。"
}

Write-Step "アプリとDockerからOllamaへの接続を確認します"
$deadline = [DateTime]::UtcNow.AddSeconds(90)
$healthy = $false
while ([DateTime]::UtcNow -lt $deadline) {
    try {
        $health = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://localhost:8000/health"
        if ($health.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $healthy) {
    throw "アプリのヘルスチェックがタイムアウトしました。docker compose logs app で詳細を確認してください。"
}

& $docker compose exec -T app python -c "import httpx; response = httpx.get('http://host.docker.internal:11434/api/tags', timeout=10.0); response.raise_for_status(); print('Ollama connection: OK')"
if ($LASTEXITCODE -ne 0) {
    throw "DockerコンテナからOllamaへ接続できません。.env の LOCAL_LLM_BASE_URL を確認してください。"
}

Write-Host "`n完了しました。http://localhost:8000/dashboard を開いてください。" -ForegroundColor Green
Write-Host "設定ページで LLM_PROVIDER=local を確認できます。" -ForegroundColor Green
