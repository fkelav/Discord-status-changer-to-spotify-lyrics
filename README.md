# Spotify → Discord Status

Syncs your Spotify playback to your Discord custom status in real time,
showing the current lyric line as it plays.

---

## What it does

- Fetches synced lyrics (with real timestamps) so your status updates
  line by line as the song plays
- Falls back to plain lyrics spread evenly across the song duration if
  synced lyrics aren't available
- Falls back to "Song — Artist" if no lyrics are found at all
- Detects pauses and shows a ⏸ status without drifting the position
- Detects when you seek in a song and jumps to the correct lyric
- Prefetches lyrics for the next queued song in the background so
  track changes are instant
- Picks an emoji based on the detected mood of the lyrics

---

## Requirements

Python 3.10 or newer.

Install dependencies:

```
pip install spotipy requests syncedlyrics
```

---

## Setup

### 1. Spotify app

Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
and create an app (or use an existing one).

- Copy your **Client ID** and **Client Secret**
- Under **Redirect URIs** add exactly: `http://127.0.0.1:23435/callback`
  (or whatever port you use — just make sure it matches what you put in the script)
- Save the settings

> Note: Spotify no longer allows `localhost` as a redirect URI as of 2025.
> Use `127.0.0.1` instead — it points to the same place.

### 2. Discord user token

> ⚠️ Using a user token (self-bot) violates Discord's Terms of Service.
> Use at your own risk.

To get your token:
1. Open Discord in a browser
2. Open DevTools (F12) → Network tab
3. Reload the page, find any request to `discord.com/api`
4. Look in the request headers for `Authorization` — that value is your token

### 3. First run

Run the script:

```
python spotify_discord_status.py
```

On first launch it will open the Settings menu automatically.
Enter your Spotify Client ID, Client Secret, Redirect URI, and Discord token.
They are saved to `config.json` next to the script.

---

## Files

| File | Purpose |
|---|---|
| `spotify_discord_status.py` | Main script |
| `config.json` | Saved tokens (auto-created on first run) |
| `spotify_discord.log` | Debug log — check this if something isn't working |
| `.cache-*` | Spotify OAuth token cache (auto-created by spotipy) |

---

## How lyrics are fetched

Priority order:

1. **syncedlyrics library** — tries multiple providers internally
   (LrcLib, NetEase, Megalobiz, Genius). Returns synced LRC timestamps
   when available, plain text otherwise.
2. **Direct LrcLib API** — fallback if syncedlyrics finds nothing.
   Tries with and without duration, with and without title cleaning,
   and also searches the LrcLib index.
3. **Track name only** — if both fail, sets Discord to `Song — Artist`
   with a progress timestamp.

Lyrics for the next queued track are fetched in the background while
the current song plays so there's no delay on track change.

---

## Settings reference

| Field | Where to get it |
|---|---|
| Spotify Client ID | Spotify Developer Dashboard → your app |
| Spotify Client Secret | Spotify Developer Dashboard → your app |
| Spotify Redirect URI | Must match what you added in the dashboard |
| Discord User Token | Browser DevTools → any Discord API request header |

---

## Changelog

### Current version
- Replaced LrcLib-only fetching with **syncedlyrics** library, which
  tries multiple providers (LrcLib, NetEase, Megalobiz, Genius)
  automatically, with direct LrcLib API as a further fallback
- **Background lyrics fetch** — when a track changes, Discord is updated
  to `Song — Artist` instantly while lyrics load in a background thread
- **Pause detection** — position freezes when paused, Discord shows ⏸,
  resumes correctly when playback continues
- **Seek detection** — if you scrub to a different part of a song, the
  correct lyric line is pushed to Discord immediately
- **Prefetch** — lyrics for the next queued song are fetched in the
  background so track changes are instant
- **Discord worker thread** — Discord status updates run in a dedicated
  background thread with a minimum 1.5s spacing between calls, preventing
  rate limit issues even during fast-changing lyrics
- **Discord 429 handling** — reads `Retry-After` header and backs off
  automatically if Discord rate limits a request
- **Title cleaning** — strips `(feat. X)`, `(Remastered 2011)`,
  `(Radio Edit)` etc. from track names before searching for lyrics,
  so lookups don't fail on annotated titles
- **Intro/instrumental handling** — shows `— Instrumental / Intro —`
  and the track name on Discord when the song position is before the
  first lyric timestamp
- **Smoother terminal rendering** — uses ANSI cursor positioning instead
  of clearing the screen, no flicker. Correct width calculation for
  emoji characters in the box drawing
- **Drift correction** — every 15–20 seconds, checks that the displayed
  lyric matches the actual song position and corrects silently if not
- **Fast end-of-song detection** — polls Spotify every 3 seconds in the
  last 10 seconds of a track so the new song is caught within 3 seconds
- **5-second sleep cap** — the main loop never sleeps more than a few
  seconds, so manual skips mid-song are always caught quickly
- Redirect URI default changed from `localhost` to `127.0.0.1`
  (Spotify API requirement change, May 2025)

---

## Troubleshooting

**Lyrics not showing / "None (track name only)"**
Check `spotify_discord.log`. Look for lines mentioning syncedlyrics or
LrcLib errors. If you see timeouts, the lyrics provider may be temporarily
down or blocked on your network. Try opening `https://lrclib.net` in a
browser to check.

**Discord status not updating**
Check the log for Discord errors. A `401` means your token is wrong or
expired — grab a fresh one from browser DevTools. A `403` means the
endpoint rejected the request.

**Spotify keeps asking me to re-authenticate**
Delete the `.cache-*` file next to the script and restart. This forces
a fresh OAuth flow.

**Song takes a while to appear after skipping**
This is expected if the song wasn't prefetched (e.g. you picked a song
directly from your library rather than the queue). The script sets a
`Song — Artist` placeholder on Discord immediately and loads lyrics in
the background — typically within 2–5 seconds depending on your connection.

if anyone needs help @fkelav on discord
