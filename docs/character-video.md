# 立ち絵掛け合い動画

ずんだもん・四国めたんを基本の掛け合いにし、春日部つむぎを必要な場面だけ登場させる
解説動画テンプレート。既存の企画、レビュー、承認、投稿フローはそのまま使う。

## 生成内容

1. LLMが各セクションに `zundamon` / `metan` / `tsumugi` のセリフと感情を作る
2. VOICEVOX Engineがセリフ単位でWAVを生成する
3. 立ち絵を話者の位置に合成し、`talk.png` があれば口を開いた差分と交互表示する
4. 字幕、音声、エンドカードをFFmpegで合成し、H.264/AAC MP4を作る

## VOICEVOX Engineの起動

Docker ComposeではVOICEVOXを任意profileとしている。`.env` に以下を設定する。

```env
COMPOSE_PROFILES=voicevox
TTS_PROVIDER=voicevox
# 掛け合い台本・話者別音声を使う明示的なオプトイン
DIALOGUE_SCRIPT_ENABLED=true
CHARACTER_RENDER_ENABLED=true
# つむぎを出さない2人掛け合いにする場合: zundamon,metan
DIALOGUE_CAST=zundamon,metan,tsumugi
```

起動または再作成:

```powershell
./scripts/dev.ps1 up
```

VOICEVOXをホストOSへ直接インストールしている場合は、Compose profileは不要。Engineを
`http://127.0.0.1:50021` で起動し、同じ `.env` 設定で使う。

## 立ち絵の配置

公式素材の利用条件を確認したうえで、透明PNGを次のように配置する。
画像ファイルは権利確認・配布元の更新追従のためリポジトリには含めない。

```text
assets/characters/
  zundamon/normal.png
  zundamon/talk.png       # 任意。口パク用
  zundamon/happy.png      # 任意
  metan/normal.png
  metan/talk.png
  tsumugi/normal.png
```

- 各キャラクターの `normal.png` は必須
- `talk.png` または `<emotion>_open.png` がある場合、発話中に0.18秒ごとに交互表示して口パクにする
- 春日部つむぎは `DIALOGUE_CAST` に含めた場合だけ選択候補となり、セリフのある場面だけ画面へ表示する
- 素材不足時はレンダリングを失敗させる。背景だけの動画へ黙ってフォールバックしない
- `DIALOGUE_SCRIPT_ENABLED=false` (既定) の場合、`dialogue` が保存済みでも動画では従来どおり
  `narration` のみを使う。既存の `generic_command` TTS運用はこの既定値のまま影響を受けない

## 権利とクレジット

VOICEVOXの音声はキャラクターごとの利用規約に従い、動画概要欄などへクレジットを表記する。
このシステムは掛け合い台本から、以下を投稿概要欄へ自動追記する。

```text
VOICEVOX:ずんだもん
VOICEVOX:四国めたん
VOICEVOX:春日部つむぎ
```

公式立ち絵は音声とは別の利用条件がある。収益化・法人利用・利用地域を含め、公開前に
必ず公式の[イラスト素材ページ](https://zunko.jp/con_illust.html)と
[キャラクター利用ガイドライン](https://zunko.jp/guideline.html)を確認すること。

## 話者の使い分け

- ずんだもん: 導入、素朴な疑問、要点の言い換え
- 四国めたん: 主説明、結論、冷静な補足
- 春日部つむぎ: 例外、実践ヒント、視点転換

LLMにはこの役割を渡しており、通常はずんだもんと四国めたんの掛け合い、必要な節だけ
春日部つむぎが登場する台本を生成する。
