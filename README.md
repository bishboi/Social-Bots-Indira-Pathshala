# Indira Pathshala – Social Bots

An automated exam-prep content pipeline plus a Telegram bot. Each run:

1. **Picks a target exam**: Rajasthan SI, JEE Main, NEET, CBSE 10, RBSE 12, REET, SSC CGL… (weighted random, or the one you choose). It then picks a subject, a seed topic and a format: previous-year-style quiz, concept explainer, cheatsheet, formula sheet, memory trick or current affairs.
2. **Researches it on the internet.** Claude, using its web search tool, checks the current syllabus and previous-year question trends, picks one high-yield sub-topic it hasn't covered recently, and gathers facts from the sources it cites.
3. **Writes every asset in one structured call:** the Telegram post, 3–5 MCQs, a cheatsheet, a memory trick, the script for a 6–9 slide short and the PDF revision notes. The content comes out in Hindi, English or Hinglish, depending on the exam.
4. **Renders**
   - `card.png`: a 1080×1350 topic card
   - `short.mp4`: a 1080×1920 animated short made with **Manim**, voiced by ElevenLabs (or Edge TTS, or silent). It comes in one of several [video styles](#video-styles).
   - `map.png`: an accurate static map whenever the topic has a map scene (it's also embedded in the PDF)
   - `notes.pdf`: A4 notes with a cheatsheet table, practice questions, an answer key and sources. Every page carries your logo in the header, a faint logo-and-name watermark, and a © copyright footer; a full copyright notice closes the notes. The logo is `assets/brand/logo.png`; replace it, or set `BRAND_LOGO`, `COPYRIGHT_OWNER` and `PDF_WATERMARK=false` in `.env`.
5. **Publishes**
   - **Telegram:** the card with the post, native **quiz polls**, the PDF and the video
   - **YouTube Shorts:** resumable upload through the Data API
   - **Instagram Reels:** Graph API, uploading the file directly

Everything for a run lands in `output/<timestamp>_<exam>_<type>/`, along with `content.json`, `research.md` and `manifest.json`.

```
pathshala_bot/
  exams.yaml        target exams, language, subjects → seed topics (edit this to add exams)
  llm.py            Claude: web research → structured ContentPackage
  models.py         the content schema (also the instructions to the model)
  pipeline.py       pick → research → generate → render → publish
  render/           manim_scene.py (animated shorts), video.py (voice timing, fallback), images.py, tts.py, pdf.py
  geo.py            accurate maps: official boundaries, rivers, gazetteer lookups
  resources.py      Wikipedia / Wikimedia Commons: coordinates, freely-licensed photos + credits
  publish/          telegram.py, youtube.py, instagram.py
  bot.py            Telegram bot: commands + daily schedule
```

## Video styles

Each run picks a style that suits the topic, or you can force one with `--video-style`. The AI writes a scene-by-scene plan, and Manim animates it. Each scene lasts exactly as long as its voice-over.

| Style | Best for | What you see |
|---|---|---|
| `quiz_countdown` | PYQ quizzes | Question, then options sliding in, a countdown ring, and a reveal that highlights the correct option |
| `map_story` | Geography and history | India map zooming into a state, with districts filled, rivers drawn and places pinned |
| `timeline` | History | An animated vertical timeline of dated events |
| `formula_breakdown` | JEE/NEET Physics, Chemistry and Maths | A LaTeX formula written on screen, the meaning of each symbol, and a comparison table |
| `top_facts` | Cheatsheets and current affairs | Big numbers and keywords punched in one at a time |
| `concept_explainer` | Anything else | Bullets, comparison tables and real photos |
| `slideshow` | A fast fallback | Static slides with a slow zoom (no Manim needed) |

Scene types you can mix within a video: `title`, `bullets`, `fact`, `quiz`, `reveal`, `timeline`, `map`, `formula`, `comparison`, `image`, `cta`. If Manim fails, the run falls back to `slideshow` automatically, so a post never goes out without its video.

To preview a style from the bundled samples:

```bash
python -m pathshala_bot run --from-json tests/fixtures/rajasthan_geo_map_sample.json --exam rajasthan-si
python -m pathshala_bot run --from-json tests/fixtures/jee_formula_sample.json
python -m pathshala_bot run --from-json tests/fixtures/rajasthan_si_sample.json --video-style slideshow
```

### Accurate maps and resources

The AI only **names** states, districts, rivers and places. Every boundary and coordinate comes from a verified dataset, and anything that can't be resolved is left off the map rather than guessed.

| What | Source | Licence |
|---|---|---|
| State/UT boundaries, drawn to India's official boundary (including all of J&K and Ladakh) | [DataMeet India maps](https://github.com/datameet/maps) | CC BY 4.0 |
| District boundaries (**Census 2011**: 33 districts for Rajasthan) | DataMeet | CC BY 4.0 |
| Major rivers | Natural Earth 1:10m | Public domain |
| Cities, parks, forts, lakes, dams, peaks | [GeoNames](https://www.geonames.org) gazetteer | CC BY 4.0 |
| Fallback coordinates (sites not in GeoNames, e.g. Kalibangan) | The Wikipedia article's own coordinates | — |
| Photos | Lead image of the named Wikipedia article, used **only** if it's hosted on Wikimedia Commons under CC0, public domain, CC BY or CC BY-SA. Fair-use images are refused. | Credit added to the video and descriptions |

Each place is checked against the focus state's boundary, so a wrong match (say, a village in another state with the same name) is dropped. The map attribution appears on screen and in the YouTube description.

The data lives in `assets/geo/` (about 3 MB). To rebuild it, run `scripts/build_geodata.py`; the script's docstring lists the source downloads.

> Rajasthan now has more districts than in 2011. Districts created after 2011 don't exist in the boundary layer, so they're skipped (with a log line). Their headquarters can still be pinned as places.

## Setup

```bash
pip install -r requirements.txt
```

You also need some system packages: `ffmpeg`; Cairo and Pango (for PDFs and Manim text); and LaTeX with `dvisvgm` (for formula reels):

```bash
sudo apt install ffmpeg libcairo2-dev libpango1.0-dev pkg-config texlive-latex-base texlive-latex-recommended texlive-fonts-recommended dvisvgm
```

The Dockerfile installs all of these.

```bash
cp .env.example .env
```

Then fill in the keys (see below). The fonts (Noto Sans Devanagari for Hindi, Poppins) ship in `assets/fonts/`.

Try it with no API keys at all. This renders the bundled Hindi sample package without calling Claude and without posting anything:

```bash
python -m pathshala_bot run --from-json tests/fixtures/rajasthan_si_sample.json --exam rajasthan-si
```

## Usage

A dry run (research, generate and render, but don't post):

```bash
python -m pathshala_bot run --exam rajasthan-si --type pyq_quiz
```

A random exam and format, posted everywhere:

```bash
python -m pathshala_bot run --publish all
```

Post to Telegram only:

```bash
python -m pathshala_bot run --exam jee-main --publish telegram
```

Re-post an earlier run:

```bash
python -m pathshala_bot run --from-json output/<run>/content.json --publish telegram
```

Start the long-running bot (it also auto-posts daily at `POST_TIMES`):

```bash
python -m pathshala_bot bot
```

List the configured exams:

```bash
python -m pathshala_bot exams
```

### Bot commands

| Command | Who | What |
|---|---|---|
| `/exams`, `/types`, `/styles` | anyone | List the exams, formats and video styles |
| `/quiz [exam]` | anyone | Sends a random quiz poll from past content (no AI cost) |
| `/preview [exam] [type] [style]` | admin | Generates and sends everything to *your* chat only |
| `/generate [exam] [type] [style]` | admin | Generates and publishes to the channel, YouTube and Instagram |
| `/history` | admin | Lists the last runs |

Admins are the numeric user IDs in `TELEGRAM_ADMIN_IDS`.

### Hosting options

- **Docker / VPS:** run `docker build -t pathshala .`, then `docker run --env-file .env -v $PWD/data:/data pathshala`. This runs the bot and its daily schedule.
- **GitHub Actions:** `.github/workflows/post.yml` posts at 08:00 and 18:00 IST with no server. Add the `.env` values as repository secrets. You can also run it manually from the Actions tab and pick the exam, format and platforms.

## Credentials

| Service | How |
|---|---|
| Claude | `ANTHROPIC_API_KEY` from console.anthropic.com. The default model is `claude-opus-5-5`, with server-side refusal fallback turned on. |
| Telegram | Create a bot with @BotFather → `TELEGRAM_BOT_TOKEN`. Add the bot as an **admin** of your channel → `TELEGRAM_CHANNEL_ID=@yourchannel`. An exam can post to its own channel through `telegram_chat` in `exams.yaml`. |
| YouTube | In Google Cloud, enable YouTube Data API v3 and create an OAuth client (Desktop app). Then run `python scripts/youtube_auth.py` to get `YOUTUBE_REFRESH_TOKEN`. Until Google audits your API project, uploads are forced to *private*. |
| Instagram | You need an Instagram **professional** account linked to a Facebook Page, and a Meta app with `instagram_basic` and `instagram_content_publish`. Put a long-lived token in `INSTAGRAM_ACCESS_TOKEN` and the IG user ID in `INSTAGRAM_USER_ID`. |
| Voice | `ELEVENLABS_API_KEY` gives the best Hindi voice. Without it, the free Edge TTS (`hi-IN-SwaraNeural`) is used. |

Any platform without credentials is skipped and noted in `manifest.json`, so you can start with Telegram only.

## Adding an exam

Add an entry to `pathshala_bot/exams.yaml` with an `id`, a `language` (`hi`/`en`/`hinglish`), a `weight` and its `subjects` with seed topics. You don't need to change any code.

## Tests

```bash
pytest -q
```

The tests run offline: rendering, Telegram message sequence, platform limits and a dry pipeline run.
