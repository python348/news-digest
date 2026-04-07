"""
Phase 2: LLMによる選別 + 要約生成
==================================
Phase 1 が出力した raw_news.json を読み、
Groq API で以下を行う:
  - S/A/B/C ランクの重要度判定
  - 2〜3行の日本語要約
  - 1行の解説（なぜ重要か）

出力: data/enriched_news.json
"""

import json
import logging
import os
import sys
from pathlib import Path

import requests

# ── ログ設定 ─────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── 環境変数 ─────────────────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# ── パス ──────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
RAW_NEWS_FILE = DATA_DIR / "raw_news.json"
ENRICHED_FILE = DATA_DIR / "enriched_news.json"

# ── 定数 ──────────────────────────────────────
# S/Aランクのみ配信（B/Cは捨てる）
PUBLISH_RANKS = {"S", "A"}
MAX_ARTICLES_TO_PUBLISH = 10


# =============================================
# LLMプロンプト
# =============================================

RANK_AND_SUMMARIZE_PROMPT = """\
あなたはニュースキュレーターです。
以下の記事リストを読み、「この読者」にとっての重要度を判定してください。

## 読者プロフィール
- 中学受験塾（集団指導）の理科主任。小4〜小6を担当
- 同時に経営企画・広報・デザイン・人事も兼務
- AI×業務効率化を強く推進中（Make, Groq API, GAS, Claude, Notion）
- 将来的に独立・個人事業を志向しており、ビジネスの種を探している
- 深掘り系・本質志向の情報を好む。表面的なニュースは響かない

## ランク判定基準

### Sランク（必ず届けるべき）
- AI主要サービスの新モデル・大型アップデート（Claude, GPT, Gemini等）
- AI×教育、AI×業務効率化の具体的な事例やツール
- 中学入試で時事問題として出題されそうなニュース（後述の出題傾向を参照）
- 独立・副業・個人事業に直結するヒント

### Aランク（届ける価値あり）
- AI業界の動向（資金調達、提携、規制、新サービス）
- 授業の導入ネタ・雑談ネタになる科学トピック
- 教育業界の動向（入試制度変更、学習指導要領など）
- 塾経営・マーケティングに関わる情報

### Bランク（余裕があれば）
- 一般的なテック系ニュース
- 直接関係ないが教養として面白い話題

### Cランク（スキップ）
- この読者に関係ない業界の話（金融、エンタメ、スポーツ等）
- 表面的・薄い内容のニュース

## 中学入試理科の出題傾向（時事問題として狙われやすいテーマ）
以下のテーマに関連するニュースは、授業で即活用できるため加点すること。

【生物】
- 新種発見、絶滅危惧種、外来種問題、生態系の変化
- 感染症・ウイルス・ワクチンの話題
- ノーベル生理学・医学賞関連
- 食物連鎖、光合成、植物の開花・季節変化に関する観測データ
- iPS細胞、遺伝子編集など生命科学の進展

【地学】
- 大きな地震（国内震度4以上、海外M6以上）、火山噴火
- 気象現象（台風、猛暑、大雪、線状降水帯）、気候変動データ
- 天文イベント（日食、月食、流星群、惑星接近、彗星）
- 宇宙探査（はやぶさ、月面探査、火星探査、ロケット打上げ）
- 化石発見、地層、プレートテクトニクスに関する新知見

【物理】
- ノーベル物理学賞関連
- エネルギー問題（原発、再生可能エネルギー、核融合）
- 光、音、電気に関する身近な現象の科学的解説

【化学】
- ノーベル化学賞関連
- 環境問題（マイクロプラスチック、CO2、オゾン層）
- 新素材、新元素の発見や命名
- 水質・大気・土壌の汚染に関するニュース

【横断テーマ】
- SDGs関連（特にエネルギー、水、生物多様性）
- 日本の世界遺産（自然遺産の登録・管理）
- 防災（ハザードマップ、避難、災害対策の技術）

## 出力ルール
- 各記事について rank / summary / insight / exam_relevance を返す
- summary は日本語で2〜3行。何が起きたかを簡潔に
- insight は日本語で1行。この読者にとってなぜ重要か
- exam_relevance は中学入試との関連度を示す（"high" / "medium" / "low" / "none"）
  - "high" の場合、insight に「入試出題の可能性あり」「授業ネタに使える」等を含めること
- 原文にない情報を追加しない（ハルシネーション禁止）
- JSON配列で返す。余計なテキストやMarkdownは不要

## 入力記事リスト
{articles_json}

## 出力フォーマット（JSON配列）
[
  {{
    "index": 0,
    "rank": "S",
    "summary": "要約文...",
    "insight": "解説文...",
    "exam_relevance": "high"
  }},
  ...
]
"""


# =============================================
# Groq API呼び出し
# =============================================

def call_groq(prompt: str) -> str:
    """Groq APIを呼び出してテキストを返す。"""
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 4000,
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def rank_and_summarize(articles: list[dict]) -> list[dict]:
    """LLMに記事のランク付けと要約を依頼する。"""
    if not GROQ_API_KEY:
        log.warning("GROQ_API_KEY 未設定 → スコアベースのフォールバック")
        return fallback_enrich(articles)

    # LLMに渡す情報を最小限に（トークン節約）
    compact = []
    for i, art in enumerate(articles):
        compact.append({
            "index": i,
            "title": art["title"],
            "summary": art["summary"][:300],
            "source": art["source"],
            "category": art["category"],
        })

    # 記事が多い場合はバッチ分割（1回あたり最大15件）
    batch_size = 15
    all_results = []

    for batch_start in range(0, len(compact), batch_size):
        batch = compact[batch_start:batch_start + batch_size]
        prompt = RANK_AND_SUMMARIZE_PROMPT.format(
            articles_json=json.dumps(batch, ensure_ascii=False, indent=2)
        )

        try:
            log.info(f"Groq API呼び出し中（{len(batch)}件）...")
            raw = call_groq(prompt)
            parsed = json.loads(raw)

            # response_format: json_object の場合、トップレベルがオブジェクトの場合がある
            if isinstance(parsed, dict):
                # {"results": [...]} のようなラッパーを剥がす
                for key in ["results", "articles", "data", "items"]:
                    if key in parsed and isinstance(parsed[key], list):
                        parsed = parsed[key]
                        break
                else:
                    parsed = list(parsed.values())[0] if parsed else []

            all_results.extend(parsed)

        except Exception as e:
            log.warning(f"Groq API失敗: {e} → フォールバック")
            for item in batch:
                all_results.append({
                    "index": item["index"],
                    "rank": "B",
                    "summary": articles[item["index"]]["summary"][:200],
                    "insight": "（要約生成に失敗）",
                })

    # 結果をarticlesにマージ
    result_map = {r["index"]: r for r in all_results if isinstance(r, dict)}

    enriched = []
    for i, art in enumerate(articles):
        r = result_map.get(i, {})
        art["rank"] = r.get("rank", "C")
        art["ai_summary"] = r.get("summary", art["summary"][:200])
        art["ai_insight"] = r.get("insight", "")
        art["exam_relevance"] = r.get("exam_relevance", "none")
        enriched.append(art)

    return enriched


def fallback_enrich(articles: list[dict]) -> list[dict]:
    """APIキーがない場合のフォールバック。importance_scoreでランク付け。"""
    for art in articles:
        score = art.get("importance_score", 0)
        if score >= 6:
            art["rank"] = "S"
        elif score >= 4:
            art["rank"] = "A"
        elif score >= 2:
            art["rank"] = "B"
        else:
            art["rank"] = "C"
        art["ai_summary"] = art["summary"][:200]
        art["ai_insight"] = "（LLM要約なし）"
    return articles


# =============================================
# メイン処理
# =============================================

def main():
    log.info("=" * 50)
    log.info("Phase 2: LLM選別 + 要約生成 開始")
    log.info("=" * 50)

    # raw_news.json 読み込み
    if not RAW_NEWS_FILE.exists():
        log.error(f"{RAW_NEWS_FILE} が見つかりません。Phase 1 を先に実行してください。")
        sys.exit(1)

    raw = json.loads(RAW_NEWS_FILE.read_text())
    articles = raw.get("articles", [])
    log.info(f"入力: {len(articles)}件")

    if not articles:
        log.info("記事が0件 → Phase 2 スキップ")
        ENRICHED_FILE.write_text(json.dumps(
            {"articles": [], "published_count": 0}, ensure_ascii=False
        ))
        print("PUBLISH_COUNT=0")
        sys.exit(0)

    # LLMでランク付け + 要約
    articles = rank_and_summarize(articles)

    # ランク集計ログ
    rank_counts = {}
    for art in articles:
        rank_counts[art["rank"]] = rank_counts.get(art["rank"], 0) + 1
    log.info(f"ランク分布: {rank_counts}")

    # S/Aランクのみ抽出
    publish = [a for a in articles if a["rank"] in PUBLISH_RANKS]
    publish = publish[:MAX_ARTICLES_TO_PUBLISH]
    log.info(f"配信対象: {len(publish)}件（S/Aランクのみ）")

    # B/Cランク（タイトルのみ表示用）
    skipped = [a for a in articles if a["rank"] not in PUBLISH_RANKS]

    # enriched_news.json 出力
    output = {
        "collected_at": raw.get("collected_at", ""),
        "total_before_filter": len(articles),
        "published_count": len(publish),
        "rank_distribution": rank_counts,
        "articles": publish,
        "skipped_articles": skipped,
    }
    ENRICHED_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    log.info(f"出力完了: {ENRICHED_FILE}")

    print(f"PUBLISH_COUNT={len(publish)}")
    log.info("Phase 2 完了")


if __name__ == "__main__":
    main()
