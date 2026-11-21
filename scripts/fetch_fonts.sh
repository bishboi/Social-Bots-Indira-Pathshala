#!/usr/bin/env bash
# Downloads the fonts used by slides, cards and PDFs into assets/fonts/.
# Noto Sans Devanagari is required for Hindi; Poppins is used for English text.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)/assets/fonts"
mkdir -p "$DIR"
# An old User-Agent makes Google Fonts serve plain, unsubsetted TTF files.
UA="Mozilla/4.0"
fetch() { # family file-prefix weight weight-name
  local url
  url=$(curl -fsS -A "$UA" "https://fonts.googleapis.com/css2?family=$1:wght@$3" | grep -o 'https://[^)]*\.ttf' | head -1)
  curl -fsS -o "$DIR/$2-$4.ttf" "$url"
  echo "  $2-$4.ttf"
}
echo "Fetching fonts into $DIR"
fetch "Noto+Sans+Devanagari" NotoSansDevanagari 400 Regular
fetch "Noto+Sans+Devanagari" NotoSansDevanagari 700 Bold
fetch "Poppins" Poppins 400 Regular
fetch "Poppins" Poppins 700 Bold
# Serif face for the daily current-affairs newspaper (Akhbar)
fetch "Noto+Serif+Devanagari" NotoSerifDevanagari 400 Regular
fetch "Noto+Serif+Devanagari" NotoSerifDevanagari 700 Bold
