FROM python:3.12-slim

# ffmpeg: video; Pango/Cairo: WeasyPrint PDFs + Manim text (HarfBuzz shaping for Hindi);
# LaTeX + dvisvgm: Manim formula rendering (JEE/NEET formula reels)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fontconfig build-essential pkg-config \
        libcairo2-dev libpango1.0-dev libharfbuzz0b \
        texlive-latex-base texlive-latex-recommended texlive-fonts-recommended dvisvgm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV OUTPUT_DIR=/data/output DB_PATH=/data/output/history.sqlite3
VOLUME ["/data"]
CMD ["python", "-m", "pathshala_bot", "bot"]
