#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将指定 CDP 端口上的调试 Chrome 窗口最小化到程序坞。

只操作该调试端口对应的 Browser 窗口，不影响日常使用的 Chrome 窗口。
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import sys
import urllib.request
from urllib.parse import urlparse


def http_json(url: str):
    with urllib.request.urlopen(url, timeout=3) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ws_recv_message(sock: socket.socket) -> dict:
    data = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("websocket closed")
        data += chunk
        while True:
            if len(data) < 2:
                break
            b1, b2 = data[0], data[1]
            length = b2 & 0x7F
            header = 2
            if length == 126:
                if len(data) < 4:
                    break
                length = struct.unpack("!H", data[2:4])[0]
                header = 4
            elif length == 127:
                if len(data) < 10:
                    break
                length = struct.unpack("!Q", data[2:10])[0]
                header = 10
            total = header + length
            if len(data) < total:
                break
            payload = data[header:total]
            data = data[total:]
            opcode = b1 & 0x0F
            if opcode == 0x1:  # text
                return json.loads(payload.decode("utf-8"))
            if opcode == 0x8:  # close
                raise RuntimeError("websocket closed by peer")
            # ignore ping/pong/binary
            continue
    raise RuntimeError("unreachable")


def ws_send_text(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    header = bytearray([0x81])
    n = len(payload)
    mask_bit = 0x80
    if n < 126:
        header.append(mask_bit | n)
    elif n < 65536:
        header.append(mask_bit | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(mask_bit | 127)
        header.extend(struct.pack("!Q", n))
    mask = os.urandom(4)
    header.extend(mask)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(bytes(header) + masked)


def cdp_call(sock: socket.socket, msg_id: int, method: str, params=None):
    body = {"id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    ws_send_text(sock, json.dumps(body, ensure_ascii=False))
    while True:
        msg = ws_recv_message(sock)
        if msg.get("id") == msg_id:
            if "error" in msg:
                raise RuntimeError(str(msg["error"]))
            return msg.get("result") or {}


def minimize(port: int) -> int:
    version = http_json(f"http://127.0.0.1:{port}/json/version")
    ws = str(version.get("webSocketDebuggerUrl") or "").strip()
    if not ws:
        return 1

    parsed = urlparse(ws)
    key = base64.b64encode(os.urandom(16)).decode()
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{parsed.port or port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    ).encode()

    sock = socket.create_connection(
        (parsed.hostname or "127.0.0.1", parsed.port or port),
        timeout=3,
    )
    sock.settimeout(3.0)
    try:
        sock.sendall(request)
        handshake = b""
        while b"\r\n\r\n" not in handshake:
            chunk = sock.recv(1024)
            if not chunk:
                return 1
            handshake += chunk
        if b"101" not in handshake.split(b"\r\n", 1)[0]:
            return 1

        msg_id = 1
        targets = cdp_call(sock, msg_id, "Target.getTargets").get("targetInfos") or []
        msg_id += 1
        window_ids = set()
        for item in targets:
            if item.get("type") not in {"page", "webview"}:
                continue
            target_id = item.get("targetId")
            if not target_id:
                continue
            try:
                result = cdp_call(
                    sock,
                    msg_id,
                    "Browser.getWindowForTarget",
                    {"targetId": target_id},
                )
                msg_id += 1
                window_id = result.get("windowId")
                if window_id is not None:
                    window_ids.add(int(window_id))
            except Exception:
                msg_id += 1
                continue

        if not window_ids:
            return 2

        for window_id in sorted(window_ids):
            cdp_call(
                sock,
                msg_id,
                "Browser.setWindowBounds",
                {
                    "windowId": window_id,
                    "bounds": {"windowState": "minimized"},
                },
            )
            msg_id += 1
        return 0
    finally:
        try:
            sock.close()
        except Exception:
            pass


def main(argv: list[str]) -> int:
    port = 9222
    if len(argv) >= 2 and str(argv[1]).strip().isdigit():
        port = int(argv[1])
    try:
        code = minimize(port)
    except Exception as exc:
        print(f"minimize failed: {exc}", file=sys.stderr)
        return 1
    if code == 0:
        print(f"minimized debug Chrome windows on port {port}")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
