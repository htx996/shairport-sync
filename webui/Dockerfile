FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    CONFIG_DIR=/config \
    DATA_DIR=/data \
    PANEL_HOST=0.0.0.0 \
    PANEL_PORT=8099 \
    SHAIRPORT_CONTAINER=shairport-sync

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      alsa-utils \
      ca-certificates \
      tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY webui/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY webui/app.py ./app.py
COPY webui/templates ./templates
COPY upstream-versions.json ./upstream-versions.json

VOLUME ["/config", "/data"]
EXPOSE 8099

CMD ["sh", "-c", "gunicorn --bind ${PANEL_HOST}:${PANEL_PORT} --workers 1 --threads 4 app:app"]
