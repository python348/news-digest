"""
Phase 3: Slack投稿
==================
Phase 2 が出力した enriched_news.json を読み、
Slack Webhook で整形投稿する。

記事が0件の場合は投稿しない（ゲートキーパーで弾かれる想定）。
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ── ログ設定 ─────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── 環境変数 ─────────────────────────────────
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# ── パス ──────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
ENRICHED_FILE = DATA_DIR / "enriched_news.json"

# ── 定数 ──────────────────────────────────────
JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST)

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

RANK_EMOJI = {
    "S": "🔴",
    "A": "🟠",
    "B": "🟡",
    "C": "⚪",
}

# 配信時間帯の表示
SLOT_NAMES = {
    7: "朝刊",
    13: "昼刊",
    19: "夕刊",
}


# =============================================
# Slack投稿メッセージ組み立て
# =============================================

def get_slot_name() -> str:
    hour = NOW.hour
    if hour < 10:
        return SLOT_NAMES.get(7, "朝刊")
    elif hour < 16:
        return SLOT_NAMES.get(13, "昼刊")
    else:
        return SLOT_NAMES.get(19, "夕刊")


def build_slack_message(articles: list[dict], skipped: list[dict],
                        rank_dist: dict) -> str:
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    wd = weekdays[NOW.weekday()]
    date_str = NOW.strftime(f"%Y/%m/%d（{wd}）")
    slot = get_slot_name()

    lines = [f"🗞️ *ニュースダイジェスト {slot}*（{date_str}）"]

    # ランク分布を表示
    dist_str = " / ".join(f"{k}:{v}件" for k, v in sorted(rank_dist.items()))
    lines.append(f"_判定結果: {dist_str} → S/Aランクのみ詳細配信_\n")

    # ── S/Aランク: カテゴリ別に詳細表示 ──────────
    for cat_key in ["ai", "domestic", "science"]:
        cat_articles = [a for a in articles if a["category"] == cat_key]

        # 理科系は該当なしならスキップ
        if cat_key == "science" and not cat_articles:
            continue

        emoji = EMOJI.get(cat_key, "📌")
        name = CATEGORY_NAMES.get(cat_key, cat_key)

        lines.append(f"{'━' * 20}")
        lines.append(f"{emoji} *{name}*")
        lines.append(f"{'━' * 20}\n")

        if not cat_articles:
            lines.append("該当なし\n")
            continue

        for art in cat_articles:
            rank = art.get("rank", "B")
            rank_em = RANK_EMOJI.get(rank, "⚪")
            rank_label = f"[{rank}]"

            # 入試関連度バッジ
            exam = art.get("exam_relevance", "none")
            exam_badge = ""
            if exam == "high":
                exam_badge = " 📝入試頻出"
            elif exam == "medium":
                exam_badge = " 📝入試関連"

            lines.append(f"{rank_em}{rank_label} *{art['title']}*{exam_badge}")
            lines.append(art.get("ai_summary", art["summary"][:200]))
            insight = art.get("ai_insight", "")
            if insight:
                lines.append(f"💡 {insight}")
            lines.append(f"🔗 {art['link']}")
            lines.append("")

    # ── B/Cランク: タイトルのみ一覧 ──────────────
    if skipped:
        lines.append(f"{'─' * 20}")
        lines.append("📋 *その他（B/Cランク・タイトルのみ）*")
        lines.append(f"{'─' * 20}")

        b_articles = [a for a in skipped if a.get("rank") == "B"]
        c_articles = [a for a in skipped if a.get("rank") == "C"]

        if b_articles:
            lines.append("")
            lines.append("*▸ Bランク*")
            for art in b_articles:
                exam = art.get("exam_relevance", "none")
                exam_tag = ""
                if exam == "high":
                    exam_tag = " 📝"
                elif exam == "medium":
                    exam_tag = " 📝"
                lines.append(f"  • {art['title']}{exam_tag}")

        if c_articles:
            lines.append("")
            lines.append("*▸ Cランク*")
            for art in c_articles:
                lines.append(f"  • _{art['title']}_")

        lines.append("")

    lines.append(f"_配信時刻: {NOW.strftime('%H:%M JST')}_")
    return "\n".join(lines)


# =============================================
# Slack投稿
# =============================================

def post_to_slack(message: str):
    if not SLACK_WEBHOOK_URL:
        log.warning("SLACK_WEBHOOK_URL 未設定 → 標準出力に表示")
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
# メイン処理
# =============================================

def main():
    log.info("=" * 50)
    log.info("Phase 3: Slack投稿 開始")
    log.info("=" * 50)

    if not ENRICHED_FILE.exists():
        log.error(f"{ENRICHED_FILE} が見つかりません。Phase 2 を先に実行してください。")
        sys.exit(1)

    data = json.loads(ENRICHED_FILE.read_text())
    articles = data.get("articles", [])
    skipped = data.get("skipped_articles", [])
    rank_dist = data.get("rank_distribution", {})

    if not articles:
        log.info("配信対象が0件 → 投稿スキップ")
        sys.exit(0)

    log.info(f"配信対象: {len(articles)}件（S/A）+ {len(skipped)}件（B/C）")

    message = build_slack_message(articles, skipped, rank_dist)
    post_to_slack(message)

    log.info("Phase 3 完了")


if __name__ == "__main__":
    main()
