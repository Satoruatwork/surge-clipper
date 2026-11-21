# Third-party notices

Surge's own code is MIT-licensed (see `LICENSE`). The installer and the source tree include or fetch the components
below, each under its own license.

## Programs bundled in the installer

| Component | License | Source |
|---|---|---|
| FFmpeg and FFprobe (Gyan.dev "full" static build) | GPL v3 (the build enables GPL components; text in `licenses\ffmpeg-LICENSE.txt`) | https://ffmpeg.org/ · build and source links: https://www.gyan.dev/ffmpeg/builds/ |
| yt-dlp | The Unlicense | https://github.com/yt-dlp/yt-dlp |
| Streamlink | BSD 2-Clause | https://github.com/streamlink/streamlink |

FFmpeg and yt-dlp run as separate programs that Surge starts; Surge does not link against them. The corresponding
source for the bundled FFmpeg build is available from the links above.

## Python libraries (frozen into `Surge.exe`)

faster-whisper, CTranslate2 and ONNX Runtime (MIT) · OpenCV (Apache-2.0) · PyAV (BSD-3-Clause) · NumPy (BSD-3-Clause) ·
FastAPI and pydantic (MIT) · Starlette, Uvicorn, httpx, websockets (BSD-3-Clause) · pywebview (BSD-3-Clause) ·
pythonnet (MIT) · curl_cffi (MIT) · anthropic (MIT) · google-api-python-client, google-auth (Apache-2.0).
Each package's license text ships in its own metadata inside the bundle.

## Assets in `assets\`

| Asset | License |
|---|---|
| Montserrat, Anton, Bebas Neue | SIL Open Font License 1.1 (`assets\fonts\OFL.txt`) |
| Luckiest Guy (Astigmatic) | Apache License 2.0 |
| Emoji stickers (Noto Emoji) | SIL Open Font License 1.1 (`assets\stickers\LICENSE-noto-emoji.txt`) |
| Music: Kevin MacLeod, 12 tracks (incompetech.com) | Creative Commons Attribution 4.0. Surge adds the credit line to every post that uses a track. https://creativecommons.org/licenses/by/4.0/ |
| YuNet face detector (`face_detection_yunet_2023mar.onnx`) | MIT, from the OpenCV Zoo: https://github.com/opencv/opencv_zoo |

## Downloaded on your machine, never redistributed by Surge

- **Meme sound pack**: fetched from Pixabay on first start under the Pixabay Content License. It is not shipped in
  the installer or committed to the repository.
- **Whisper speech models** (large-v3-turbo, base.en): fetched from Hugging Face by faster-whisper on first use.
  The Whisper models are MIT-licensed (OpenAI).
