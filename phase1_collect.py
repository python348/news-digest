"""
Phase 1: データ収集 + 前処理（トークン消費ゼロ）
================================================
RSS/APIから記事を収集し、重複排除・ソース偏り防止・
キーワードベースの重要度スコアリングまでをPythonだけで行う。

出力: data/raw_news.json
"""

import json
import hashlib
import logging
import sys
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

# ── 定数 ──────────────────────────────────────
JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST)

# 配信間隔（7時/13時/19時の6時間間隔）
COLLECT_HOURS = 8  # 余裕を持って8時間分を収集

DATA_DIR = Path(__file__).parent / "data"
RAW_NEWS_FILE = DATA_DIR / "raw_news.json"
SEEN_FILE = DATA_DIR / "seen.json"

MAX_PER_SOURCE = 2  # 1ソースあたりの最大記事数
MAX_PER_CATEGORY = 8  # 1カテゴリあたりの最大記事数

# ── 重要度スコアリング用キーワード ─────────────
IMPORTANCE_KEYWORDS = {
    # 高スコア（+3）: 速報性・影響が大きい
    "high": [
        "リリース", "発表", "launch", "release", "announced",
        "震度5", "震度6", "震度7", "M7", "M8", "M9",
        "速報", "緊急", "警報", "新発見", "世界初", "日本初",
        "Claude", "GPT-5", "GPT-4o", "Gemini 2",
    ],
    # 中スコア（+2）: 注目度が高い
    "mid": [
        "アップデート", "update", "新機能", "新モデル",
        "震度4", "M6", "津波",
        "ChatGPT", "Gemini", "Claude", "OpenAI", "Anthropic", "Google AI",
        "新種", "化石", "深海", "小惑星", "彗星",
    ],
    # 低スコア（+1）: 一般的な関連トピック
    "low": [
        "AI", "人工知能", "LLM", "機械学習",
        "地震", "天文", "宇宙", "生物",
    ],
}


# =============================================
# RSSソース定義
# =============================================

RSS_SOURCES = [
    # --- AI関連 ---
    {"url": "https://blog.anthropic.com/rss.xml", "category": "ai", "source": "Anthropic Blog"},
    {"url": "https://blog.google/technology/ai/rss/", "category": "ai", "source": "Google AI Blog"},
    {"url": "https://openai.com/blog/rss.xml", "category": "ai", "source": "OpenAI Blog"},
    {"url": "https://www.itmedia.co.jp/news/rss/2.0/news_ai.xml", "category": "ai", "source": "ITmedia AI+"},
    {"url": "https://gigazine.net/news/rss_2.0/", "category": "ai", "source": "GIGAZINE",
     "keywords": ["ChatGPT", "Claude", "Gemini", "GPT-4", "GPT-5", "生成AI",
                   "大規模言語モデル", "LLM", "OpenAI", "Anthropic", "Google AI",
                   "Copilot", "Midjourney", "Stable Diffusion", "DALL-E",
                   "機械学習", "深層学習", "ニューラルネットワーク"],
     "exclude_keywords": ["マラソン", "レシピ", "ダイエット", "占い", "芸能"]},
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "category": "ai",
     "source": "TechCrunch AI",
     "keywords": ["AI", "LLM", "GPT", "Claude", "Gemini", "machine learning",
                   "OpenAI", "Anthropic", "Google"]},

    # --- 国内主要ニュース ---
    {"url": "https://www3.nhk.or.jp/rss/news/cat0.xml", "category": "domestic", "source": "NHK",
     "exclude_keywords": ["台風", "大雨", "暴風", "洪水", "津波", "噴火",
                           "火山", "竜巻", "猛暑", "大雪", "警報"]},

    # --- 理科系トピック ---
    # 地震関連はAPI（後述）

    # 天文
    {"url": "https://www.nao.ac.jp/rss/atom.xml", "category": "science", "source": "国立天文台"},
    {"url": "https://www.astroarts.co.jp/article/feed.xml", "category": "science", "source": "AstroArts"},
    {"url": "https://spaceweather.com/rssnews.php", "category": "science", "source": "SpaceWeather"},

    # 気象・防災（NHKから抽出）
    {"url": "https://www3.nhk.or.jp/rss/news/cat0.xml", "category": "science", "source": "NHK 防災",
     "keywords": ["台風", "大雨", "暴風", "洪水", "津波", "噴火", "火山",
                   "竜巻", "猛暑", "熱中症", "大雪", "警報"]},

    # 新発見・科学
    {"url": "http://feeds.nature.com/nature/rss/current", "category": "science", "source": "Nature",
     "keywords": ["new species", "新種", "discovery", "発見", "fossil", "化石",
                   "asteroid", "小惑星", "exoplanet", "系外惑星", "earthquake",
                   "volcano", "climate"]},
    {"url": "https://www.eurekalert.org/rss/technology_engineering.xml", "category": "science",
     "source": "EurekAlert",
     "keywords": ["species", "discovery", "fossil", "ocean", "biodiversity",
                   "earthquake", "volcano", "asteroid", "comet"]},
    {"url": "https://natgeo.nikkeibp.co.jp/atcl/news/feed/rss.xml", "category": "science",
     "source": "ナショジオ",
     "keywords": ["新種", "発見", "化石", "生物", "深海", "火山", "地震",
                   "天文", "宇宙", "気象", "恐竜"]},
]


# =============================================
# 1. RSS収集
# =============================================

def fetch_rss(url: str, category: str, source: str,
              keywords: list[str] | None = None,
              exclude_keywords: list[str] | None = None) -> list[dict]:
    """RSSフィードから記事を取得する。"""
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        log.warning(f"RSS取得失敗: {url} → {e}")
        return []

    cutoff = NOW - timedelta(hours=COLLECT_HOURS)
    articles = []

    for entry in feed.entries:
        # 日付フィルタ
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        if published:
            pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
            if pub_dt < cutoff:
                continue

        title = entry.get("title", "").strip()
        summary = entry.get("summary", entry.get("description", "")).strip()
        link = entry.get("link", "")
        text = f"{title} {summary}".lower()

        # 除外キーワード
        if exclude_keywords and any(kw.lower() in text for kw in exclude_keywords):
            continue

        # キーワードフィルタ（設定されている場合のみ）
        if keywords and not any(kw.lower() in text for kw in keywords):
            continue

        articles.append({
            "title": title,
            "summary": summary[:500],
            "link": link,
            "source": source,
            "category": category,
            "published": pub_dt.isoformat() if published else NOW.isoformat(),
        })

    return articles


# =============================================
# 2. 地震API
# =============================================

def fetch_earthquake_jma() -> list[dict]:
    """気象庁の地震情報を取得する（震度3以上）。"""
    url = "https://www.jma.go.jp/bosai/quake/data/list.json"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning(f"気象庁API失敗: {e}")
        return []

    cutoff = NOW - timedelta(hours=COLLECT_HOURS)
    articles = []

    for quake in data[:20]:
        try:
            time_str = quake.get("at", "")
            if not time_str:
                continue

            q_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            if q_time < cutoff:
                continue

            max_int = quake.get("max_int", "")
            int_value = max_int.replace("+", "").replace("-", "").replace("弱", "").replace("強", "")
            if not int_value or not int_value.isdigit() or int(int_value) < 3:
                continue

            place = quake.get("anm", "不明")
            mag = quake.get("mag", "?")
            depth = quake.get("dep", "?")

            title = f"地震: {place} 震度{max_int}（M{mag}）"
            summary = f"{time_str} 深さ{depth}km 最大震度{max_int}"
            link = f"https://www.jma.go.jp/bosai/quake/"

            articles.append({
                "title": title,
                "summary": summary,
                "link": link,
                "source": "気象庁",
                "category": "science",
                "published": q_time.isoformat(),
            })
        except (ValueError, KeyError):
            continue

    return articles


def fetch_earthquake_usgs() -> list[dict]:
    """USGS地震情報（M6.0以上の世界の地震）。"""
    url = ("https://earthquake.usgs.gov/fdsnws/event/1/query"
           "?format=geojson&minmagnitude=6.0&orderby=time&limit=5")
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning(f"USGS API失敗: {e}")
        return []

    cutoff = NOW - timedelta(hours=COLLECT_HOURS)
    articles = []

    for feature in data.get("features", []):
        props = feature["properties"]
        q_time = datetime.fromtimestamp(props["time"] / 1000, tz=timezone.utc)
        if q_time < cutoff:
            continue

        mag = props.get("mag", "?")
        place = props.get("place", "不明")
        url_detail = props.get("url", "")

        title = f"海外地震: {place}（M{mag}）"
        summary = f"{q_time.astimezone(JST).strftime('%m/%d %H:%M JST')} {place}"

        articles.append({
            "title": title,
            "summary": summary,
            "link": url_detail,
            "source": "USGS",
            "category": "science",
            "published": q_time.isoformat(),
        })

    return articles


# =============================================
# 3. 重複排除 & フィルタリング
# =============================================

def load_seen() -> set[str]:
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text())
            return set(data.get("seen", []))
        except Exception:
            pass
    return set()


def save_seen(seen: set[str]):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps({"seen": list(seen)[-3000:]}, ensure_ascii=False))


def url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def deduplicate(articles: list[dict], seen: set[str]) -> list[dict]:
    new_articles = []
    for art in articles:
        h = url_hash(art["link"])
        if h not in seen:
            seen.add(h)
            new_articles.append(art)
    return new_articles


def deduplicate_by_title(articles: list[dict]) -> list[dict]:
    seen_titles: set[str] = set()
    result = []
    for art in articles:
        normalized = art["title"].strip().lower()
        if normalized not in seen_titles:
            seen_titles.add(normalized)
            result.append(art)
    return result


def limit_per_source(articles: list[dict]) -> list[dict]:
    source_count: Counter = Counter()
    result = []
    for art in articles:
        if source_count[art["source"]] < MAX_PER_SOURCE:
            result.append(art)
            source_count[art["source"]] += 1
    return result


# =============================================
# 4. 重要度スコアリング（Pythonのみ、トークン0）
# =============================================

def score_article(article: dict) -> int:
    """キーワードベースで重要度スコアを算出する。"""
    text = f"{article['title']} {article['summary']}".lower()
    score = 0
    for kw in IMPORTANCE_KEYWORDS["high"]:
        if kw.lower() in text:
            score += 3
    for kw in IMPORTANCE_KEYWORDS["mid"]:
        if kw.lower() in text:
            score += 2
    for kw in IMPORTANCE_KEYWORDS["low"]:
        if kw.lower() in text:
            score += 1
    return score


def rank_and_limit(articles: list[dict]) -> list[dict]:
    """スコア順にソートし、カテゴリごとに上限を適用する。"""
    for art in articles:
        art["importance_score"] = score_article(art)

    articles.sort(key=lambda a: a["importance_score"], reverse=True)

    cat_count: Counter = Counter()
    result = []
    for art in articles:
        cat = art["category"]
        if cat_count[cat] < MAX_PER_CATEGORY:
            result.append(art)
            cat_count[cat] += 1

    return result


# =============================================
# 5. メイン処理
# =============================================

def main():
    log.info("=" * 50)
    log.info("Phase 1: データ収集 + 前処理 開始")
    log.info(f"現在時刻: {NOW.isoformat()}")
    log.info(f"収集範囲: 直近{COLLECT_HOURS}時間")
    log.info("=" * 50)

    # 1. 収集
    articles = []
    for src in RSS_SOURCES:
        log.info(f"取得中: {src['source']} ({src['url'][:60]}...)")
        arts = fetch_rss(
            src["url"], src["category"], src["source"],
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

    log.info(f"収集完了: 全{len(articles)}件")

    # 2. 重複排除 & フィルタ
    articles = deduplicate_by_title(articles)
    articles = limit_per_source(articles)
    log.info(f"ソースフィルタ後: {len(articles)}件")

    seen = load_seen()
    articles = deduplicate(articles, seen)
    save_seen(seen)
    log.info(f"重複排除後: {len(articles)}件")

    # 3. 重要度スコアリング & カテゴリ上限
    articles = rank_and_limit(articles)
    log.info(f"ランキング後: {len(articles)}件")

    # 4. raw_news.json に出力
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "collected_at": NOW.isoformat(),
        "total_count": len(articles),
        "articles": articles,
    }
    RAW_NEWS_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    log.info(f"出力完了: {RAW_NEWS_FILE}")

    # 件数を標準出力に出す（シェルスクリプトのゲートキーパー用）
    print(f"NEWS_COUNT={len(articles)}")

    log.info("Phase 1 完了")
    return len(articles)


if __name__ == "__main__":
    count = main()
    sys.exit(0 if count >= 0 else 1)
