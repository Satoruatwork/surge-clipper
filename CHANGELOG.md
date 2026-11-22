# Changelog

Each `## <version>` section becomes that release's notes on GitHub and in the app's "What's new".

## 0.4.2

- Connect **Google Gemini**, **OpenCode Go** or **OpenCode Zen** with just a key: press Connect and Surge tests the best models your key can use with a real clip prompt, then keeps the first that answers properly for clips and for ranking. A free Gemini key gets Gemini Flash at no cost.
- **Pick for me** does the same for any other service, and **Fetch models** now lists what each model costs Surge per 1,000 clips and per broadcast, best and cheapest first.
- New **Best and cheapest models across your keys** card under Settings, AI writer: it compares every service you added and names the best and the cheapest model for writing clips and for ranking a broadcast, with a Use button that tests the model before switching. Models that failed on your key are left out.

## 0.4.1

- **Posts a day** now goes as high as you set it. Pick **Spread through the day** on an account or a group, or for several accounts at once, and Surge spaces that many posts evenly between the hours you choose (all day by default), each a few minutes off the exact time: 30 a day is one about every 48 minutes. Set times still post once each, and the account now tells you when it has fewer times than posts a day.
- When warm-up holds an account below its posts a day, the account says so, shows how many weeks the ramp takes, and offers to turn warm-up off. Several accounts can switch it off at once.
- The "today" count on Accounts and Schedule counts only today's posts. It used to include every post queued for the coming days.

## 0.4.0

- New **Already viral** mode on the Desk: paste a Twitch channel and Surge re-cuts the moments viewers clipped most in the last 24 hours, 7 days or 30 days, in your format, from the original broadcast.
- Already viral skips moments Twitch muted for music, moments with almost nothing said, and moments where the game screen is black, and fills in from the next moments down. Run again tops the row back up to your clip count.
- New **Export** button on finished rows: the clips go into a folder in your Videos, best first, with a captions.txt holding each title, caption, hashtags and music credit.
- Clips no longer open on a black loading screen: the start moves to the first frame of gameplay.
- The facecam crop keeps up when a streamer moves their cam during a stream, and no longer stops short at a door frame or wall edge in their room. Clips from one source stay framed the same way.
- Captions no longer repeat a phrase dozens of times when the speech model gets stuck (it could print "oh shit" 21 times in 3 seconds over loud music).
- The caption profanity mask now catches every form of a swear word or slur ("niggas", "fuckin'", "motherfuckers"), not only the exact base word.

## 0.3.1

- Updating while Surge is running is now safe. The installer closes Surge and the helper programs it started, checks that Surge.exe can be replaced, and if it can't, stops with a clear message before changing anything. Before, Windows could fail to close the app and the update left a half-updated install.
- Uninstalling while Surge is running closes it the same way.
- Set the `SURGE_PORT` environment variable to run Surge on a port other than 8765.

## 0.3.0

- The AI writer is no longer Anthropic-only. Under Settings, AI writer, add a connector with just a base URL and an API key for Anthropic or any OpenAI-compatible service (OpenAI, Google Gemini, DeepSeek, OpenRouter, Groq, Ollama, LM Studio and more). No Anthropic subscription needed.
- Pick one model for each clip and one for ranking a broadcast. Fetch models lists what your service offers, and Test sends a real clip prompt and checks that the model answers with clean JSON.
- A "Which model should I use?" guide recommends models for the best results, cheap and free options, and local models.
- Replies wrapped in code fences, extra chatter or "thinking" blocks are read correctly, so more models work. Your existing Anthropic key moves into the first connector by itself.
- Connector keys are encrypted on your PC and never leave the engine.

## 0.2.1

- The update check keeps working on shared and mobile networks where GitHub's request limit is already used up, and it retries after 30 minutes instead of 6 hours when a check fails.
- Check now gives a plain message when GitHub can't be reached.

## 0.2.0

- Surge now tells you when a newer version is out: a banner at the top with a download button, the version in the sidebar, and a switch plus "Check now" under Settings, Engine.
- The new installer updates in place and keeps your clips, accounts and settings.

## 0.1.0

- First public release: Windows installer, the engine and window in one app, and a meme sound pack that downloads on first start.
