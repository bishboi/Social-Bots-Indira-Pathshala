# Indira Pathshala – Social Bots

An automated exam-prep content pipeline, a Telegram bot, an **admin panel** for configuring everything, and a **daily current-affairs newspaper**. Each content run:

1. **Picks a fresh topic for a space**: a space is a group of one or more exams (Rajasthan SI, JEE Main, NEET, CBSE 10, RBSE 12, REET, SSC CGL…) with its own channels. The space's agent reads what the space already posted and chooses an exam, subject, topic and format that it hasn't covered recently: a real previous-year question, previous-year-style quiz, concept explainer, cheatsheet, formula sheet, memory trick or current affairs. See [Spaces](#spaces).
2. **Researches it on the internet.** The AI (Claude or OpenAI, your choice – see [AI provider](#ai-provider)), using its web search tool, checks the current syllabus and previous-year question trends, picks one high-yield sub-topic it hasn't covered recently, and gathers facts from the sources it cites.
3. **Writes every asset in one structured call:** the Telegram post, 3–5 MCQs, a cheatsheet, a memory trick, the script for a 6–9 slide short and the PDF revision notes. The content comes out in Hindi, English or Hinglish, depending on the exam.
4. **Renders**
   - `card.png`: a 1080×1350 topic card
   - `short.mp4`: a 1080×1920 animated short made with **Manim**, voiced by Gemini TTS (or the free Edge TTS, or silent). It comes in one of several [video styles](#video-styles).
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
  agent.py          a space's agent: reads the space's past topics, plans the next one, runs the pipeline
  bot.py            Telegram bot: commands + daily schedule
```

## AI provider

Every AI step can run on **Anthropic (Claude)** or **OpenAI**: topic research with web search, writing the posts, quizzes, notes and video scripts, the memory-trick review, and the daily newspaper. Choose in the panel (**Settings → AI provider**) or in `.env`:

```bash
LLM_PROVIDER=openai          # or anthropic (default)
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.5         # any OpenAI model with the web_search tool + structured outputs
```

| | Anthropic | OpenAI |
|---|---|---|
| Key | `ANTHROPIC_API_KEY` | `OPENAI_API_KEY` |
| Default model | `claude-opus-5-5` (`CLAUDE_MODEL`) | `gpt-5.5` (`OPENAI_MODEL`) |
| Web research | Claude web search tool, with cited sources | Responses API `web_search` tool, with URL citations |
| Structured output | `output_format` (Pydantic) | `responses.parse` with `text_format` (strict JSON schema) |
| Thinking depth | `RESEARCH_EFFORT` / `GENERATION_EFFORT` → `effort` | the same settings → `reasoning.effort` (left out for models without reasoning) |

You only need the key for the provider you use. Switching takes effect on the next run, with no restart.

### OpenRouter as the AI provider

Set the provider to **OpenRouter** (Settings → AI provider, or `LLM_PROVIDER=openrouter`) to run everything (research, writing, checking) through one OpenRouter key on any model it offers. The default is `anthropic/claude-sonnet-5.5`; change it with `OPENROUTER_TEXT_MODEL` or in the panel. Web research uses OpenRouter's web plugin. The same key also draws the test-series diagrams.

## Database (Supabase)

On a server, keep the app's data in **Supabase** so nothing is lost on restarts or redeploys. That covers the run history (which prevents repeated topics and headlines), the generated quizzes used by `/quiz`, and every admin-panel setting (spaces, channels, custom exams, newspaper schedules, editions and keys).

1. Create a project at [supabase.com](https://supabase.com).
2. Create the tables: open **SQL Editor**, paste [`supabase/schema.sql`](supabase/schema.sql) and click **Run**. Alternatively, set `SUPABASE_DB_URL` and run `python -m pathshala_bot db-init`. The script is safe to run again, and running it again after an update adds any new columns (for example `runs.space_id`, used to keep each space's topic history apart; until it's added, runs still save and the space history falls back to matching by exam).
3. Add the credentials from **Project Settings → API Keys** to `.env`. Use a **Secret key** (`sb_secret_…`) or, on older projects, the legacy **service_role** key (`eyJ…`). Don't use the publishable or anon key.
   ```bash
   SUPABASE_URL=https://<project>.supabase.co
   SUPABASE_SERVICE_ROLE_KEY=<secret or service_role key>
   ```
4. Run the one-step setup. It checks the URL and key (naming the problem if one is wrong), creates the tables when `SUPABASE_DB_URL` is set, verifies them, and seeds the panel settings from `.env`. It's safe to run again at any time.
   ```bash
   python -m pathshala_bot db-setup
   ```
   With Docker: `docker exec pathshala python -m pathshala_bot db-setup`. For a quick status check later, use `db-check`.

| Table | Holds |
|---|---|
| `runs` | Every content and newspaper run: exam/edition, topic, status, where it was posted, and the generated content as JSON |
| `headlines` | Newspaper headlines already used, per edition (so stories aren't repeated within a week) |
| `app_config` | The admin-panel settings, as one row. It includes your API keys. |

All three tables have **Row Level Security turned on with no policies**, so the public `anon` key can't read anything. The server uses the `service_role` key, a server secret that must never go in a browser or app. On the first start with Supabase, an existing local `output/control.json` is imported automatically. Without Supabase settings the app falls back to local SQLite (`DB_PATH`) and `output/control.json`. The panel header shows which one is in use, and any database error appears on the dashboard. Generated files (PDFs, videos, images) stay in `OUTPUT_DIR`.

## Admin panel

Start everything with one command: the web panel, the scheduler and the Telegram bot.

```bash
ADMIN_PASSWORD=choose-a-password python -m pathshala_bot serve
```

Then open http://localhost:8080. You'll get a login page: sign in as `admin` (or your `ADMIN_USER`) with your `ADMIN_PASSWORD`. The panel has six tabs:

| Tab | What you configure |
|---|---|
| **Dashboard** | Upcoming runs, what's running now, recent runs (with their space) and links to their PDFs and videos, and setup warnings (e.g. a missing API key, a space with no channels) |
| **Spaces** | Your spaces: the exams in each (type their names), the connected channels, what to create (shorts/reels, PDF notes, quiz questions, post + card + memory trick), content types, video styles, language, an optional focus note for the agent, how many past topics to avoid, and when it runs (**every X hours** from a start time, or **at set times** each day, on chosen days; or manual only). Each space has **Run now** and **Dry run** (generate without posting) buttons and shows its recent topics. |
| **Channels** | Any number of Telegram channels, YouTube accounts and Instagram accounts. Each has its own credentials, an on/off switch and a **Test** button. |
| **Exams** | The exam catalogue the spaces use: built-in exams from `exams.yaml` plus exams you added by name, with their language, subjects, conducting body, pattern and sources. Type a name to add one, edit what the AI found, or **Refresh from web**. |
| **Newspaper** | Daily current-affairs editions per **state or Central** and **language**, each with its own Telegram channels, and when the newspaper runs |
| **Settings** | Brand, timezone, default language, voice, PDF watermark, newspaper title, how many runs may render at the same time, and API keys. Keys are stored on the server and only ever shown masked. |

Settings are saved to Supabase (`app_config`) when it's configured, otherwise to `output/control.json` (readable only by the server's own user). On the first start they're seeded from your `.env`, so existing setups carry over. Settings saved before spaces existed are converted automatically: each old content schedule becomes a space with the same exams, channels, outputs and timing, and exam weights are dropped. Schedules reload as soon as you save. Sign-in uses a login page and a signed session cookie that lasts 7 days. The cookie is HttpOnly and SameSite=Lax, and it's marked Secure over HTTPS. Repeated wrong passwords are locked out for a while, and changing `ADMIN_PASSWORD` signs everyone out. Scripts can still call the API with `curl -u admin:<password>`. Put the panel behind HTTPS (e.g. a reverse proxy) if you expose it to the internet.

## Spaces

A **space** is a group of one or more exams plus the channels it posts to. For example:

- "Rajasthan Police": Rajasthan SI and REET → the Rajasthan Telegram channel and the Hindi YouTube channel
- "Science": JEE Main and NEET → the science Telegram channel and Instagram

Each space has its own **agent**, and spaces run independently of each other. A space can run on its own schedule, or only when you press **Run now** (or send `/generate <space>` to the bot). Each run of the agent:

1. Reads the topics this space posted before (the last 40 by default, set per space). Runs from before spaces existed count too.
2. Asks the AI to choose the next post: an exam, a subject, one specific topic and a format. The topic must be clearly different from the recent ones. The AI rotates subjects, keeps the space's exams balanced, and prefers topics that appear in several of the space's exams (the post is then written for all of them). An optional **focus** note steers it, e.g. "prefer Rajasthan GK".
3. Checks the choice. If the topic is too close to a recent one, the AI is asked again. If the AI is unavailable, a rule-based pick is used instead: the exam served longest ago, a subject it hasn't covered recently, and an unused seed topic.
4. Researches, writes and renders what the space is set to create, then posts it to the space's connected channels only.

A space never overlaps itself: if a run is still going when the next one is due, that firing is skipped. Different spaces can run at the same time, up to **Runs at the same time** in Settings (2 by default, since rendering videos is CPU-heavy).

From the command line:

```bash
python -m pathshala_bot spaces
```
```bash
python -m pathshala_bot run --space rajasthan-si --dry-run
```

### Adding exams by name

In a space (or the **Exams** tab), type an exam's name, e.g. *Rajasthan Patwari*, *UP Police Constable*, *Bihar Daroga*, *CTET Paper 2*, and press **Add**:

- If it's already in the catalogue (built-in or added before), it's added straight away. Matching ignores case and punctuation, and works on the short name, the full name or the id.
- Otherwise the AI searches the web for the official notification and syllabus and builds the exam's profile: full and short name, conducting body, current pattern (papers, marks, negative marking), the language most aspirants study in, the audience, and the syllabus as subjects with topics, plus the sources it used. This takes about a minute and needs your AI key.

The new exam is saved with the rest of the settings when you click **Save changes**. Check and edit it in the **Exams** tab. The agent reads the pattern and conducting body when it plans and writes. From the command line: `python -m pathshala_bot exam-lookup "Rajasthan Patwari" --save`.

### Previous-year question (`previous_year_question`)

A content type built around **one real question from a past paper**. The research step looks for a question on the topic in the official question paper or answer key, or a reputable archive that names the exam and year, and checks the official answer. The short (style `pyq_reveal`) then:

1. opens with the exam and year on screen and the line *"Dosto, yeh prashan saal 2021 me RPSC SI exam me poocha gaya tha…"* (Hindi: *"दोस्तों, यह प्रश्न साल 2021 में RPSC SI परीक्षा में पूछा गया था…"*; other languages get the same line translated)
2. shows the question with its four options and a countdown
3. reveals the official answer
4. explains the solution and related facts, then the call to action

The same question is the first Telegram quiz poll, tagged `[RPSC SI 2021]`. The PDF notes open with the question, the marked answer and the solution, and the source is listed. If no verifiable past-paper question is found, the post is made as a previous-year-*style* quiz (`pyq_quiz`) instead, never with an invented year. The agent picks this format by itself; to post only this format in a space, select just `previous_year_question` under Content types.

## Test series

The **Test series** tab builds a complete test series for any exam, as a funnel:

1. **Previous-year papers**: the agent searches the web for each year's paper, then opens the pages and PDFs it finds (official answer keys and sites that reproduce the full paper) and copies the questions with their official answers. It does not rely on search snippets. PDFs are read with `pdftotext`, which is included in the Docker image; only public web addresses are fetched. Hints and explanations are written afterwards. A year that can't be found is marked "not found" and skipped; click retry in the panel to try it again. If only part of a paper can be verified, the test says so (for example "62 of 100 questions"), and its time is scaled to match.
2. **Subject-wise tests**: one subject at a time, with that subject's real topic mix.
3. **Full-length mock tests**: built exactly like the real paper, with the same sections, number of questions, marks, negative marking and time.

How the agents work:

- **Pattern (blueprint).** First the agent researches the official pattern and analyses of previous-year papers: the sections, how many questions each section and each topic usually gets, marking, duration and the easy/medium/hard mix. The panel shows this table. Every test keeps these ratios: a 25-question Hindi test splits its questions across Hindi's topics in the same proportion as the real paper.
- **Writer.** Writes the questions in batches, at the real paper's level and style, and never repeats a question already used in the series.
- **Hints and explanations.** Every question gets a **hint** (a nudge towards the concept or first step, never the answer), a short **solution**, and a **detailed explanation**: the concept, the full working, why each wrong option is wrong, and a related fact, shortcut or common trap. Students can open the hint during the online test. After submitting, they see the hint, solution and explanation for every question; explanations open automatically for wrong and skipped answers. The solutions PDF has all three.
- **Checker.** Solves every question again without seeing the key. Questions with a wrong key, two correct options or missing data are thrown out and rewritten. For previous-year papers the official key is kept, and any disagreement is flagged in the panel for you to review.
- **Figures.** Questions that need a diagram (geometry, circuits, ray diagrams, figure series, mirror images, graphs) are drawn as SVG by an **OpenRouter** model. The defaults are `anthropic/claude-sonnet-5.5`, with `google/gemini-3.8-flash` as a backup; you can change both in Settings → Test series. The SVG is cleaned (no scripts or links) before use. Without an OpenRouter key, tests are built without figure questions.

Each test produces a **question paper PDF**, a **solutions PDF** (answer key and step-by-step solutions) and an **online test** at `<Public URL>/t/<series>/<test>`. The online test has a timer, sections, a question palette, mark-for-review, autosave, negative marking, score, rank, percentile and solutions. The answers are never sent to the browser before the student submits. `<Public URL>/t/<series>` lists every released test, stage by stage.

Each stage has its own access level:

| Access | What happens |
|---|---|
| Free | Posted to the series' Telegram channels with both PDFs and the online link |
| Sign-up | Students enter their name and mobile number to attempt it. These are your leads: download them as CSV from the panel. The post includes a sample question as a quiz poll |
| Private | Not posted and not served publicly |

Use **Build next** to build and post the next test in the funnel, or **Build all** to build every missing test without posting (use "post" on each test later). You can also turn on *release the next test automatically* for a drip schedule. A full mock takes several minutes and many AI calls (writer + checker, about one call per 15–20 questions each).

To set it up, add the following in **Settings → Test series**:

- **Public URL**: the HTTPS address of this server, for example `https://tests.example.com`. Students' test pages need no login.
- **OpenRouter API key**: for the diagrams.

If you use Supabase, run `supabase/schema.sql` again: it adds the `test_leads` and `test_attempts` tables.

From the command line:

```bash
python -m pathshala_bot series                      # list series
python -m pathshala_bot series rpsc-si --blueprint  # research the exam pattern again
python -m pathshala_bot series rpsc-si              # build + post the next test
python -m pathshala_bot series rpsc-si --all --dry-run
```

## Daily current-affairs newspaper (Akhbar)

The newspaper is published **per edition**, not per exam. An edition is a **region in one language**:

- **State editions** (any state or UT, e.g. *Rajasthan · हिन्दी*): one paper shared by **every** exam of that state, such as the state PSC, police (SI, constable), subordinate/staff boards (patwari, clerk…), teacher recruitment and the state board. Roughly 55% is that state's news (government decisions, schemes, budget, appointments, culture, geography, recruitment notices), 30% national and 15% international.
- **Central edition** (*Central · हिन्दी / English*): national and international news for central exams: UPSC, SSC, banking, railways, defence, CTET and NTA.

Manage editions in the panel's **Newspaper** tab. Each edition has a region, a language, its own Telegram channels and an on/off switch. The defaults are Rajasthan · Hindi, Central · Hindi and Central · English (off). A newspaper schedule (lower down the same tab, off by default) produces one paper per selected edition. Each run:

1. **Researches the day's news.** Claude searches the last 24–48 hours and **decides what matters** for that edition's readers. It prefers official sources (the state government and DIPR portals, the state PSC, PIB, RBI) and the leading papers of the region, and skips stories that edition used in the last week.
2. **Writes the edition:** 8–12 articles, each with a headline, dateline, summary, body, key facts, an **exam angle** ("परीक्षा दृष्टि"), a related static GK fact and its sources. It adds one-liners, "did you know" facts and a 6–10 question quiz.
3. **Checks it:** stories with no date, more than 3 days old, or with no source are dropped. The rest are ordered by importance.
4. **Lays it out like a real daily newspaper (A4 PDF):**
   - **Masthead:** **इंदिरा पाठशाला समसामयिकी पत्रिका** (English editions: *Indira Pathshala Samsamayiki Patrika*), set as a red "इंदिरा पाठशाला" overline with double rules and the main title in a display face: Rozha One for Hindi, blackletter for English, like The Times. You can change the titles in the panel (Settings → Daily newspaper) or with `AKHBAR_TITLE_HI` / `AKHBAR_TITLE_EN`.
   - **Type:** Hindi editions are set in **Martel** (a Devanagari newspaper serif) with **Mukta** for labels. English editions use **Source Serif 4** text and **Playfair Display** headlines. All fonts are bundled, open-licensed, and listed in `assets/fonts/README.md`. "Ears" on either side: the logo and motto on the left, the Telegram handle on the right. Below it a date strip with volume/issue, date, a red edition flag ("राजस्थान संस्करण" / "राष्ट्रीय संस्करण" / "National edition") and "मूल्य: निःशुल्क".
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

## Languages

Every exam, space and newspaper edition can use any of the 22 languages of the Eighth Schedule, plus English and Hinglish. Each language gets its own script fonts (Noto for most scripts; Nastaliq for Urdu reading text), localized dates, and right-to-left layout for Urdu, Kashmiri and Sindhi.

| Code | Language | Voice-over |
|---|---|---|
| `hi` `en` `bn` `gu` `kn` `ml` `mr` `ne` `ta` `te` `pa` `or` `as` `mai` `ks` `mni` `sat` | Hindi, English, Bengali, Gujarati, Kannada, Malayalam, Marathi, Nepali, Tamil, Telugu, Punjabi, Odia, Assamese, Maithili, Kashmiri, Manipuri, Santali | Gemini TTS Lite |
| `sd` | Sindhi | Gemini TTS (Flash) |
| `ur` | Urdu | Edge TTS (`ur-IN-GulNeural`) |
| `sa` `doi` `brx` `kok` | Sanskrit, Dogri, Bodo, Konkani | Edge voice of a related language (Hindi or Marathi), as an approximation |
| `hinglish` | Hinglish | Gemini TTS Lite |

If Gemini fails (no key, quota), the voice-over falls back to Edge TTS automatically.

**Labels** ("Key Points", "Quiz", section names, the newspaper title, and so on) exist in English and Hindi. For any other language they're translated once by the AI and cached in `output/cache/i18n/<code>.json`. To correct a translation, copy that file to `assets/i18n/<code>.json` and edit it. That file always takes priority.

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
| `/spaces` | admin | Lists the spaces and how many channels each is connected to |
| `/preview [space] [exam] [type] [style]` | admin | Runs a space's agent and sends everything to *your* chat only |
| `/generate [space] [exam] [type] [style]` | admin | Runs a space's agent and posts to the space's channels |
| `/history` | admin | Lists the last runs |
| `/akhbar [edition]` | admin | Builds today's newspaper for an edition (e.g. `rajasthan-hi`, `central-en`) and sends it to your chat |

Admins are the numeric user IDs in `TELEGRAM_ADMIN_IDS`. `[space]` is a space's ID or name. With only an exam, the first space containing that exam runs, limited to that exam. With nothing, the only space runs (if you have several, the bot asks which).

### Hosting options

- **Docker / VPS:** run `docker build -t pathshala .`, then `docker run -d --name pathshala --restart unless-stopped --env-file .env -p 8080:8080 -v "$PWD/data:/data" pathshala`. This runs the admin panel, scheduler and bot, and keeps settings and history in `./data` (or in Supabase). Docker reports the container as *healthy* once the panel is up.
- **GitHub Actions:** `.github/workflows/post.yml` posts at 08:00 and 18:00 IST with no server, and the newspaper at 06:45 IST for the editions in the `AKHBAR_EDITIONS` repository variable (e.g. `Rajasthan:hi,central:hi,central:en`). This option has no admin panel: it uses the `.env` values. Add the `.env` values as repository secrets. You can also run it manually from the Actions tab and pick the exam, format and platforms.

### Troubleshooting Docker

The container doesn't stop when something is misconfigured. The panel still starts and shows the problem, so first open http://localhost:8080 and look at the dashboard. Then check the logs:

```bash
docker ps -a --filter name=pathshala
```

```bash
docker logs pathshala
```

| Symptom | Fix |
|---|---|
| `The container name "/pathshala" is already in use` | Remove the old container first: `docker rm -f pathshala`, then `docker run …` again |
| Panel says *ADMIN_PASSWORD is not set* | Add `ADMIN_PASSWORD=…` to `.env`, then recreate the container |
| *Invalid API key* | Use a **Secret key** (`sb_secret_…`) or the legacy **service_role** key from the **same project** as `SUPABASE_URL`, not the publishable or anon key. Then run `db-check`, which names the key type and any mismatch |
| Dashboard shows *Cannot reach Supabase* or *does not exist* | Check `SUPABASE_URL`, run `supabase/schema.sql` in the SQL editor, use the **service_role** key, then `docker exec pathshala python -m pathshala_bot db-check` |
| Changed `.env` but nothing changed | A container reads `.env` only when it's created: `docker rm -f pathshala`, then `docker run …` again |
| Pulled new code | `docker build -t pathshala .`, then `docker rm -f pathshala` and `docker run …` again |

Comments after values (`KEY=value  # note`) and quoted values (`KEY="value"`) in `.env` are handled for both Docker and local runs. `.env` is excluded from the image by `.dockerignore`.

## Credentials

| Service | How |
|---|---|
| AI: Claude | `ANTHROPIC_API_KEY` from console.anthropic.com. The default model is `claude-opus-5-5`, with server-side refusal fallback turned on. |
| AI: OpenAI | `OPENAI_API_KEY` from platform.openai.com/api-keys, plus `LLM_PROVIDER=openai`. Set `OPENAI_MODEL` to a model your account has that supports web search. |
| Telegram | Create a bot with @BotFather → `TELEGRAM_BOT_TOKEN`. Add the bot as an **admin** of your channel → `TELEGRAM_CHANNEL_ID=@yourchannel`. An exam can post to its own channel through `telegram_chat` in `exams.yaml`. |
| YouTube | In Google Cloud, enable YouTube Data API v3 and create an OAuth client (Desktop app). Then run `python scripts/youtube_auth.py` to get `YOUTUBE_REFRESH_TOKEN`. Until Google audits your API project, uploads are forced to *private*. |
| Instagram | You need an Instagram **professional** account linked to a Facebook Page, and a Meta app with `instagram_basic` and `instagram_content_publish`. Put a long-lived token in `INSTAGRAM_ACCESS_TOKEN` and the IG user ID in `INSTAGRAM_USER_ID`. |
| Voice | `GEMINI_API_KEY` from aistudio.google.com/apikey uses Gemini TTS (`gemini-3.8-flash-lite-tts`). Without it, the free Edge TTS is used. ElevenLabs still works if you set `TTS_PROVIDER=elevenlabs`. |

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

Add an entry to `pathshala_bot/exams.yaml` with an `id`, a `language` (a code from the [Languages](#languages) table, e.g. `hi`, `ta`, `bn`) and its `subjects` with seed topics, or add a custom exam in the panel's **Exams** tab. Then add it to a space. You don't need to change any code.

## Tests

```bash
pytest -q
```

The tests run offline: rendering, Telegram message sequence, platform limits and a dry pipeline run.
