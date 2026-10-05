FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONFAULTHANDLER=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY app.py main_bot.py self_bot.py helper_bot.py self_features.py advanced_features.py admin_ui.py admin_center.py control_store.py session_vault.py db_utils.py send_queue.py ./
COPY start.sh keep_alive.sh ./

RUN mkdir -p data/action downloads logs sessions \
    && chmod +x start.sh keep_alive.sh

EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"

CMD ["./start.sh"]
