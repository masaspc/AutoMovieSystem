<#
.SYNOPSIS
  .agents/skills(単一の正本)を Claude Code / Codex のスキル探索先へリンクする。

.DESCRIPTION
  Windowsでは管理者権限不要のディレクトリjunctionを使う(シンボリックリンクは開発者モード/
  管理者権限が必要なため使わない)。冪等: 既存の正しいjunctionは張り直さない。
  チェックアウト直後に一度だけ実行すればよい。

  張るリンク:
    .claude/skills            -> .agents/skills            (Claude Code プロジェクトスキル)
    .codex/skills             -> .agents/skills            (Codex プロジェクトスキル)
    $CODEX_HOME/skills/<name> -> .agents/skills/<name>     (Codex 自動発見。既定 ~/.codex/skills)
#>
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repo ".agents\skills"
if (-not (Test-Path $source)) { throw "スキルの正本が見つかりません: $source" }

function Link-Dir([string]$link, [string]$target) {
    if (Test-Path $link) {
        $item = Get-Item $link -Force
        if ($item.LinkType -eq "Junction" -and $item.Target -eq $target) {
            Write-Host "  = $link (既にリンク済み)"
            return
        }
        # 実体ディレクトリ(誤って作られた)や古いリンクは削除して張り直す
        Remove-Item $link -Recurse -Force
    }
    $parent = Split-Path -Parent $link
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    New-Item -ItemType Junction -Path $link -Target $target | Out-Null
    Write-Host "  + $link -> $target"
}

Write-Host "プロジェクトスキルのリンク:"
Link-Dir (Join-Path $repo ".claude\skills") $source
Link-Dir (Join-Path $repo ".codex\skills")  $source

$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
$codexSkills = Join-Path $codexHome "skills"
Write-Host "Codex 自動発見先($codexSkills)へ各スキルをリンク:"
if (-not (Test-Path $codexSkills)) { New-Item -ItemType Directory -Force -Path $codexSkills | Out-Null }
Get-ChildItem -Directory $source | ForEach-Object {
    Link-Dir (Join-Path $codexSkills $_.Name) $_.FullName
}

Write-Host "完了。"
