#!/bin/sh
set -eu

if [ "$#" -eq 0 ]; then
    set -- cli
fi

start_display_stack() {
    export DISPLAY="${DISPLAY:-:99}"
    display_num="${DISPLAY#:}"
    # 清理上次异常退出残留的 X 锁/套接字，否则 Xvfb 起不来、noVNC 会一直“无法连接到服务器”
    rm -f "/tmp/.X${display_num}-lock" "/tmp/.X11-unix/X${display_num}" 2>/dev/null || true
    mkdir -p /tmp/.X11-unix
    chmod 1777 /tmp/.X11-unix 2>/dev/null || true

    Xvfb "$DISPLAY" -screen 0 1440x900x24 -ac +extension GLX +render -noreset \
        >/tmp/grok-xvfb.log 2>&1 &
    xvfb_pid=$!

    # 等 X 套接字就绪，避免 x11vnc 抢跑失败
    i=0
    while [ "$i" -lt 30 ]; do
        if [ -S "/tmp/.X11-unix/X${display_num}" ] && kill -0 "$xvfb_pid" 2>/dev/null; then
            break
        fi
        i=$((i + 1))
        sleep 0.1
    done
    if ! kill -0 "$xvfb_pid" 2>/dev/null; then
        echo "[entrypoint] Xvfb 启动失败，见 /tmp/grok-xvfb.log" >&2
        cat /tmp/grok-xvfb.log >&2 || true
        return 1
    fi

    x11vnc -display "$DISPLAY" -rfbport 5900 -localhost -forever -shared -nopw -noxdamage \
        >/tmp/grok-x11vnc.log 2>&1 &
    x11vnc_pid=$!

    i=0
    while [ "$i" -lt 30 ]; do
        if kill -0 "$x11vnc_pid" 2>/dev/null; then
            # 端口就绪再继续
            if (command -v ss >/dev/null 2>&1 && ss -lnt | grep -q ':5900') \
                || (command -v netstat >/dev/null 2>&1 && netstat -lnt 2>/dev/null | grep -q ':5900') \
                || true; then
                # 轻探测：进程还在即可
                break
            fi
        fi
        i=$((i + 1))
        sleep 0.1
    done
    if ! kill -0 "$x11vnc_pid" 2>/dev/null; then
        echo "[entrypoint] x11vnc 启动失败，见 /tmp/grok-x11vnc.log" >&2
        cat /tmp/grok-x11vnc.log >&2 || true
        return 1
    fi

    websockify --web=/usr/share/novnc --heartbeat=30 0.0.0.0:6080 127.0.0.1:5900 \
        >/tmp/grok-websockify.log 2>&1 &
    websockify_pid=$!

    # 后台看门：Xvfb/x11vnc 挂了就自动拉起，避免 noVNC 假死
    (
        while true; do
            sleep 5
            if ! kill -0 "$xvfb_pid" 2>/dev/null; then
                echo "[entrypoint] Xvfb 已退出，尝试重启..." >&2
                rm -f "/tmp/.X${display_num}-lock" "/tmp/.X11-unix/X${display_num}" 2>/dev/null || true
                Xvfb "$DISPLAY" -screen 0 1440x900x24 -ac +extension GLX +render -noreset \
                    >/tmp/grok-xvfb.log 2>&1 &
                xvfb_pid=$!
                sleep 1
            fi
            if ! kill -0 "$x11vnc_pid" 2>/dev/null; then
                echo "[entrypoint] x11vnc 已退出，尝试重启..." >&2
                x11vnc -display "$DISPLAY" -rfbport 5900 -localhost -forever -shared -nopw -noxdamage \
                    >/tmp/grok-x11vnc.log 2>&1 &
                x11vnc_pid=$!
            fi
            if ! kill -0 "$websockify_pid" 2>/dev/null; then
                echo "[entrypoint] websockify 已退出，尝试重启..." >&2
                websockify --web=/usr/share/novnc --heartbeat=30 0.0.0.0:6080 127.0.0.1:5900 \
                    >/tmp/grok-websockify.log 2>&1 &
                websockify_pid=$!
            fi
        done
    ) &
    watchdog_pid=$!
    return 0
}

if [ "$1" = "web" ]; then
    shift
    xvfb_pid=""
    x11vnc_pid=""
    websockify_pid=""
    watchdog_pid=""
    server_pid=""

    start_display_stack || true

    terminate_server() {
        if [ -n "${server_pid:-}" ]; then
            kill -TERM "$server_pid" 2>/dev/null || true
        fi
    }
    cleanup_display() {
        trap - EXIT INT TERM
        if [ -n "${server_pid:-}" ]; then
            kill "$server_pid" 2>/dev/null || true
        fi
        if [ -n "${watchdog_pid:-}" ]; then
            kill "$watchdog_pid" 2>/dev/null || true
        fi
        if [ -n "${websockify_pid:-}" ]; then
            kill "$websockify_pid" 2>/dev/null || true
        fi
        if [ -n "${x11vnc_pid:-}" ]; then
            kill "$x11vnc_pid" 2>/dev/null || true
        fi
        if [ -n "${xvfb_pid:-}" ]; then
            kill "$xvfb_pid" 2>/dev/null || true
        fi
        wait 2>/dev/null || true
    }
    trap terminate_server INT TERM
    trap cleanup_display EXIT

    sleep 0.5
    python3 web_server.py "$@" &
    server_pid=$!
    wait "$server_pid"
    status=$?
    cleanup_display
    exit "$status"
else
    script="grok_register_ttk.py"
fi

if [ "${DOCKER_XVFB:-1}" = "1" ] && command -v xvfb-run >/dev/null 2>&1; then
    exec xvfb-run --auto-servernum --server-args="-screen 0 1440x900x24" \
        python3 "$script" "$@"
fi

exec python3 "$script" "$@"
