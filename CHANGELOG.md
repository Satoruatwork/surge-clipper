# Changelog

Each `## <version>` section becomes that release's notes on GitHub and in the app's "What's new".

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
