# 📻 ポプラジ — 自分専用 朝のニュースラジオ（PoC）

毎朝、登録キーワードのニュースを集めて、2人のパーソナリティ（ハル＆ソラ）の掛け合いラジオにします。
スマホではPWAとしてホーム画面に追加し、ボタン1つで聴けます。**費用0円・個人利用・非商用**が前提です。

```
GitHub Actions (毎朝 04:37 JST)
  ① ニュース収集   Google News RSS（キーワード when:1d）＋ 追加RSS
                    期間フィルタ / URL・見出しの重複排除 / 前日までの既出を除外
  ② 台本生成       Gemini無料枠 → GitHub Models → Ollama → テンプレート の順でフォールバック
                    JSONスキーマ検証 / 記事に無い数字の検出（捏造チェック）
  ③ 読み変換       OpenAI → オープンエーアイ など（字幕は元の表記のまま）
  ④ 音声合成       VOICEVOX ENGINE（Actionsのserviceコンテナ）
  ⑤ 結合           ジングル・チャプター音・間を入れて1本に → ffmpegでMP3＋ラウドネス正規化
                    各セリフの開始/終了秒をそのまま字幕同期に使う
  ⑥ 公開           gh-pages に orphan コミットで force push（音声がgit履歴に溜まらない）
                    音声は30日分だけ保持、台本JSONは残す
```

## ローカルで試す（Windows・追加インストール不要）

```bash
pip install -r requirements.txt
python -m radio.pipeline --site site --sample --llm template --tts windows --now 2026-11-08T05:00:00+09:00
python tools/serve.py site 8765
```

ブラウザで http://127.0.0.1:8765 を開きます。`--tts windows` は Windows 標準の OneCore 日本語音声（ハル＝Haruka、ソラ＝Ichiro）を使い、SSMLで抑揚をつけます。

| オプション | 値 |
|---|---|
| `--sample` | `samples/feeds/` の架空ニュースでオフライン実行 |
| `--llm` | `auto`（環境変数のある順に試す）/ `gemini` / `github` / `ollama` / `template` |
| `--tts` | `auto` / `voicevox` / `windows`（Windows OneCore）/ `silent`（テスト用） |
| `--now` | 現在時刻を固定（検証用） |

## COEIROINK の声で作る（ローカル）

1. [COEIROINK v2](https://coeiroink.com/) をインストールして起動し、使いたいキャラをダウンロードします
2. 声を一覧・設定します（ハルは標準搭載のつくよみちゃんが設定済み。ソラは未設定です）

```bash
python -m tools.coeiroink_speakers
python -m tools.coeiroink_speakers --set sora=キャラ名:スタイル名
python -m tools.coeiroink_speakers --preview
```

3. 番組を生成します：`python -m radio.pipeline --site site --sample --llm template --tts coeiroink`

`--emotion sora:excited=スタイル名` で、驚いたときだけ別スタイルに切り替えられます。
COEIROINK は公式の Linux/Docker 版が無いため、GitHub Actions での毎朝の自動生成は VOICEVOX を使います。

## 番組づくりの工夫

- **固有名詞の読み**：台本と一緒にAIが読みをカタカナで出し、その回だけ読み辞書に追加します（例：長新太→チョウシンタ）
- **展示カード**：イベントのニュースは会場・会期・エリアをカードにし、📍地図と📅Googleカレンダーに飛べます。記事に無い会場・会期・エリアは表示しません
- **コーナー**：`corners`（topics の設定）から各ニュースにコーナー名を付けます
- **2段階の事実チェック**：台本を書いたあと、同じAIに記事と照合させ、裏付けのない断定を書き直します（`--no-review` で省略）
- **抑揚の調整**：「驚き」のセリフは全体の3割まで（`max_excited_ratio`）、3回以上は続けません
- **長さ**：`minutes` で平日／週末の目安の分数を指定します
- **BGM**：自作のローファイ風ループを小さく流し、話している間は自動で音量を下げます（`characters.json` の `bgm`、`--no-bgm` で無効）

## アプリの機能

ミニプレーヤー（他の画面でも操作できる）／スリープタイマー（15・30・60分、音を小さくしてから停止）／オフライン保存（新しい3回分）／再生速度／続きから再生／字幕と台本／連続リスニング日数

## キーワードをスマホから変える

GitHubアプリで Issue を新規作成し、「🏷️ キーワードを変える」フォームに記入すると、`config/topics.json` が自動で更新されます（リポジトリの持ち主が立てた Issue だけ反映）。

## 失敗の通知

毎朝の生成が失敗すると、「📻 放送失敗 日付」の Issue が自動で立ち、GitHubアプリの通知でスマホに届きます。

## テスト

```bash
python -m pytest -q
```

- **Python 103件**：ニュースのパース・期間・重複排除・既出除外、台本の検証・捏造チェック・フォールバック、
  LLM API呼び出し（HTTPはモック）、読み変換、音声結合のタイミング精度、30日の整理、E2E（サイト生成→検証）
- **UIロジック 26件**：サーバー起動後に `http://127.0.0.1:8765/tests.html` を開くと実行されます
- **公開前チェック**：`python -m tools.check_site site`（Actionsでも公開直前に実行）

## GitHub で毎朝動かす

1. このフォルダを **Public** リポジトリとして push します（Publicなら標準ランナーは無料）
2. Settings → Pages → Source を「Deploy from a branch」、Branch を `gh-pages` / `/(root)` にします（初回の放送後に選べます）
3. （任意）Settings → Secrets → Actions に `GEMINI_API_KEY` を登録します。未登録でも GitHub Models（`GITHUB_TOKEN`）が使えます
4. Actions → daily-radio → Run workflow で手動実行して確かめます（`sample: true` にすると架空ニュースで試せます）
5. スマホで `https://<ユーザー名>.github.io/<リポジトリ名>/` を開き、「ホーム画面に追加」します

キーワードは `config/topics.json`、キャラ設定とVOICEVOXの話者IDは `config/characters.json`、読み辞書は `config/reading_dict.json` で変更します。

## 注意

- 公開リポジトリなので、キーワード・台本・音声は誰でも見られます
- 記事本文は取得しません。見出しとRSSの説明文だけを使い、出典リンクを必ず表示します
- VOICEVOXの音声は、キャラクターごとの利用規約に従ってクレジットを表示します（アプリのフッターに自動で出ます）
- 公開リポジトリで60日間活動がないと、スケジュール実行は自動で無効化されます。止まっていたら Actions 画面から再有効化してください
- Google News RSS は公式に安定稼働が保証されたAPIではありません。失敗したフィードはスキップして続行します
