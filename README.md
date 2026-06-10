# Spotify → Discord Status

Syncs your Spotify playback to your Discord custom status in real time, showing the current lyric line as it plays.

---

## What it does

- Fetches synced lyrics (with real timestamps) so your status updates line by line as the song plays
- Falls back to plain lyrics spread evenly across the song duration if synced lyrics aren't available
- Falls back to `Song — Artist` if no lyrics are found at all
- Detects pauses and shows a ⏸ status without drifting the position
- Detects when you seek in a song and jumps to the correct lyric
- Prefetches lyrics for the next queued song in the background so track changes are instant
- Picks an emoji based on the detected mood of the lyrics (happy, sad, angry, romantic, chill, hype, neutral)
- Renders your current album art as pixel art directly in the terminal using ANSI true colour

---

## Requirements

Python 3.10 or newer.

Install dependencies:

```
pip install spotipy requests syncedlyrics Pillow
```

> `Pillow` is required for album art rendering. The script will run without it but album art will be disabled.

Or just open the exe

---

## Setup

### 1. Spotify app

Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and create an app (or use an existing one).

- Copy your **Client ID** and **Client Secret**
- Under **Redirect URIs** add exactly: `http://127.0.0.1:23435/callback`
- Save the settings

> **Note:** Spotify no longer allows `localhost` as a redirect URI as of 2025. Use `127.0.0.1` instead — it points to the same place.

### 2. Discord user token

> ⚠️ Using a user token (self-bot) violates Discord's Terms of Service. Use at your own risk.

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

On first launch it will open the Settings menu automatically. Enter your Spotify Client ID, Client Secret, Redirect URI, and Discord token.

Config is saved to `%APPDATA%\SpotifyDiscordStatus\config.json` on Windows.

---

## Main menu

On launch you'll see a menu with three options:

| Option | Action |
|--------|--------|
| `[1] Start` | Begin syncing lyrics to Discord |
| `[2] Settings` | Update individual tokens or all at once |
| `[3] Exit` | Quit |

---

## Files

| File | Purpose |
|------|---------|
| `spotify_discord_status.py` | Main script |
| `%APPDATA%\SpotifyDiscordStatus\config.json` | Saved tokens (auto-created on first run) |
| `%APPDATA%\SpotifyDiscordStatus\spotify_discord.log` | Debug log — check this if something isn't working |
| `%APPDATA%\SpotifyDiscordStatus\.cache` | Spotify OAuth token cache (auto-created by spotipy) |

---

## How lyrics are fetched

Priority order:

1. **syncedlyrics library** — tries multiple providers internally (LrcLib, NetEase, Megalobiz, Genius). Returns synced LRC timestamps when available, plain text otherwise.
2. **Direct LrcLib API** — fallback if syncedlyrics finds nothing. Tries with and without duration, with and without title cleaning, and also searches the LrcLib index.
3. **Track name only** — if both fail, sets Discord to `🎵 Song — Artist` with a progress timestamp.

Failed fetches are automatically retried once after a short delay before falling back to track name only.

Lyrics for the next queued track are prefetched in the background 10 seconds into the current song, so there's no delay on track change.

---

## Terminal UI

The terminal display shows:

- Song title, artist, and a progress bar
- The current lyric line being shown on Discord
- The lyrics source (syncedlyrics synced/plain, LrcLib, etc.)
- Discord update status (Updated / Failed / Paused)
- **Album art** rendered as ANSI pixel art using Unicode half-block characters (`▄`) with true 24-bit colour — requires Pillow
- The emojy in a speech bubble, speaking the current lyric. The emojy variant is deterministically chosen from 25 options based on the current song, so it stays consistent per track but changes between songs.

---

## Settings reference

| Field | Where to get it |
|-------|----------------|
| Spotify Client ID | Spotify Developer Dashboard → your app |
| Spotify Client Secret | Spotify Developer Dashboard → your app |
| Spotify Redirect URI | Must match what you added in the dashboard (`http://127.0.0.1:23435/callback`) |
| Discord User Token | Browser DevTools → any Discord API request header |

---

## Troubleshooting

**Lyrics not showing / "None (track name only)"**
Check `spotify_discord.log`. Look for syncedlyrics or LrcLib errors. If you see timeouts, the lyrics provider may be temporarily down. Try opening `https://lrclib.net` in a browser to check.

**Discord status not updating**
Check the log for Discord errors. A `401` means your token is wrong or expired — grab a fresh one from browser DevTools. A `403` means the endpoint rejected the request.

**Spotify keeps asking me to re-authenticate**
Delete `%APPDATA%\SpotifyDiscordStatus\.cache` and restart. This forces a fresh OAuth flow.

**Song takes a while to appear after skipping**
Expected if the song wasn't prefetched (e.g. you picked it directly from your library rather than the queue). The script sets `Song — Artist` on Discord immediately and loads lyrics in the background — typically 2–5 seconds depending on your connection.

**Album art not showing**
Make sure Pillow is installed: `pip install Pillow`. The script logs a warning and disables album art gracefully if it isn't.

---

If you need help @fkelav on discord