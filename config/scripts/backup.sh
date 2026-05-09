#!/usr/bin/env bash
# FONCIER+ v3.5.2 — backup.sh (quotidien, rétention 7j)
set -euo pipefail
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="/backups"
mkdir -p "$BACKUP_DIR"

pg_dump -Fc -Z6 -f "$BACKUP_DIR/foncier-$STAMP.dump"
echo "✓ Dump $BACKUP_DIR/foncier-$STAMP.dump ($(du -h "$BACKUP_DIR/foncier-$STAMP.dump" | cut -f1))"

find "$BACKUP_DIR" -name "foncier-*.dump" -mtime +7 -delete
echo "✓ Nettoyage backups > 7j"
