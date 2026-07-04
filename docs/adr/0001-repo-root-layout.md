# ADR-0001: リポジトリルート直下にアプリを構築する

Status: Accepted (2026-07-04)

## Context
仕様§5は `youtube-growth-automation/` ディレクトリをルートとする構造を示すが、
本リポジトリ(AutoMovieSystem)は空であり、サブディレクトリ化する理由がない。

## Decision
仕様§5の構造を、サブディレクトリなしでリポジトリルート直下に展開する。

## Consequences
- importパス・CI・Docker build contextが単純になる
- 仕様のパス表記との差異はこのADRで吸収する
