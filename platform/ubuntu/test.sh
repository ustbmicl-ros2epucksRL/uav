#!/bin/sh
# 在 Ubuntu 容器里跑三套服务的单元测试，再在同一台服务器网络里做一次飞行联调。
set -eu
cd "$(dirname "$0")"
docker compose build
docker compose run --rm --no-deps --entrypoint pytest planning -q tests
docker compose run --rm --no-deps --entrypoint pytest flight -q tests
docker compose run --rm --no-deps --entrypoint pytest lowair -q tests
docker compose up -d --wait planning flight lowair
status=0
docker compose run --rm --no-deps tester || status=$?
docker compose down
exit "$status"
