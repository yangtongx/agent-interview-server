#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f data/public-bank.json ]]; then
  echo "未找到 data/public-bank.json，先执行 npm run dump-bank"
  exit 1
fi

rm -rf dist
mkdir -p dist/data
cp -a server dist/server
cp -a data/public-bank.json dist/data/public-bank.json
if [[ -d out ]]; then
  cp -a out dist/out
fi
if [[ -f .env.example ]]; then
  cp -a .env.example dist/.env.example
fi

mkdir -p release
STAMP="$(date +%Y%m%d%H%M)"
TAR="release/ali-agent-interview-server-${STAMP}.tar.gz"
tar -C dist -czf "$TAR" .
ln -sfn "$(basename "$TAR")" release/ali-agent-interview-server-latest.tar.gz
echo "已打包 $TAR"
echo "上传后： tar -xzf ... && python3 -m pip install -r server/requirements.txt && python3 -m server"
