#!/bin/sh
set -eu

PORT="${1:-9222}"
FORCE_RESTART="${2:-}"
PROFILE_DIR="$(cd "$(dirname "$0")" && pwd)/.local-chrome-profile"
if [ "$FORCE_RESTART" != "--reset" ]; then
  PROFILE_DIR="${FORCE_RESTART:-$PROFILE_DIR}"
  FORCE_RESTART=""
fi

mkdir -p "$PROFILE_DIR"

ws_allowed() {
  # 用 Docker 网关 origin 探测：缺 --remote-allow-origins 时会 403
  python3 - "$PORT" <<'PY'
import base64
import json
import os
import socket
import sys
import urllib.request
from urllib.parse import urlparse

port = int(sys.argv[1])
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as resp:
        data = json.loads(resp.read().decode())
except Exception:
    sys.exit(1)

ws = data.get("webSocketDebuggerUrl") or ""
if not ws:
    sys.exit(1)

parsed = urlparse(ws)
key = base64.b64encode(os.urandom(16)).decode()
request = (
    f"GET {parsed.path} HTTP/1.1\r\n"
    f"Host: 127.0.0.1:{port}\r\n"
    f"Upgrade: websocket\r\n"
    f"Connection: Upgrade\r\n"
    f"Sec-WebSocket-Key: {key}\r\n"
    f"Sec-WebSocket-Version: 13\r\n"
    f"Origin: http://192.168.65.254:{port}\r\n"
    f"\r\n"
).encode()
sock = socket.create_connection(("127.0.0.1", port), timeout=3)
sock.sendall(request)
response = sock.recv(256).decode("latin1", errors="ignore")
sock.close()
sys.exit(0 if response.startswith("HTTP/1.1 101") else 2)
PY
}

ensure_page_target() {
  # DrissionPage 4.x 连接 CDP 时要求 /json 至少存在一个 page/webview target。
  # macOS 上用 open -na 拉起无痕 Chrome 偶尔只启动调试进程、不创建窗口，
  # 此时 HTTP/WebSocket 虽可用，DrissionPage 仍会拒绝连接。
  python3 - "$PORT" <<'PY'
import json
import sys
import urllib.request

port = int(sys.argv[1])
base = f"http://127.0.0.1:{port}"
try:
    with urllib.request.urlopen(f"{base}/json", timeout=3) as response:
        targets = json.loads(response.read().decode("utf-8"))
    if any(item.get("type") in {"page", "webview"} for item in targets):
        sys.exit(0)

    request = urllib.request.Request(
        f"{base}/json/new?about:blank",
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        created = json.loads(response.read().decode("utf-8"))
    sys.exit(0 if created.get("type") in {"page", "webview"} else 1)
except Exception:
    sys.exit(1)
PY
}


minimize_debug_chrome() {
  # 启动后缩到程序坞，减少抢占前台。仅操作本 CDP 端口对应的调试 Chrome。
  MINIMIZE_SCRIPT="$(cd "$(dirname "$0")" && pwd)/minimize_local_chrome.py"
  if [ -f "$MINIMIZE_SCRIPT" ] && command -v python3 >/dev/null 2>&1; then
    python3 "$MINIMIZE_SCRIPT" "$PORT" >/dev/null 2>&1 || return $?
    return 0
  fi
  return 1
}

needs_restart=0
if [ "$FORCE_RESTART" = "--reset" ]; then
  needs_restart=1
fi
if [ "$needs_restart" -eq 0 ] && curl -fsS "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
  if command -v python3 >/dev/null 2>&1; then
    set +e
    ws_allowed
    code=$?
    set -e
    if [ "$code" -eq 0 ]; then
      set +e
      ensure_page_target
      page_code=$?
      set -e
      if [ "$page_code" -eq 0 ]; then
        set +e
        minimize_debug_chrome
        min_code=$?
        set -e
        if [ "$min_code" -eq 0 ]; then
          printf '%s\n' "Local Chrome CDP already available on 127.0.0.1:${PORT} (已最小化到程序坞)"
        else
          printf '%s\n' "Local Chrome CDP already available on 127.0.0.1:${PORT}"
        fi
        curl -fsS "http://127.0.0.1:${PORT}/json/version" || true
        exit 0
      fi
      printf '%s\n' "检测到 Chrome 未创建页面标签，将重启调试 Chrome..."
      needs_restart=1
    elif [ "$code" -eq 2 ]; then
      printf '%s\n' "检测到 Chrome CDP 拒绝远程 WebSocket（缺少 --remote-allow-origins），将重启调试 Chrome..."
      needs_restart=1
    fi
  else
    printf '%s\n' "Local Chrome CDP already available on 127.0.0.1:${PORT}"
    exit 0
  fi
fi

if [ "$needs_restart" -eq 1 ]; then
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -t -iTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
    if [ -n "${pids:-}" ]; then
      printf '%s\n' "关闭旧调试 Chrome (pid: $pids) ..."
      # shellcheck disable=SC2086
      kill $pids 2>/dev/null || true
      sleep 1
    fi
  fi
fi

# Chrome 111+ 必须带 remote-allow-origins，Docker 才能建立 CDP WebSocket
# 小窗口启动，就绪后再 CDP 最小化到程序坞，减少打断日常使用
open -na "Google Chrome" --args \
  --remote-debugging-port="$PORT" \
  --remote-allow-origins="*" \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  --window-size=900,700 \
  --window-position=80,80 \
  --incognito

i=0
while [ "$i" -lt 40 ]; do
  if curl -fsS "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
    if command -v python3 >/dev/null 2>&1; then
      set +e
      ws_allowed
      code=$?
      set -e
      if [ "$code" -eq 0 ]; then
        set +e
        ensure_page_target
        page_code=$?
        set -e
        if [ "$page_code" -eq 0 ]; then
          set +e
          minimize_debug_chrome
          min_code=$?
          set -e
          printf '%s\n' "Local Chrome started on CDP port $PORT (remote-allow-origins=*)"
          printf '%s\n' "Profile directory: $PROFILE_DIR"
          if [ "$min_code" -eq 0 ]; then
            printf '%s\n' "已将调试 Chrome 最小化到程序坞（不影响你日常 Chrome）"
          else
            printf '%s\n' "提示: 自动最小化失败时可手动点黄灯；任务仍会继续"
          fi
          printf '%s\n' "Docker 控制台请选：本地 Chrome 无痕"
          exit 0
        fi
      fi
    else
      printf '%s\n' "Local Chrome started on CDP port $PORT"
      exit 0
    fi
  fi
  i=$((i + 1))
  sleep 0.25
done

printf '%s\n' "Chrome 已尝试启动，但 CDP/WebSocket 未就绪 (port=$PORT)。" >&2
printf '%s\n' "请检查: curl http://127.0.0.1:${PORT}/json/version" >&2
exit 1
