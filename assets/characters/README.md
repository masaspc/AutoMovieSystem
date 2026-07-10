# 立ち絵キャラクターパック

このディレクトリには、利用条件を確認したうえで入手したキャラクター素材だけを配置する。
公式画像そのものはリポジトリへ含めない。

```text
assets/characters/
  zundamon/
    normal.png          # 必須: 透明背景の通常立ち絵
    happy.png           # 任意: 表情差分
    serious.png
    surprised.png
    talk.png            # 任意: 口を開いた立ち絵。あれば口パクに使う
  metan/
    normal.png
    ...
  tsumugi/
    normal.png
    ...
```

`CHARACTER_RENDER_ENABLED=true` の場合、動画内で登場する全キャラクターの `normal.png`
が必要。`talk.png` または `<emotion>_open.png` を用意すると、話している間に口パクとして
交互表示する。

VOICEVOX音声を使う動画では、概要欄に登場話者分のクレジットを記載する。
