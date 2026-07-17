#!/usr/bin/env bash
# .agents/skills(単一の正本)を Claude Code / Codex のスキル探索先へシンボリックリンクする。
# 冪等: 何度実行してもよい。チェックアウト直後に一度だけ実行すればよい。
#   .claude/skills            -> .agents/skills            (Claude Code プロジェクトスキル)
#   .codex/skills             -> .agents/skills            (Codex プロジェクトスキル)
#   $CODEX_HOME/skills/<name> -> .agents/skills/<name>     (Codex 自動発見。既定 ~/.codex/skills)
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$repo/.agents/skills"
[ -d "$source_dir" ] || { echo "スキルの正本が見つかりません: $source_dir" >&2; exit 1; }

link_dir() {  # link_dir <link> <target>
  local link="$1" target="$2"
  if [ -L "$link" ] && [ "$(readlink "$link")" = "$target" ]; then
    echo "  = $link (既にリンク済み)"; return
  fi
  rm -rf "$link"
  mkdir -p "$(dirname "$link")"
  ln -s "$target" "$link"
  echo "  + $link -> $target"
}

echo "プロジェクトスキルのリンク:"
link_dir "$repo/.claude/skills" "$source_dir"
link_dir "$repo/.codex/skills"  "$source_dir"

codex_home="${CODEX_HOME:-$HOME/.codex}"
codex_skills="$codex_home/skills"
echo "Codex 自動発見先($codex_skills)へ各スキルをリンク:"
mkdir -p "$codex_skills"
for d in "$source_dir"/*/; do
  name="$(basename "$d")"
  link_dir "$codex_skills/$name" "${d%/}"
done

echo "完了。"
