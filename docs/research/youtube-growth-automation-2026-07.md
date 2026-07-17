# YouTube成長自動化リサーチ(2026-07)

調査日: 2026-07-13〜2026-07-14。YouTube / Googleの公式資料のみを根拠とする。

## 結論

制作本数そのものではなく、視聴者が動画を選ぶ訴求、見続ける構成、視聴後の満足を一体で改善する。
企画・台本・素材・品質検査・メタデータ案までは自動化し、YouTubeへの書き込みは対象チャンネル、
タイトル、サムネイル、公開範囲、AI開示を1画面に集約して、ユーザーの明示操作を最終決定とする。

## 公式資料から導いた要件

1. **CTR単独で最適化しない**
   - 推薦はAppeal、Engagement、Satisfactionを含む視聴者反応から長期的な満足を目指す。
   - タイトル・サムネイルの約束を冒頭で回収し、クリック後の視聴時間と満足を損なう誇張を拒否する。
   - 出典: [YouTube's Recommendation System](https://support.google.com/youtube/answer/16533387?hl=en)、[Content performance](https://support.google.com/youtube/answer/16559650?hl=en)

2. **冒頭30秒と場面別維持率を改善ループの中心にする**
   - タイトル・サムネイルとの一致、早い価値提示、後半の強い場面を前へ移すことが公式ガイドの方向性。
   - 維持率処理には通常1〜2日かかるため、投稿直後の数字だけで結論を出さない。
   - 出典: [Audience retention](https://support.google.com/youtube/answer/9314415?hl=en)

3. **タイトル・サムネイルは異なる訴求角度を3案用意する**
   - 公式A/Bテストは最大3案を同時比較し、勝者はCTRだけでなく総視聴時間で判断する。
   - 公開APIで公式テスト開始は文書化されていないため、本システムは3案生成・採用案の自動推薦までを担う。
   - 出典: [A/B test titles and thumbnails](https://support.google.com/youtube/answer/16391400?hl=en)

4. **量産時ほど独自性をゲートする**
   - 2025年7月以降のinauthentic content方針は、反復的・大量生産的・テンプレート差分の小さい動画を
     チャンネル全体で評価する。ニュースフィードやWeb記事の読み上げだけにしない。
   - 最近の動画とのタイトル・フック類似、根拠不足、独自の説明不足を自動検出し、基準未達だけ詳細確認へ回す。
   - 出典: [YouTube channel monetization policies](https://support.google.com/youtube/answer/1311392?hl=en)、[Spam policy](https://support.google.com/youtube/answer/2801973?hl=en)

5. **AI開示は最終確認に明示する**
   - 台本・タイトル・サムネイル等の制作補助は通常開示不要だが、現実的な架空映像、実在人物・出来事の改変、
     AI生成音楽等は開示対象。本システムは安全側にAI開示を有効化し、確認画面へ送信値を表示する。
   - 出典: [Altered or synthetic content](https://support.google.com/youtube/answer/14328491?hl=en)、[Videos resource](https://developers.google.com/youtube/v3/docs/videos)

6. **YouTubeへの書き込みはユーザーの最終決定を残す**
   - 対象チャンネル、送信内容、公開範囲を示した明示操作を必要とする。完全無人公開は採用しない。
   - 自動処理はレビュー合格まで。人間の1操作で承認と非公開アップロードを開始し、公開予約は別の明示操作とする。
   - 出典: [YouTube API Services Developer Policies](https://developers.google.com/youtube/terms/developer-policies)

## 採用する自動化

- 日次オートパイロットは明示的な設定時のみ有効化し、対象チャンネルと1日上限を必須化する。
- 台本生成時に、過去の生指標を「過去の観測」として与え、因果・成功保証として扱わない。
- レンダリング前に成長品質プリフライトを実行し、訴求、継続、満足、独自性、信頼性を構造化確認する。
- 基準未達は最大1回だけ自動修正し、根拠IDを変更する修正は破棄する。
- 合格候補はタイトル・サムネイル採用案を自動選択する。
- 最終画面では動画、メタデータ、出典、レビュー、AI開示、公開範囲をまとめて表示する。
- 最終操作は「承認してYouTubeへ非公開アップロード」の1回とし、自動公開は行わない。

## 今後の拡張

- 48時間・7日・28日の段階セルフレビュー
- Shortsのengaged viewsと長尺のインプレッション/CTRを分離した分析
- 長尺公開後のYouTube Studio公式タイトル・サムネイルA/Bテスト開始通知
- 次に見る動画、再生リスト、終了画面候補のAPI連携

