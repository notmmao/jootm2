#!/usr/bin/env bash
# 一键镜像部署（只推镜像，不动服务器资源文件，无 rsync）
# 用法：bash scripts/deploy_image.sh
# 流程：gen_version → docker build（短hash+latest 双标签）→ push ACR
#       → 阿里云 pull/tag jootm2:python → compose up -d → 验证 /api/version + /api/status
set -euo pipefail
cd "$(dirname "$0")/.."

ACR=crpi-n05futsy39yjkqf2.cn-shanghai.personal.cr.aliyuncs.com/notmmao/mir2-python
SERVER=root@aliyun.notmmao.com
IP=139.224.24.88:8080

HASH=$(git rev-parse --short HEAD)
if [ -n "$(git status --porcelain)" ]; then
  echo "!! 警告：工作区有未提交改动，镜像内容可能与版本号 $HASH 不一致"
fi
echo "==> 部署版本：$HASH $(git log -1 --format=%s)"

# 1. 生成 version.json（构建前必跑）
PY=D:/Python310/python.exe
[ -x "$PY" ] || PY=python
"$PY" scripts/gen_version.py

# 2. 本地构建 + 推 ACR（push 需要已 docker login --username=notmmao@gmail.com）
docker build -t "$ACR:$HASH" -t "$ACR:latest" .
if ! docker push "$ACR:$HASH" || ! docker push "$ACR:latest"; then
  echo "!! push 失败：若是认证问题，先执行 docker login --username=notmmao@gmail.com $ACR"
  exit 1
fi

# 3. 服务器 pull → tag → 重启容器（不碰 /root/jootm2 下的 data/resources）
ssh "$SERVER" "docker pull $ACR:$HASH \
  && docker tag $ACR:$HASH jootm2:python \
  && cd /root/jootm2 && docker compose up -d"

# 4. 验证：/api/version 的 version == 短hash，/api/status 200（最多等 30 秒）
echo "==> 等待服务启动并验证…"
ok=0
for i in $(seq 1 10); do
  sleep 3
  VER=$(curl -s --max-time 5 "http://$IP/api/version" || true)
  if echo "$VER" | grep -Eq "\"version\": ?\"$HASH\""; then ok=1; break; fi
done
if [ "$ok" != 1 ]; then
  echo "!! 版本验证失败（期望 $HASH），最后一次返回：$VER"
  exit 1
fi
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://$IP/api/status")
if [ "$CODE" != 200 ]; then
  echo "!! /api/status 返回 $CODE（期望 200）"
  exit 1
fi
echo "==> 部署完成：version=$HASH，/api/status 200"