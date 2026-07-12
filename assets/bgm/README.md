# BGM素材

`assets/bgm/<mood>/` 配下の音源をBGMとして使用する。moodは `calm` / `upbeat` / `serious`。
音声ファイル(mp3/wav/ogg)はgit管理外。**各ファイルの隣に同名の `<name>.credit.txt`
(概要欄へ自動追記されるクレジット文)を必ず置くこと。**

## 収録済み音源(2026-07-12 取得)

Kevin MacLeod (incompetech.com) の楽曲。ライセンスは **CC-BY 4.0**
(クレジット表記により商用利用・改変可。表記文は各 `.credit.txt` のとおりで、
アップロード時に動画概要欄へ自動追記される)。

| mood | ファイル | 曲名 |
|---|---|---|
| calm | meditation_impromptu_03.mp3 | Meditation Impromptu 03 |
| calm | deliberate_thought.mp3 | Deliberate Thought |
| upbeat | carefree.mp3 | Carefree |
| upbeat | wallpaper.mp3 | Wallpaper |
| serious | thinking_music.mp3 | Thinking Music |
| serious | lightless_dawn.mp3 | Lightless Dawn |

再取得: `https://incompetech.com/music/royalty-free/mp3-royaltyfree/<曲名(URLエンコード)>.mp3`

## 追加する場合

1. 利用規約を確認する(商用利用・YouTube収益化動画での利用可否・クレジット要否)
2. `assets/bgm/<mood>/<name>.mp3` へ配置する
3. `assets/bgm/<mood>/<name>.credit.txt` にクレジット文を1行で書く
   (クレジット不要の音源でも出所メモとして必ず作成する。概要欄に出したくない場合は
   空ファイルにする)
4. 選曲は video_project_id から決定的に行われるため、ファイルを増減すると
   既存プロジェクトの再レンダリング時に選曲が変わりうる(冪等キーには実際に
   使った音源のチェックサムが含まれるため、重複投稿等は起きない)

## 効果音(SE)

`assets/se/` のWAVは `uv run python scripts/generate_se.py` で合成される
オリジナル音源(クレジット不要)。削除しても同コマンドで再生成できる。
