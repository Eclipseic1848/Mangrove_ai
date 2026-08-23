#!/bin/sh
set -eu

# 三类运行资料必须位于验收数据卷，缺失或配置为空时拒绝启动。
: "${DATA_PREP_UPLOAD_ROOT:?DATA_PREP_UPLOAD_ROOT is required}"
: "${SEMANTIC_EXECUTION_ROOT:?SEMANTIC_EXECUTION_ROOT is required}"
: "${DATA_PREP_ARTIFACT_ROOT:?DATA_PREP_ARTIFACT_ROOT is required}"

for managed_root in \
    "$DATA_PREP_UPLOAD_ROOT" \
    "$SEMANTIC_EXECUTION_ROOT" \
    "$DATA_PREP_ARTIFACT_ROOT"
do
    case "$managed_root" in
        /app/data/*) mkdir -p -- "$managed_root" ;;
        *) echo "managed root must stay under /app/data" >&2; exit 64 ;;
    esac
done

if [ "$#" -eq 0 ]; then
    echo "application command is required" >&2
    exit 64
fi

exec "$@"
