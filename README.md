# Surge

Surge watches live streams (Twitch, Kick, YouTube) or past broadcasts, cuts the moments where chat and the
streamer blow up, turns each one into a captioned 9:16 short, writes the hook, title, description and hashtags,
and schedules it across many YouTube channels and Instagram accounts.

Everything runs on your PC. Posting goes through the official YouTube and Instagram APIs with your own
developer apps.

## Install

1. Download **`Surge-Setup-<version>.exe`** from the [latest release](https://github.com/Satoruatwork/surge-clipper/releases/latest). Windows 10 or 11, 64-bit.
2. Run it. It installs for your user only (no admin prompt) and adds a Start menu entry and an optional desktop shortcut.
3. Open **Surge**. The first start downloads the meme sound pack (about a minute). The first captioned clip downloads
   the Whisper speech models (about 1.7 GB, once).

Good to know:

- **SmartScreen** may warn about an unknown publisher: the installer is not code-signed. Choose *More info*, then
  *Run anyway*. To check the download, compare its SHA-256 with `SHA256SUMS.txt` on the release page.
- Surge's window uses the **Microsoft Edge WebView2 Runtime**. Windows 11 and current Windows 10 have it. If it is
  missing, the installer points you to the download.
- Rendering picks the fastest encoder it finds (NVIDIA NVENC, Intel QuickSync, AMD AMF) and falls back to the CPU.
- Your data lives in `%LOCALAPPDATA%\Surge` (see [Where things are](#where-things-are)). Updating or uninstalling leaves
  it alone; the uninstaller asks before deleting it.
- Twitch and YouTube change often. To update the downloader, run
  `"%LOCALAPPDATA%\Programs\Surge\yt-dlp.exe" -U`.
- **Updates**: Surge asks GitHub for the newest release when it starts and every 6 hours while it runs. When there is one,
  a banner at the top offers the download; run the new installer and it updates in place. Only the latest-release
  info is requested: your clips, accounts and settings never leave your PC. Turn it off in Settings, Engine.

## Start it

- Open **Surge** from the Start menu or the desktop shortcut. A window opens; close it to stop the engine.
- Engine without a window: `Surge.exe --server`, then open http://127.0.0.1:8765 in a browser.
- Something broken? `%LOCALAPPDATA%\Surge\surge.log` has the engine's log, and `Surge.exe --selftest` checks that every
  bundled component loads.

## Daily flow

1. **Desk**: paste a live channel (`twitch.tv/name`, `kick.com/name`, a YouTube live link) or a past broadcast
   (`twitch.tv/videos/…`, a YouTube video, a local file). Add several streamers at once: volume comes from
   watching many streams in parallel.
2. Each stream row shows its live excitement trace. When the trace crosses the dashed line, Surge waits for the
   reaction to land, cuts the moment and starts processing. A finished clip takes about 20 to 60 seconds.
3. **Clips**: review, fix caption words, swap the hook or layout, re-render, then **Approve and schedule**.
   With **Autopilot** on, clips scoring above your bar are scheduled automatically.
4. **Schedule**: every account's posting slots on a timeline.
5. **Export**: the folder button on a finished past-broadcast or Already viral row copies its clips, best first,
   into `Videos\Surge exports\…` with a `captions.txt` (title, caption, hashtags, music credit, source second)
   for sending to a campaign manager or posting by hand.

### Already viral: re-cut what viewers clipped most

Pick **Already viral** on the Desk, paste a Twitch channel (`twitch.tv/kaicenat`) and choose the last 24 hours,
7 days or 30 days. Surge reads the channel's most-viewed viewer clips, groups clips of the same moment, ranks the
moments by total views, reads a little audio around each one to find the real peak, then cuts only those
sections from the broadcast in full quality and runs them through the normal clip pipeline. Moments Twitch muted
for music, moments where almost nothing is said, and moments where the game screen is black are skipped and
replaced from the next ones down. **Run again** tops the row back up to your clip count: clips you kept stay,
moments already tried are skipped. With an AI writer connected, the viewers' own clip titles help it write the
hook. The clips come from the streamer's broadcast (the VOD), not from the viewer clips themselves, and the
broadcast must still be up on Twitch.

## How it hits 20 to 30 clips in 20 minutes

- Past broadcast: Surge downloads only the audio and the chat replay first (a few hundred MB for 7 hours),
  scores every second, listens to the best ~50 windows, ranks them, and pulls just the winning sections in full
  quality. Clips render 3 at a time on the hardware encoder.
- Live: the trigger level adjusts itself toward **Target clips per hour** in Settings. Two or three streams at
  45 an hour each covers 20 to 30 clips per 20 minutes.

## What makes a clip

- **Signals**: chat speed against the channel's own normal rate, distinct chatters (one spammer can't fake a
  spike), what chat is saying (laughing, hyped, shocked, fail, "clip it"), the streamer's volume, and what the
  streamer shouts (from a live transcript). Chat is shifted back by its typing delay.
- **Boundaries**: the start snaps to the beginning of a sentence (the setup), the end lands just after the
  reaction, dead air at the top is cut.
- **No repeats**: one clip per moment. A spike inside an existing clip is skipped, and neighbouring clips are
  trimmed so no two clips share a second of footage.
- **Framing**: the webcam overlay is found automatically on every clip and the box snaps to the overlay's own
  border, so the facecam band never carries a slice of the game; the game crop slides off the webcam so it
  never shows twice. Split layout (facecam over gameplay) when there is
  an overlay; a 9:16 crop that follows the face when the streamer is on full camera; blurred fill
  otherwise. Set the box by hand under the crop button on a stream row.
- **Captions**: Whisper large-v3-turbo with word timestamps, game vocabulary as hotwords, 1 to 6 words at a time
  with the spoken word popping in colour. Swearing can be masked or bleeped.
- **Copy**: with an AI connector (Anthropic, or any OpenAI-compatible service such as OpenAI, Google Gemini,
  DeepSeek, OpenRouter, Groq, or a model on your own PC), the AI scores each clip, trims it, and writes the hook,
  title, description, hashtags and four extra angles so every account posts different text. Without one, Surge
  writes from the transcript and chat. See [AI writer](#ai-writer-bring-your-own-model).

## Sound and effects

- **Meme sounds**: 31 of the sounds Shorts edits run on (vine boom, bruh, bonk, metal pipe, air horn, record
  scratch, rizz, cha-ching, sad violin, dun dun dun, police siren...), downloaded from Pixabay on first start under
  its free licence (monetized use allowed, no credit). They are not shipped in the installer or this repository.
  To refresh the pack, delete `sfx\pack.json` in your data folder and restart Surge (from source:
  `python -m surge.fetch_sounds`).
- **Ratio and rotation**: only a share of clips gets meme sounds (Settings, default 70%), the top sounds come up
  most often, and no sound plays twice in a row.
- **Placement**: the AI writer (or the built-in director) puts the hit on the payoff, a build-up before shocks, a tail after
  fails, a sticker on the reaction, and a small zoom in slow stretches. Edit any cue in the clip drawer.
- **Music**: 12 Kevin MacLeod tracks (CC BY 4.0, credit line added to every post automatically). The song's drop
  lands on the clip's peak and ducks under the streamer's voice. The Sounds page lists 30 songs the clipping scene
  uses; they're commercial, so Content ID claims them on API uploads. Add them only if licensed.
- **Your files**: drop audio on the Sounds page. Names are read ("vine boom", "bruh", "phonk"...) to tag them, and
  your files are picked ahead of the pack.

## Running the operation

- **Setup**: step-by-step Google Cloud and Meta app setup with live checks (client file type, Meta credentials, AI
  connector), the YouTube upload counter for the day, and automatic Instagram sign-in when your redirect is the local
  address. Publish the Google consent screen to production, or Google signs channels out every 7 days.
- **Accounts**: health per account (sign-in state, failures today, next post), check or reconnect, groups with shared
  posting times, tags and caps, bulk pause/resume/cap/group, and warm-up (1 post a day, one more each week).
  Posting is either set times (one post each) or **Spread through the day**: the day's posts spaced evenly between
  two hours (all day by default), so 30 a day goes out about every 48 minutes.
- **Campaign sites** (Campaigns, Sites): up to 12 boards and agencies. Sign in on each in a Surge window (you type
  your own password on the site; the sign-in is kept), join campaigns and link accounts there, then tick the linked
  accounts in Surge. Surge does not automate these sites: their terms forbid it and it gets payout accounts banned.
- **Campaign routing**: a campaign names a site, streamers and accounts. Its streamers go on the watchlist; their
  clips post only to its accounts (optionally reserved for it), with its tags, mentions, tracking code and length
  rules. Posts land in its submission queue: tracked automatically where the site follows your linked accounts,
  otherwise copy all + open the submit page + mark submitted. Views, likes, comments and earnings per campaign.
- **Campaigns**: each clipping campaign's rules (tags, mentions, streamers, length, platforms) apply to posts on its
  enrolled accounts; earnings = views x rate per 1,000 up to the cap; Copy links gives the list to submit.
- **Watchlist** (Desk): streamers to watch automatically when they go live in GTA VI, biggest first, up to your limit;
  Find live GTA VI streams lists the biggest streams on Twitch right now.
- **Performance**: views per day, top posts, and views per post by streamer, chat reaction, meme sound, layout and
  account; storage clean-up of old source footage.

## AI writer: bring your own model

The AI writer is optional. Surge works without it: its built-in writer uses the transcript and chat. With a model
behind it you get sharper hooks and titles, a different angle for each account, a better edit plan, and better ranking
of broadcast moments. You do not need an Anthropic subscription: a connector is just a **base URL** and an **API key**.

Settings, AI writer, **Add connector**: pick a service (or *Custom*), paste your key, press **Fetch models**, choose a
model for each clip and one for ranking a broadcast, then press **Test**. Surge sends a real clip prompt and checks that
the reply is clean JSON before you rely on it. Add as many connectors as you like and switch with **Use this**. Keys are
stored encrypted on your PC.

| Service | Base URL | Each clip | Ranking a broadcast |
|---|---|---|---|
| Anthropic (best results) | `https://api.anthropic.com` | `claude-haiku-5-5` | `claude-sonnet-5-5` |
| OpenAI | `https://api.openai.com/v1` | `gpt-6-luna` | `gpt-6.2-sol` |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-3.8-flash` | `gemini-4-hydrogen` |
| DeepSeek (cheapest) | `https://api.deepseek.com` | `deepseek-flash` | `deepseek-v4-pro` |
| OpenRouter | `https://openrouter.ai/api/v1` | `deepseek/deepseek-v4.1-flash`, or any `:free` model | `anthropic/claude-sonnet-5.5` |
| Groq | `https://api.groq.com/openai/v1` | press Fetch models | press Fetch models |
| Ollama or LM Studio, on your PC | `http://localhost:11434/v1` or `http://localhost:1234/v1` | a 14B or larger instruct model | the same |
| Anything else | any service that offers `/chat/completions` (OpenAI format) or `/v1/messages` (Anthropic format) | | |

Model names above are as of November 2026; services rename models often, so **Fetch models** shows what yours offers
today. How to choose:

- **Best results**: Claude Haiku 5.5 for clips and Claude Sonnet 5.5 for ranking. Surge's prompts were written and tuned
  with Claude.
- **No Anthropic account**: any model on the list works if it passes **Test**. DeepSeek is the cheapest; OpenAI and
  Gemini are solid.
- **Free**: OpenRouter models whose name ends in `:free` (for example Gemma 4 31B), a free Google AI Studio key, or a
  model on your own PC with Ollama. Free tiers change and are rate limited.
- **On your own PC**: use a 14B or larger instruct model. Models under 8B often break the JSON Surge needs, and then
  Surge falls back to its built-in writer for that clip.
- Each clip is one short request, so pick a fast model that does not "think" first. On the cheap tier that is roughly a
  tenth of a cent per clip. Ranking sends about 15,000 to 20,000 tokens once per broadcast, so give it a stronger model
  with at least 64k context.
- Azure OpenAI's per-deployment addresses and header login are not supported.

## Connect accounts

### YouTube (up to as many channels as you like)

1. Google Cloud Console: create a project, enable **YouTube Data API v3**.
2. OAuth consent screen: add the Google accounts that own your channels as test users.
3. Create an OAuth client of type **Desktop app**, download the JSON, and add it in Settings, Platforms.
4. Accounts, **Connect YouTube channel**: your browser opens, pick the channel. Repeat per channel.

Shorts upload right away as private with a publish time, and YouTube publishes them on schedule even when this
PC is off.

**Until the project passes Google's free YouTube API audit, YouTube locks every API upload to private.**
Apply through the YouTube API Services audit form before you rely on it. Uploads per project per day are capped
by Google; one project serves all your channels.

### Instagram (Business or Creator accounts)

1. Create a Meta app with **Instagram API with Instagram login**.
2. Either paste an access token per account (Accounts, Connect Instagram), or sign in through Instagram after
   adding the app ID, secret and redirect address in Settings.
3. A Business portfolio system-user token (starts with `EAA`) connects every Instagram account linked to your
   Pages at once.

Until Meta reviews the app, only accounts added as testers can post. Instagram allows about 50 API posts per
account per day. Reels post from this app at their slot time, so leave Surge running.

## Rules that can cost you the account

- **Rockstar / Take-Two** tolerate non-commercial fan footage. Paid clipping campaigns and monetised channels
  are commercial use, which their policy does not cover: they can take clips down. Avoid isolated cutscenes,
  story spoilers, in-game TV and exploits.
- **YouTube reused content**: channels made of other people's footage without meaningful additions can be
  refused or removed from the Partner Program. Captions, hooks and editing help; they are not a guarantee.
- **Clip only streamers who allow it** (their own clipping campaign, or permission). Credit them: Surge adds
  the streamer's link to every post.
- Posting the same clip on many accounts can be treated as spam. Surge gives every account its own title,
  description and hashtag mix and spaces posts out, but keep **Accounts per clip** modest.

## Where things are

| | Installed | From source |
|---|---|---|
| Data folder | `%LOCALAPPDATA%\Surge` | `data\` in the repo |
| Clips | `clips\<id>\clip.mp4` | same, inside the data folder |
| Database | `surge.db` | same |
| Settings | `settings.json` (API keys encrypted with Windows DPAPI) | same |
| AI connectors | `connectors.json` (keys encrypted with Windows DPAPI) | same |
| Account tokens | `tokens\` (encrypted with Windows DPAPI) | same |
| Meme sound pack | `sfx\` | same |
| Whisper models | `models\` | same |
| Log | `surge.log` | console |
| Engine code | inside `Surge.exe` | `backend\surge\` |
| UI code | inside `Surge.exe` | `ui\src\` |

Set the `SURGE_DATA` environment variable to use a different data folder, and `SURGE_PORT` to run on a port other than
8765.

## Build from source

Needs Python 3.12, Node 20.19+ (or 22.12+) and ffmpeg on your PATH (`winget install Gyan.FFmpeg`).

```powershell
git clone https://github.com/Satoruatwork/surge-clipper.git; cd surge-clipper
py -3.12 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
cd ui; npm ci; npm run build; cd ..
backend\.venv\Scripts\python.exe -m pytest backend\tests
.\Surge.cmd
```

Develop with the engine and the Vite dev server (http://localhost:5173 proxies to the engine):

```powershell
cd backend; .venv\Scripts\python.exe -m surge.main --server
```

```powershell
cd ui; npm run dev
```

## Build the installer

Needs everything above plus [Inno Setup 6](https://jrsoftware.org/isdl.php). One command freezes the app with
PyInstaller, downloads and checksum-verifies ffmpeg and yt-dlp, self-tests the frozen build, and compiles the installer:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

The installer lands in `build\installer\Surge-Setup-<version>.exe` with a `SHA256SUMS.txt`. The version comes from
`backend\surge\__init__.py`.

### Publish a release

Bump `__version__`, commit, then push a tag:

```powershell
git tag v0.1.0; git push origin v0.1.0
```

The **Release** workflow (`.github/workflows/release.yml`) builds the installer on GitHub and attaches it to a new
release. Installed copies show it in the update banner on their next check (right away after a restart). The **CI**
workflow runs the tests and builds the UI on every push and pull request.

## Code signing policy

The Windows installer is **not code-signed yet**, which is why Windows says "unknown publisher". Until it is signed,
you can check a download yourself:

- Installers from 0.2.0 on are built by GitHub Actions from the tagged commit
  ([release workflow](.github/workflows/release.yml)); nobody uploads a build from their own PC. (0.1.0 was built on
  the maintainer's PC.)
- Compare the installer's SHA-256 with the one on the release page and in `SHA256SUMS.txt`.
- The source and the build script (`packaging\build.ps1`) are in this repository.

Team roles: authors, reviewers and approvers are [Satoruatwork](https://github.com/Satoruatwork), the sole maintainer.

Privacy policy: see [PRIVACY.md](PRIVACY.md). In short, Surge has no telemetry and no server of its own. It contacts
only the services listed there: automatically for the update check, the first-start sound pack and the caption models,
and otherwise only when you ask.

## Credits and licenses

Surge is MIT licensed (`LICENSE`). The installer bundles FFmpeg (GPL v3), yt-dlp (Unlicense) and Streamlink (BSD),
and the repository carries fonts, emoji stickers and 12 CC BY 4.0 tracks by Kevin MacLeod
([incompetech.com](https://incompetech.com)). Full list: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
