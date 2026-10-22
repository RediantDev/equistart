#!/bin/bash
# сброс БД без остановки сервера
psql -h 127.0.0.1 -U postgres -q -c "drop database if exists equistart with (force)" -c "create database equistart owner equistart"
. /home/claude/env.sh; cd /home/claude/equistart && python3 manage.py migrate --noinput >/dev/null && python3 manage.py createcachetable
rm -f /home/claude/sim/*.jsonl; rm -rf /home/claude/equistart/media
echo reset-done
