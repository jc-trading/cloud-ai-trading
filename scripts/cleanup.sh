#!/bin/bash

echo "=========================================="
echo "CloudAiTrading 文件清理"
echo "=========================================="

cd "$(dirname "$0")" || exit 1

echo ""
echo "准备删除以下诊断和测试脚本："
echo ""

# 列出要删除的文件
FILES_TO_DELETE=(
  "diagnose_migration.sh"
  "diagnose_migration_fixed.sh"
  "reset_and_migrate.sh"
  "reset_and_migrate_fixed.sh"
  "diagnose_final.sh"
  "reset_final.sh"
  "debug_migration.sh"
  "force_migrate_007.sh"
  "fallback_create_system_tables.sql"
  "fallback_execute.sh"
  "check_alembic.sh"
  "alembic_diagnosis.txt"
  "diagnostic_output.txt"
  "reset_output.txt"
)

for file in "${FILES_TO_DELETE[@]}"; do
  if [ -f "$file" ]; then
    echo "  ❌ $file"
  fi
done

echo ""
read -p "确认删除上述文件？(y/n): " confirm

if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
  for file in "${FILES_TO_DELETE[@]}"; do
    if [ -f "$file" ]; then
      rm "$file"
      echo "✅ 已删除: $file"
    fi
  done
  echo ""
  echo "=========================================="
  echo "清理完成！"
  echo "=========================================="
else
  echo "取消删除"
fi
