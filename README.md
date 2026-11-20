# Indira Pathshala – Social Bots

An automated exam-prep content pipeline plus a Telegram bot. Each run:

1. **Picks a target exam**: Rajasthan SI, JEE Main, NEET, CBSE 10, RBSE 12, REET, SSC CGL… (weighted random, or the one you choose). It then picks a subject, a seed topic and a format: previous-year-style quiz, concept explainer, cheatsheet, formula sheet, memory trick or current affairs.
2. **Researches it on the internet.** Claude, using its web search tool, checks the current syllabus and previous-year question trends, picks one high-yield sub-topic it hasn't covered recently, and gathers facts from the sources it cites.
3. **Writes every asset in one structured call:** the Telegram post, 3–5 MCQs, a cheatsheet, a memory trick, the script for a 6–9 slide short and the PDF revision notes. The content comes out in Hindi, English or Hinglish, depending on the exam.
4. **Renders**
   - `card.png`: a 1080×1350 topic card
   - `slides/*.png` and `short.mp4`: a 1080×1920 vertical short with voice-over (ElevenLabs → Edge TTS → silent)
   - `notes.pdf`: A4 notes with a cheatsheet table, practice questions, an answer key and sources
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
  render/           images.py (slides/card), video.py (ffmpeg), tts.py, pdf.py (WeasyPrint)
  publish/          telegram.py, youtube.py, instagram.py
  bot.py            Telegram bot: commands + daily schedule
```

## Setup

```bash
pip install -r requirements.txt
```

You also need `ffmpeg` and Pango (for the PDF output) on the system: `apt install ffmpeg libpango-1.0-0 libpangoft2-1.0-0`.

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
| `/exams`, `/types` | anyone | List the exams and formats |
| `/quiz [exam]` | anyone | Sends a random quiz poll from past content (no AI cost) |
| `/preview [exam] [type]` | admin | Generates and sends everything to *your* chat only |
| `/generate [exam] [type]` | admin | Generates and publishes to the channel, YouTube and Instagram |
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
