# 🗞️ デイリーニュースダイジェスト

毎日 JST 8:00 に、AI・地震・天文・新発見のニュースを自動収集し、  
Groq APIで要約して、Slackに配信するシステム。

## アーキテクチャ

```
GitHub Actions (cron: 毎日 UTC 23:00 = JST 8:00)
  │
  ├─ RSS/API で情報収集
  │    ├─ AI:   Anthropic / Google AI / OpenAI / ITmedia / GIGAZINE
  │    ├─ 地震: 気象庁API（震度3+） / USGS（M6.0+）
  │    ├─ 天文: 国立天文台 / アストロアーツ
  │    └─ 新発見: Nature / EurekAlert / ナショジオ
  │
  ├─ 重複排除（URLハッシュで過去7日分と照合）
  │
  ├─ Groq API で日本語要約＋解説生成
  │
  └─ Slack Webhook で投稿
```

## セットアップ手順

### 1. GitHubリポジトリ作成

```bash
# 新しいリポジトリを作成（publicにする → Actions無料）
git init news-digest
cd news-digest

# このプロジェクトのファイルをコピー
# main.py, requirements.txt, .github/workflows/daily-digest.yml, data/seen.json
```

### 2. Slack Webhook URL を取得

1. [Slack App管理画面](https://api.slack.com/apps) にアクセス
2. 「Create New App」→「From scratch」
3. アプリ名: `News Digest Bot`、ワークスペースを選択
4. 左メニュー「Incoming Webhooks」→ ONにする
5. 「Add New Webhook to Workspace」→ 投稿先チャンネルを選択
6. 表示される `https://hooks.slack.com/services/T.../B.../xxx` をコピー

### 3. Groq API キーを取得

1. [Groq Console](https://console.groq.com/) にアクセス（無料登録）
2. API Keys → 「Create API Key」
3. 生成されたキーをコピー

### 4. GitHub Secrets を設定

リポジトリの Settings → Secrets and variables → Actions → 「New repository secret」

| Secret名 | 値 |
|---|---|
| `GROQ_API_KEY` | Groqで取得したAPIキー |
| `SLACK_WEBHOOK_URL` | Slackで取得したWebhook URL |

### 5. プッシュして動作確認

```bash
git add .
git commit -m "Initial setup"
git remote add origin https://github.com/YOUR_USERNAME/news-digest.git
git push -u origin main
```

### 6. 手動テスト

GitHub → Actions タブ → 「Daily News Digest」→ 「Run workflow」で手動実行可能。

## カスタマイズ

### カテゴリ追加
`main.py` の `RSS_SOURCES` リストに追加:

```python
{"url": "https://example.com/feed.xml", "category": "ai",
 "keywords": ["キーワード1", "キーワード2"]},  # キーワードフィルタ（省略可）
```

### 地震の閾値変更
`fetch_earthquake_jma()` 内の `if int(int_value) < 3:` を変更。  
例: 震度4以上にしたい → `< 4` に変更。

### 配信時刻の変更
`.github/workflows/daily-digest.yml` の cron を変更:

```yaml
# JST 7:00 にしたい場合 → UTC 22:00
- cron: '0 22 * * *'
```

### LLMモデルの変更
環境変数 `GROQ_MODEL` で切替可能。GitHub Secretsに追加するか、
workflow YAMLの env に直接書く。

## コスト

| サービス | 費用 |
|---|---|
| GitHub Actions (publicリポ) | 無料 |
| Groq API (無料枠) | 無料（日次制限あり） |
| Slack Incoming Webhook | 無料 |
| **合計** | **¥0** |

## トラブルシューティング

### Actions が動かない
- リポジトリが **public** であることを確認
- Secrets が正しく設定されているか確認
- Actions タブでログを確認

### RSS取得が0件
- フィードURLが変更されている可能性 → ブラウザで直接アクセスして確認
- キーワードフィルタが厳しすぎる → keywords を減らす or 削除

### Groq APIエラー
- 無料枠の日次制限に達した可能性 → 翌日に自動復旧
- モデル名が変更された可能性 → [Groq Models](https://console.groq.com/docs/models) で確認
