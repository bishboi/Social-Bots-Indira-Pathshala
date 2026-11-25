FROM python:3.12-slim

# ffmpeg: video; Pango/Cairo: WeasyPrint PDFs + Manim text (HarfBuzz shaping for Hindi);
# LaTeX + dvisvgm: Manim formula rendering (JEE/NEET formula reels)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fontconfig build-essential pkg-config poppler-utils \
        libcairo2-dev libpango1.0-dev libharfbuzz0b \
        texlive-latex-base texlive-latex-recommended texlive-fonts-recommended dvisvgm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV OUTPUT_DIR=/data/output DB_PATH=/data/output/history.sqlite3
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"
# Admin UI + scheduler + Telegram bot. Requires ADMIN_PASSWORD; put it behind HTTPS if exposed publicly.
CMD ["python", "-m", "pathshala_bot", "serve", "--host", "0.0.0.0", "--port", "8080"]
