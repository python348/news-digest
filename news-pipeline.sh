#!/bin/bash
# =============================================
# news-pipeline.sh
# 3フェーズパイプラインの橋渡しスクリプト
#
# 役割:
#   1. ゲートキーパー: 記事0件ならLLM起動しない（コスト0）
#   2. フェーズ分離: 各フェーズの責務を明確にする
#   3. ログ管理: 開始・終了をログに記録
# =============================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="${SCRIPT_DIR}/data/pipeline.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "=========================================="
log "ニュースパイプライン 開始"
log "=========================================="

# ── Phase 1: Python（トークン消費ゼロ）──────────
log "Phase 1: データ収集 + 前処理"
PHASE1_OUTPUT=$(python3 "${SCRIPT_DIR}/phase1_collect.py" 2>>"$LOG_FILE")
NEWS_COUNT=$(echo "$PHASE1_OUTPUT" | grep -oP 'NEWS_COUNT=\K\d+' || echo "0")
log "Phase 1 完了: ${NEWS_COUNT}件"

# ── ゲートキーパー ────────────────────────────
if [ "$NEWS_COUNT" -eq 0 ]; then
    log "記事が0件 → Phase 2/3 をスキップ（LLMコスト: ¥0）"
    log "パイプライン終了（空振り防止）"
    exit 0
fi

# ── Phase 2: Groq API（LLMの仕事）─────────────
log "Phase 2: LLM選別 + 要約生成"
PHASE2_OUTPUT=$(python3 "${SCRIPT_DIR}/phase2_enrich.py" 2>>"$LOG_FILE")
PUBLISH_COUNT=$(echo "$PHASE2_OUTPUT" | grep -oP 'PUBLISH_COUNT=\K\d+' || echo "0")
log "Phase 2 完了: 配信対象 ${PUBLISH_COUNT}件"

# ── ゲートキーパー（2回目）──────────────────────
if [ "$PUBLISH_COUNT" -eq 0 ]; then
    log "S/Aランク記事が0件 → Phase 3 をスキップ"
    log "パイプライン終了（重要ニュースなし）"
    exit 0
fi

# ── Phase 3: Slack投稿 ─────────────────────────
log "Phase 3: Slack投稿"
python3 "${SCRIPT_DIR}/phase3_post.py" 2>>"$LOG_FILE"
log "Phase 3 完了"

log "=========================================="
log "パイプライン正常終了"
log "=========================================="
