FROM python:3.12-slim

# ffmpeg for video, Pango for WeasyPrint PDFs (HarfBuzz shaping for Hindi)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b fontconfig \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV OUTPUT_DIR=/data/output DB_PATH=/data/output/history.sqlite3
VOLUME ["/data"]
CMD ["python", "-m", "pathshala_bot", "bot"]
