# Indira Pathshala – Social Bots

An automated exam-prep content pipeline, a Telegram bot, an **admin panel** for configuring everything, and a **daily current-affairs newspaper**. Each content run:

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

## Admin panel

Start everything with one command: the web panel, the scheduler and the Telegram bot.

```bash
ADMIN_PASSWORD=choose-a-password python -m pathshala_bot serve
```

Then open http://localhost:8080 and log in as `admin`. The panel has six tabs:

| Tab | What you configure |
|---|---|
| **Dashboard** | Upcoming runs, what's running now, recent runs with links to their PDFs and videos, and setup warnings (e.g. a missing API key) |
| **Channels** | Any number of Telegram channels, YouTube accounts and Instagram accounts. Each has its own credentials, an on/off switch and a **Test** button. |
| **Exams** | Turn each target exam on or off. Set its **language** (Hindi, English or Hinglish), how often it's picked, and which channels it posts to. You can also add custom exams with their own subjects and topics. |
| **Schedules** | **Every X hours** (from a start time) or **at set times** each day, on chosen days of the week. Also: which exams (or, for the newspaper, which editions), which content types, which **outputs** (shorts/reels, PDF notes, quiz questions, post + card + memory trick), which video styles, which channels, and a language override. Each schedule has **Run now** and **Dry run** (generate without posting) buttons. |
| **Newspaper** | Daily current-affairs editions per **state or Central** and **language**, each with its own Telegram channels |
| **Settings** | Brand, timezone, default language, voice, PDF watermark, newspaper title, and API keys. Keys are stored on the server and only ever shown masked. |

Settings are saved to `output/control.json` (readable only by the server's own user). On the first start that file is seeded from your `.env`, so existing setups carry over. Schedules reload as soon as you save. The panel uses HTTP Basic auth (`ADMIN_USER` / `ADMIN_PASSWORD`), so put it behind HTTPS (e.g. a reverse proxy) if you expose it to the internet.

## Daily current-affairs newspaper (Akhbar)

The newspaper is published **per edition**, not per exam. An edition is a **region in one language**:

- **State editions** (any state or UT, e.g. *Rajasthan · हिन्दी*): one paper shared by **every** exam of that state, such as the state PSC, police (SI, constable), subordinate/staff boards (patwari, clerk…), teacher recruitment and the state board. Roughly 55% is that state's news (government decisions, schemes, budget, appointments, culture, geography, recruitment notices), 30% national and 15% international.
- **Central edition** (*Central · हिन्दी / English*): national and international news for central exams: UPSC, SSC, banking, railways, defence, CTET and NTA.

Manage editions in the panel's **Newspaper** tab. Each edition has a region, a language, its own Telegram channels and an on/off switch. The defaults are Rajasthan · Hindi, Central · Hindi and Central · English (off). A "Daily newspaper" schedule (in the Schedules tab, off by default) produces one paper per selected edition. Each run:

1. **Researches the day's news.** Claude searches the last 24–48 hours and **decides what matters** for that edition's readers. It prefers official sources (the state government and DIPR portals, the state PSC, PIB, RBI) and the leading papers of the region, and skips stories that edition used in the last week.
2. **Writes the edition:** 8–12 articles, each with a headline, dateline, summary, body, key facts, an **exam angle** ("परीक्षा दृष्टि"), a related static GK fact and its sources. It adds one-liners, "did you know" facts and a 6–10 question quiz.
3. **Checks it:** stories with no date, more than 3 days old, or with no source are dropped. The rest are ordered by importance.
4. **Lays it out like a real daily newspaper (A4 PDF):**
   - **Masthead:** the title in a display face (Rozha One for Hindi, blackletter for English, like The Times). "Ears" on either side: the logo and motto on the left, the Telegram handle on the right. Below it a date strip with volume/issue, date, a red edition flag ("राजस्थान संस्करण" / "राष्ट्रीय संस्करण" / "National edition") and "मूल्य: निःशुल्क".
   - **Front page:** the lead story gets a large headline, a subheading, a "पाठशाला डेस्क" byline with dateline, a **real photo** with its credit, and two-column text. A sidebar holds "इस अंक में" (section index with real page numbers), "संक्षेप में", "क्या आप जानते हैं?" and the lead's exam angle. The next three stories run in three columns below.
   - **Inside pages:** a running header (title · edition · date · page), and red section banners (राज्य, देश, परीक्षा व भर्ती, अर्थव्यवस्था, विदेश, विज्ञान व पर्यावरण, पुरस्कार व नियुक्तियाँ, खेल) over three ruled columns. Each story has a key-facts box, an exam angle, static GK and its sources.
   - **Last page:** the quiz in columns, more one-liners, and the **answers printed upside down** like a newspaper puzzle. Newsprint-tinted pages, a faint logo watermark and a © footer throughout.
   - **Photos** are the lead images of the Wikipedia articles the AI names (the place, monument or institution in the story). They're used only if they're on Wikimedia Commons under a free licence, with the credit printed under the photo. Stories without a suitable photo simply run without one.
5. **Posts it to the edition's Telegram channels:** the PDF with today's top headlines as the caption, and optionally the quiz as polls.

You can also run it by hand:

```bash
python -m pathshala_bot akhbar --region Rajasthan --language hi --publish
```

```bash
python -m pathshala_bot akhbar --region central --language en --chat @mychannel
```

To use an edition saved in the panel, pass `--edition rajasthan-hi`. Admins can send `/akhbar rajasthan-hi` or `/akhbar central-en` to the bot to get a preview in their own chat.

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

Start the admin panel, scheduler and bot together (recommended):

```bash
python -m pathshala_bot serve
```

To run the bot alone with the older `.env` schedule (`POST_TIMES`), use `python -m pathshala_bot bot`.

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
| `/akhbar [edition]` | admin | Builds today's newspaper for an edition (e.g. `rajasthan-hi`, `central-en`) and sends it to your chat |

Admins are the numeric user IDs in `TELEGRAM_ADMIN_IDS`.

### Hosting options

- **Docker / VPS:** run `docker build -t pathshala .`, then `docker run --env-file .env -p 8080:8080 -v $PWD/data:/data pathshala`. This runs the admin panel, scheduler and bot; settings and history persist in `./data`.
- **GitHub Actions:** `.github/workflows/post.yml` posts at 08:00 and 18:00 IST with no server, and the newspaper at 06:45 IST for the editions in the `AKHBAR_EDITIONS` repository variable (e.g. `Rajasthan:hi,central:hi,central:en`). This option has no admin panel: it uses the `.env` values. Add the `.env` values as repository secrets. You can also run it manually from the Actions tab and pick the exam, format and platforms.

## Credentials

| Service | How |
|---|---|
| Claude | `ANTHROPIC_API_KEY` from console.anthropic.com. The default model is `claude-opus-5-5`, with server-side refusal fallback turned on. |
| Telegram | Create a bot with @BotFather → `TELEGRAM_BOT_TOKEN`. Add the bot as an **admin** of your channel → `TELEGRAM_CHANNEL_ID=@yourchannel`. An exam can post to its own channel through `telegram_chat` in `exams.yaml`. |
| YouTube | In Google Cloud, enable YouTube Data API v3 and create an OAuth client (Desktop app). Then run `python scripts/youtube_auth.py` to get `YOUTUBE_REFRESH_TOKEN`. Until Google audits your API project, uploads are forced to *private*. |
| Instagram | You need an Instagram **professional** account linked to a Facebook Page, and a Meta app with `instagram_basic` and `instagram_content_publish`. Put a long-lived token in `INSTAGRAM_ACCESS_TOKEN` and the IG user ID in `INSTAGRAM_USER_ID`. |
| Voice | `ELEVENLABS_API_KEY` gives the best Hindi voice. Without it, the free Edge TTS (`hi-IN-SwaraNeural`) is used. |

Any platform without credentials is skipped and noted in `manifest.json`, so you can start with Telegram only.

## Memory tricks (याद रखने की ट्रिक)

A memory trick is added only when a topic has a list, sequence, dates or easily confused facts to memorise. Each trick has three parts:

- the trick itself: a short sentence, story, rhyme or real word that makes sense when said aloud
- a **breakdown** saying what each cue word stands for
- a one-line tip on when to use it in the exam

Example: "मछली पूरब से संग चली, बड़ी होकर संग-बड़ी बनी, सिरोही होते अजमेर पहुँची". Here मछली stands for मत्स्य संघ (मत्स्य means fish), पूरब for पूर्व राजस्थान, and so on through all 7 stages.

Quality checks:

1. **Writing rules:** the AI is given rules plus good and bad examples. Cues must be real words that hint at their fact, never bare letters.
2. **Automatic check:** rejects letter strings like "म-पू-सं-वृ", a missing breakdown, and cues that don't appear in the trick.
3. **Review call:** a second Claude call tests the trick against the facts. It keeps it (score 8/10 or more), rewrites it (choosing the best of three alternatives), or drops it. A trick that still fails the checks after the review is left out entirely. To skip the extra call, set `MNEMONIC_REVIEW=false`.

The trick appears in the PDF with its breakdown table, and as its own Telegram message.

## Adding an exam

Add an entry to `pathshala_bot/exams.yaml` with an `id`, a `language` (`hi`/`en`/`hinglish`), a `weight` and its `subjects` with seed topics. You don't need to change any code.

## Tests

```bash
pytest -q
```

The tests run offline: rendering, Telegram message sequence, platform limits and a dry pipeline run.
