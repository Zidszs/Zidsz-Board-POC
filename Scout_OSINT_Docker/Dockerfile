FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    iptables libcap2-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-backend.txt ./
RUN pip install --no-cache-dir -r requirements-backend.txt

COPY scout/ scout/

RUN setcap cap_net_bind_service,cap_net_admin+ep $(readlink -f $(which python3)) || true

ENV SCOUT_BACKEND=1
ENV SCOUT_DATA_DIR=/app/data

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health')" || exit 1

CMD ["python", "-m", "scout.server"]
