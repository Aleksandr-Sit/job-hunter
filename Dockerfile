FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUTF8=1

# Системные зависимости для lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libxml2-dev libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/

# data/ монтируется как volume (SQLite + логи)
RUN mkdir -p data/logs

CMD ["python", "-m", "src.scheduler"]
