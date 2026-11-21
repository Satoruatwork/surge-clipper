# Privacy policy

Surge runs on your PC. It has no accounts, no analytics, no telemetry and no server of its own: nothing you do in it
is sent to the maintainers. Your clips, database, settings and sign-in tokens stay in `%LOCALAPPDATA%\Surge`. API keys
and tokens are encrypted there with Windows DPAPI.

Surge does talk to other services. This is everything it contacts, and when.

## Automatically

| Service | When | What is sent |
|---|---|---|
| GitHub (api.github.com, github.com) | At start and every 6 hours, to look for a newer version | A normal web request: your IP address and the text `Surge/<version>`. Turn it off in Settings, Engine. |
| Pixabay (pixabay.com, cdn.pixabay.com) | First start only, to download the meme sound pack | A normal web request for the pages and sound files. |
| Hugging Face (huggingface.co) | The first time a caption model is needed, to download it | A normal web request for the Whisper speech model files. |

## Only when you ask

- **Twitch, Kick and YouTube**: when you add a stream, a past broadcast or a watchlist streamer, Surge (with yt-dlp and
  Streamlink) reads the stream, its chat and the channel's public details from that service.
- **YouTube (Google) and Instagram (Meta)**: when you connect an account and approve a post, Surge uploads the finished
  clip with its title, description and hashtags to that account through the official API, using your own developer app.
  You sign in on Google's or Instagram's own page; Surge keeps the resulting token on your PC.
- **Your AI service**: if you add an AI connector, the clip's transcript, a chat summary and clip details are sent to
  the service you chose (Anthropic, OpenAI, Google, DeepSeek, OpenRouter, Groq, or a server you run yourself) to score the
  clip and write its copy, and a broadcast's candidate moments are sent to rank them. Without a connector nothing is
  sent. The key is stored encrypted on your PC and is only ever sent to the address you entered.
- **Campaign sites**: the sign-in windows open the site you picked and keep its cookies in Surge's own browser profile on
  your PC. Surge does not automate these sites.

## What the maintainers can see

Nothing. There is no collection point. GitHub shows download counts for releases; that is GitHub's own statistic.

## Other services' privacy policies

- GitHub: https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement
- Google and YouTube: https://policies.google.com/privacy
- Meta and Instagram: https://www.facebook.com/privacy/policy/
- Twitch: https://www.twitch.tv/p/legal/privacy-notice/
- Kick: https://kick.com/privacy-policy
- Anthropic: https://www.anthropic.com/legal/privacy
- OpenAI: https://openai.com/policies/privacy-policy
- Any other AI service you connect (DeepSeek, OpenRouter, Groq, ...): read its own privacy policy first
- Hugging Face: https://huggingface.co/privacy
- Pixabay: https://pixabay.com/service/privacy/

Changes to this policy are visible in this file's history.
