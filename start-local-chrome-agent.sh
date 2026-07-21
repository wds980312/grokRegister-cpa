#!/bin/sh
set -eu
cd "$(dirname "$0")"
export LOCAL_CHROME_AGENT_HOST="${LOCAL_CHROME_AGENT_HOST:-127.0.0.1}"
export LOCAL_CHROME_AGENT_PORT="${LOCAL_CHROME_AGENT_PORT:-18083}"
exec python3 local_chrome_agent.py
