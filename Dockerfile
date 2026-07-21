FROM python:3.13-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get -o Acquire::Retries=5 update \
    && for attempt in 1 2 3 4 5; do \
        apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
            ca-certificates \
            chromium \
            fonts-liberation \
            fonts-noto-color-emoji \
            tk \
            xauth \
            x11vnc \
            novnc \
            websockify \
            xvfb \
        && break; \
        if [ "$attempt" = "5" ]; then exit 1; fi; \
        sleep 2; \
    done \
    && rm -rf /var/lib/apt/lists/*

ENV GROK_DOCKER=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install -r requirements.txt

COPY . .
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["cli"]
