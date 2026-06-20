FROM python:3.12-slim

WORKDIR /app

# Системные зависимости для lxml и tgcrypto
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libxml2-dev libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/

# data/ монтируется как volume (SQLite + логи + tg session)
RUN mkdir -p data/logs

CMD ["python", "-m", "src.scheduler"]
