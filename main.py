"""
デイリーニュースダイジェスト自動配信システム v2
=============================================
毎日JST 8:00にSlackへ「要約＋解説＋出典リンク」を配信する。

カテゴリ:
  - AI関連（Claude, Gemini, ChatGPT等）
  - 国内主要ニュース（政治・経済・社会）
  - 理科系トピック（地震・天文・気象・新発見をまとめて表示）

実行基盤: GitHub Actions (cron)
要約生成: Groq API (llama-3.3-70b-versatile)
配信先:   Slack Incoming Webhook
"""

import os
import json
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import Counter

import feedparser
import requests

# ── ログ設定 ─────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── 環境変数 ─────────────────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# ── 定数 ──────────────────────────────────────
JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST)
YESTERDAY = NOW - timedelta(hours=24)

# ── カテゴリ設定 ──────────────────────────────
EMOJI = {
    "ai": "🤖",
    "domestic": "📰",
    "science": "🔬",
}

CATEGORY_NAMES = {
    "ai": "AI関連",
    "domestic": "国内主要ニュース",
    "science": "理科系トピック",
}

# 1ソースあたりの最大記事数（偏り防止）
MAX_PER_SOURCE = 2


# =============================================
# 1. 情報収集
# =============================================

def fetch_rss(url: str, category: str, keywords: list[str] | None = None,
              exclude_keywords: list[str] | None = None) -> list[dict]:
    """RSSフィードから直近24時間の記事を取得する。"""
    articles = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            # 日時パース
            published = None
            for attr in ("published_parsed", "updated_parsed"):
                t = getattr(entry, attr, None)
                if t:
                    from time import mktime
                    published = datetime.fromtimestamp(mktime(t), tz=timezone.utc)
                    break

            if published and published < YESTERDAY:
                continue

            title = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            link = getattr(entry, "link", "")
            text = (title + " " + summary).lower()

            # キーワードフィルタ（指定がある場合のみ）
            if keywords:
                if not any(kw.lower() in text for kw in keywords):
                    continue

            # 除外キーワード
            if exclude_keywords:
                if any(kw.lower() in text for kw in exclude_keywords):
                    continue

            articles.append({
                "category": category,
                "title": title,
                "summary": summary[:500],
                "link": link,
                "source": feed.feed.get("title", url),
            })
    except Exception as e:
        log.warning(f"RSS取得失敗: {url} → {e}")
    return articles


def fetch_earthquake_jma() -> list[dict]:
    """
    気象庁の地震情報をJSON APIから取得する。
    震度3以上のみ抽出。
    """
    articles = []
    url = "https://www.jma.go.jp/bosai/quake/data/list.json"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        quakes = resp.json()

        for q in quakes[:50]:
            time_str = q.get("at", "")
            if not time_str:
                continue
            try:
                qt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if qt < YESTERDAY:
                continue

            max_int = q.get("max_int", "")
            int_value = max_int.replace("-", "").replace("+", "")
            try:
                if int(int_value) < 3:
                    continue
            except (ValueError, TypeError):
                continue

            hypo = q.get("anm", "不明")
            mag = q.get("mag", "不明")
            detail_url = "https://www.jma.go.jp/bosai/quake/"

            articles.append({
                "category": "science",
                "title": f"🌏 {hypo}で震度{max_int}（M{mag}）",
                "summary": f"震源: {hypo} / 最大震度{max_int} / M{mag} / {time_str}",
                "link": detail_url,
                "source": "気象庁",
            })
    except Exception as e:
        log.warning(f"気象庁API取得失敗: {e}")
    return articles


def fetch_earthquake_usgs() -> list[dict]:
    """USGS Earthquake API から M6.0以上の地震を取得する。"""
    articles = []
    start = YESTERDAY.strftime("%Y-%m-%dT%H:%M:%S")
    url = (
        "https://earthquake.usgs.gov/fdsnws/event/1/query"
        f"?format=geojson&starttime={start}&minmagnitude=6.0"
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        for feature in data.get("features", []):
            props = feature.get("properties", {})
            place = props.get("place", "不明")
            mag = props.get("mag", 0)
            detail_url = props.get("url", "")
            time_ms = props.get("time", 0)
            qt = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc)

            articles.append({
                "category": "science",
                "title": f"🌏 海外地震: {place}（M{mag}）",
                "summary": f"場所: {place} / M{mag} / {qt.astimezone(JST).strftime('%m/%d %H:%M JST')}",
                "link": detail_url,
                "source": "USGS",
            })
    except Exception as e:
        log.warning(f"USGS API取得失敗: {e}")
    return articles


# ── RSSソース定義 ──────────────────────────────

RSS_SOURCES = [
    # ━━━ AI関連 ━━━
    {"url": "https://www.anthropic.com/feed.xml", "category": "ai"},
    {"url": "https://blog.google/technology/ai/rss/", "category": "ai"},
    {"url": "https://openai.com/blog/rss.xml", "category": "ai"},
    {"url": "https://www.itmedia.co.jp/news/subtop/aiplus/rss/index.xml", "category": "ai"},
    {"url": "https://gigazine.net/news/rss_2.0/", "category": "ai",
     "keywords": ["ChatGPT", "Claude", "Gemini", "LLM", "GPT-", "OpenAI",
                   "Anthropic", "Google AI", "生成AI", "大規模言語モデル",
                   "Copilot", "Midjourney", "Stable Diffusion", "Suno",
                   "機械学習", "ディープラーニング"],
     "exclude_keywords": ["マラソン", "ランナー", "レシピ", "料理", "ダイエット"]},
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "category": "ai"},
    {"url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "category": "ai"},

    # ━━━ 国内主要ニュース ━━━
    {"url": "https://www.nhk.or.jp/rss/news/cat0.xml", "category": "domestic"},
    {"url": "https://news.yahoo.co.jp/rss/topics/top-picks.xml", "category": "domestic"},
    {"url": "https://www3.nhk.or.jp/rss/news/cat1.xml", "category": "domestic"},
    {"url": "https://www3.nhk.or.jp/rss/news/cat3.xml", "category": "domestic"},

    # ━━━ 理科系トピック ━━━
    {"url": "https://www.nao.ac.jp/rss/atom.xml", "category": "science"},
    {"url": "https://www.astroarts.co.jp/article/feed.xml", "category": "science"},
    {"url": "https://spaceweather.com/rssnews.php", "category": "science"},
    {"url": "https://www3.nhk.or.jp/rss/news/cat0.xml", "category": "science",
     "keywords": ["台風", "大雨", "暴風", "洪水", "津波", "噴火", "火山",
                   "竜巻", "猛暑", "熱中症", "大雪", "警報"]},
    {"url": "http://feeds.nature.com/nature/rss/current", "category": "science",
     "keywords": ["new species", "新種", "discovery", "発見", "fossil", "化石",
                   "asteroid", "小惑星", "exoplanet", "系外惑星", "earthquake",
                   "volcano", "climate"]},
    {"url": "https://www.eurekalert.org/rss/technology_engineering.xml", "category": "science",
     "keywords": ["species", "discovery", "fossil", "ocean", "biodiversity",
                   "earthquake", "volcano", "asteroid", "comet"]},
    {"url": "https://natgeo.nikkeibp.co.jp/atcl/news/feed/rss.xml", "category": "science",
     "keywords": ["新種", "発見", "化石", "生物", "深海", "火山", "地震",
                   "天文", "宇宙", "気象", "恐竜"]},
]


def collect_all_articles() -> list[dict]:
    """全ソースから記事を収集する。"""
    articles = []
    for src in RSS_SOURCES:
        log.info(f"取得中: {src['url']}")
        arts = fetch_rss(
            src["url"], src["category"],
            src.get("keywords"), src.get("exclude_keywords"),
        )
        articles.extend(arts)
        log.info(f"  → {len(arts)}件")

    log.info("取得中: 気象庁 地震情報")
    jma = fetch_earthquake_jma()
    articles.extend(jma)
    log.info(f"  → {len(jma)}件")

    log.info("取得中: USGS 地震情報")
    usgs = fetch_earthquake_usgs()
    articles.extend(usgs)
    log.info(f"  → {len(usgs)}件")

    return articles


# =============================================
# 2. ソース偏り防止 & 重複排除
# =============================================

def limit_per_source(articles: list[dict]) -> list[dict]:
    """同一ソースからの記事数を制限する。"""
    source_count: Counter = Counter()
    result = []
    for art in articles:
        source = art["source"]
        if source_count[source] < MAX_PER_SOURCE:
            result.append(art)
            source_count[source] += 1
    return result


def deduplicate_by_title(articles: list[dict]) -> list[dict]:
    """タイトルが類似する記事を除外する（完全一致ベース）。"""
    seen_titles: set[str] = set()
    result = []
    for art in articles:
        normalized = art["title"].strip().lower()
        if normalized not in seen_titles:
            seen_titles.add(normalized)
            result.append(art)
    return result


# =============================================
# 3. LLMで要約・解説生成（Groq API）
# =============================================

SUMMARIZE_PROMPT = """あなたはニュースキュレーターです。以下の記事情報を日本語で要約してください。

## ルール
- 「要約」は2〜3行で、何が起きたかを簡潔に説明する
- 「解説」は1行で、なぜ重要か・読者が知っておくべきポイントを補足する
- 出典リンクはそのまま維持する
- 原文にない情報を追加しない（ハルシネーション禁止）
- JSON形式で返す

## 入力
タイトル: {title}
元の要約: {summary}
出典: {source}
URL: {link}

## 出力フォーマット（JSON）
{{
  "summary": "2〜3行の要約文",
  "insight": "1行の解説・ポイント"
}}
"""


def summarize_with_groq(article: dict) -> dict:
    """Groq APIで記事を要約する。"""
    if not GROQ_API_KEY:
        return {
            "summary": article["summary"][:200],
            "insight": "（LLM要約なし：GROQ_API_KEY未設定）",
        }

    prompt = SUMMARIZE_PROMPT.format(
        title=article["title"],
        summary=article["summary"],
        source=article["source"],
        link=article["link"],
    )

    try:
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
                "max_tokens": 500,
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        result = json.loads(content)
        return {
            "summary": result.get("summary", article["summary"][:200]),
            "insight": result.get("insight", ""),
        }
    except Exception as e:
        log.warning(f"Groq API失敗: {e}")
        return {
            "summary": article["summary"][:200],
            "insight": "（要約生成に失敗）",
        }


def enrich_articles(articles: list[dict]) -> list[dict]:
    """全記事にLLM要約を付与する。"""
    for art in articles:
        log.info(f"要約生成中: {art['title'][:50]}")
        result = summarize_with_groq(art)
        art["ai_summary"] = result["summary"]
        art["ai_insight"] = result["insight"]
    return articles


# =============================================
# 4. Slack投稿
# =============================================

def build_slack_message(articles: list[dict]) -> str:
    """Slack投稿用のメッセージを組み立てる。"""
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    wd = weekdays[NOW.weekday()]
    date_str = NOW.strftime(f"%Y/%m/%d（{wd}）")
    lines = [f"🗞️ *デイリーニュースダイジェスト*（{date_str}）\n"]

    for cat_key in ["ai", "domestic", "science"]:
        emoji = EMOJI[cat_key]
        name = CATEGORY_NAMES[cat_key]
        cat_articles = [a for a in articles if a["category"] == cat_key]

        if cat_key == "science" and not cat_articles:
            continue

        lines.append(f"{'━' * 20}")
        lines.append(f"{emoji} *{name}*")
        lines.append(f"{'━' * 20}\n")

        if not cat_articles:
            lines.append("該当なし\n")
            continue

        for art in cat_articles[:5]:
            lines.append(f"*{art['title']}*")
            lines.append(art.get("ai_summary", art["summary"][:200]))
            insight = art.get("ai_insight", "")
            if insight:
                lines.append(f"💡 {insight}")
            lines.append(f"🔗 {art['link']}")
            lines.append("")

    lines.append(f"_配信時刻: {NOW.strftime('%H:%M JST')}_")
    return "\n".join(lines)


def post_to_slack(message: str):
    """Slack Webhookに投稿する。"""
    if not SLACK_WEBHOOK_URL:
        log.warning("SLACK_WEBHOOK_URL が未設定です。標準出力に表示します。")
        print("\n" + "=" * 60)
        print(message)
        print("=" * 60)
        return

    try:
        resp = requests.post(
            SLACK_WEBHOOK_URL,
            json={"text": message},
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Slack投稿完了")
    except Exception as e:
        log.error(f"Slack投稿失敗: {e}")
        raise


# =============================================
# 5. メイン処理
# =============================================

def main():
    log.info("=" * 50)
    log.info("デイリーニュースダイジェスト 開始")
    log.info(f"現在時刻: {NOW.isoformat()}")
    log.info("=" * 50)

    articles = collect_all_articles()
    log.info(f"収集完了: 全{len(articles)}件")

    articles = deduplicate_by_title(articles)
    articles = limit_per_source(articles)
    log.info(f"フィルタ後: {len(articles)}件")

    articles = enrich_articles(articles)

    message = build_slack_message(articles)
    post_to_slack(message)

    log.info("完了")


if __name__ == "__main__":
    main()
