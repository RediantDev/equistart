#!/bin/sh
# Ежедневная копия БД и файлов. Запуск из каталога проекта (cron):
#   15 3 * * * cd /opt/equistart && ./deploy/backup.sh >> /var/log/equistart-backup.log 2>&1
# Копии хранятся на этом же сервере в BACKUP_DIR и (рекомендуется) копируются
# во второе хранилище в России: S3-совместимое (Yandex Object Storage, Selectel) через rclone.
set -eu
BACKUP_DIR=${BACKUP_DIR:-/var/backups/equistart}
KEEP_DAYS=${KEEP_DAYS:-30}
STAMP=$(date +%Y%m%d-%H%M)
mkdir -p "$BACKUP_DIR"

docker compose exec -T db pg_dump -U equistart -d equistart --format=custom > "$BACKUP_DIR/db-$STAMP.dump"
docker compose run --rm --no-deps -T -v "$BACKUP_DIR:/backup" --entrypoint tar web czf "/backup/media-$STAMP.tgz" -C /app media

# простая проверка: дамп читается
docker compose exec -T db pg_restore --list < "$BACKUP_DIR/db-$STAMP.dump" > /dev/null

find "$BACKUP_DIR" -name 'db-*.dump' -mtime +"$KEEP_DAYS" -delete
find "$BACKUP_DIR" -name 'media-*.tgz' -mtime +"$KEEP_DAYS" -delete

if [ -n "${RCLONE_REMOTE:-}" ]; then
  rclone copy "$BACKUP_DIR" "$RCLONE_REMOTE" --max-age 2d
fi
# отметка для пульта администратора (последняя копия)
docker compose exec -T web python manage.py backup_done "$STAMP"
echo "$(date -Iseconds) backup ok: $STAMP"
