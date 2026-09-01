FROM python:3.12-slim

LABEL org.opencontainers.image.title="RsyncWebUI" \
      org.opencontainers.image.description="Web interface for rsync with scheduling, built for Unraid" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="1.2.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    DATA_DIR=/config \
    BROWSE_ROOTS=/mnt,/data,/config \
    TZ=Europe/Berlin

RUN apt-get update && apt-get install -y --no-install-recommends \
        rsync \
        openssh-client \
        sshpass \
        ca-certificates \
        tzdata \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY wsgi.py .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

VOLUME ["/config"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
