# Social Video Autopublisher

A Python pipeline that builds a vertical short video every morning and publishes it to Instagram, Facebook and YouTube with no human intervention.

It runs in production for a Spanish-language reflections channel. Each run picks the next text in the series, finds a matching background clip, renders the video with synced subtitles and music, and publishes it to the three platforms.

## What it does

1. **Picks the content.** Reads the next unused entry from a JSON bank (quote, development text, visual theme, caption, series).
2. **Finds a background.** Searches Pexels and Pixabay for a clip that matches the theme, and avoids reusing clips.
3. **Renders the video.** 1080×1920 at 24 fps with Pillow and ffmpeg: series label, title card, subtitled development, music and a closing call to action.
4. **Hosts the file temporarily.** Uploads the MP4 to a WordPress media endpoint so Meta can fetch it by URL.
5. **Publishes.** Instagram Reels and Facebook video through the Graph API, and YouTube Shorts through the YouTube Data API with an OAuth refresh token.
6. **Cleans up and records.** Deletes the temporary media, marks the entry as used and writes a log line per step.

## Design decisions

- **Configuration outside the code.** Every credential lives in `keys.json`, which is git-ignored. `keys.example.json` shows the expected fields.
- **Test mode.** `python main.py --test` renders the video and opens it without publishing anything.
- **Failure isolation.** Each platform is published independently: a YouTube error does not cancel the Instagram post, and every result is logged. The entry is only marked as used if at least one platform succeeded, so a total failure is retried.
- **No double posting.** A daily guard skips the run if today's video already went out, so a scheduler retry never publishes twice.
- **Explicit waits.** The Instagram container is polled until its status is `FINISHED` before publishing, instead of guessing with a fixed sleep.

## Run it

```bash
pip install -r requirements.txt
cp keys.example.json keys.json          # fill in your own credentials
cp reflexiones.example.json reflexiones.json
python main.py --test                   # render only
python main.py                          # render and publish
```

Requires ffmpeg on the PATH. The font paths point to standard Windows fonts; change them in the config block to run on Linux or macOS. On the production machine it runs once a day from the Windows Task Scheduler.

## Stack

Python · Pillow · NumPy · ffmpeg · Meta Graph API · YouTube Data API v3 · Pexels and Pixabay APIs · WordPress REST API

Built with AI-assisted development (Claude Code), reviewed and maintained by hand.
