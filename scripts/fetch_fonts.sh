#!/usr/bin/env bash
# Downloads the fonts used by slides, cards and PDFs into assets/fonts/.
# Mukta (Hindi / Hinglish UI and video text), Martel + Rozha One (Hindi newspaper), Poppins (English UI),
# Source Serif 4 + Playfair Display + UnifrakturMaguntia (English newspaper). Noto fonts are fallbacks.
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
# Newspaper mastheads: Rozha One (Devanagari display) and UnifrakturMaguntia (blackletter, English)
fetch "Rozha+One" RozhaOne 400 Regular
fetch "UnifrakturMaguntia" UnifrakturMaguntia 400 Regular
# Newspaper text faces: Martel (Devanagari serif), Source Serif 4 + Playfair Display (English editions)
fetch "Martel" Martel 400 Regular
fetch "Martel" Martel 700 Bold
fetch "Martel" Martel 800 ExtraBold
fetch "Source+Serif+4" SourceSerif4 400 Regular
fetch "Source+Serif+4" SourceSerif4 700 Bold
fetch "Playfair+Display" PlayfairDisplay 700 Bold
fetch "Playfair+Display" PlayfairDisplay 900 Black
# UI / notes / video text: Mukta (Devanagari + Latin sans)
fetch "Mukta" Mukta 400 Regular
fetch "Mukta" Mukta 500 Medium
fetch "Mukta" Mukta 600 SemiBold
fetch "Mukta" Mukta 700 Bold
fetch "Mukta" Mukta 800 ExtraBold

# ---- Other Indian scripts (Noto, OFL) ------------------------------------------------
# fetch_opt: like fetch, but skips weights a family doesn't have
fetch_opt() { fetch "$@" 2>/dev/null || echo "  (skipped $2-$4)"; }
for pair in "Bengali" "Gurmukhi" "Gujarati" "Oriya" "Tamil" "Telugu" "Kannada" "Malayalam"; do
  fetch "Noto+Sans+$pair" "NotoSans$pair" 400 Regular
  fetch "Noto+Sans+$pair" "NotoSans$pair" 700 Bold
  fetch_opt "Noto+Sans+$pair" "NotoSans$pair" 800 ExtraBold
  fetch "Noto+Serif+$pair" "NotoSerif$pair" 400 Regular
  fetch "Noto+Serif+$pair" "NotoSerif$pair" 700 Bold
done
fetch "Noto+Nastaliq+Urdu" NotoNastaliqUrdu 400 Regular
fetch "Noto+Nastaliq+Urdu" NotoNastaliqUrdu 700 Bold
fetch "Noto+Naskh+Arabic" NotoNaskhArabic 400 Regular
fetch "Noto+Naskh+Arabic" NotoNaskhArabic 700 Bold
fetch "Noto+Sans+Ol+Chiki" NotoSansOlChiki 400 Regular
fetch "Noto+Sans+Ol+Chiki" NotoSansOlChiki 700 Bold
fetch "Noto+Sans+Meetei+Mayek" NotoSansMeeteiMayek 400 Regular
fetch "Noto+Sans+Meetei+Mayek" NotoSansMeeteiMayek 700 Bold
