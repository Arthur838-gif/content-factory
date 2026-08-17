#!/usr/bin/env bash
# P-1a 验收（计划书附录 B 方式）：对运行中的服务手动触发采集并核对数据库。
#
# 前置：
#   .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
#   离线环境启动时可加：RSSHUB_BASE_URL=file://$(pwd)/tests/fixtures
# 环境变量：
#   BASE   服务地址（默认 http://127.0.0.1:8000）
#   DB     SQLite 路径（默认 data/app.db）
#   NOTIFY_WEBHOOK  如已配置则告警演练走真实通道
set -euo pipefail
cd "$(dirname "$0")/.."
BASE="${BASE:-http://127.0.0.1:8000}"
DB="${DB:-data/app.db}"
PY="${PY:-.venv/bin/python}"

echo "== 手动触发采集（第 1 次） =="
curl -sS -X POST "$BASE/api/collectors/hotboard/run"; echo
echo "== 手动触发采集（第 2 次，去重应生效：inserted=0） =="
curl -sS -X POST "$BASE/api/collectors/hotboard/run"; echo

echo "== hot_items 按来源统计 =="
sqlite3 "$DB" "SELECT source, COUNT(*) FROM hot_items GROUP BY source;"
echo "== source=radar 的候选选题 =="
sqlite3 "$DB" "SELECT id, domain, title FROM topics WHERE source='radar' LIMIT 5;"

echo "== 备份演练 =="
"$PY" -c "from app.services.scheduler import backup_database; print('备份文件：', backup_database())"
echo "备份目录现有："; ls data/backups/ || true

echo "== 告警通道演练 =="
"$PY" -m app.services.notify WARN test 通道演练 "P-1a 验收"

echo
echo "判读：第 2 次采集 inserted 应为 0；topics 应有 source=radar 的行；"
echo "告警显示'已外发'需 NOTIFY_WEBHOOK 已配置，否则为日志降级（配置后重跑本脚本）。"
