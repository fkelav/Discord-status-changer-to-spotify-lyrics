#  Spotify → Discord Status

Automatically sets your Discord custom status to the **current lyric line** of whatever you're listening to on Spotify — synced in real time, line by line.

---

## What it does

- Detects what song you're playing on Spotify
- Fetches the lyrics with real timestamps (LrcLib)
- Updates your Discord status to the current lyric line the moment it starts
- Detects the mood of the song (sad, happy, chill, hype etc.) and picks a matching emoji
- Clears your status when you stop listening
- Shows a live terminal display with song info, progress bar, and current lyric

---

## Setup

You need **3 things** before running:

### 1. Spotify API credentials

1. Go to https://developer.spotify.com/dashboard
2. Log in and click **Create app**
3. Name it anything, set the Redirect URI to: `http://localhost:8888/callback`
4. Copy your **Client ID** and **Client Secret**

### 2. Discord token (your personal account token)

>  This is your **account** token, not a bot token. Keep it private — anyone with it can access your account.

1. Open Discord in your **browser** (discord.com, not the app)
2. Press `F12` to open DevTools
3. Go to the **Network** tab
4. Click on any request to `discord.com`
5. Look for the `Authorization` header in the request headers
6. Copy that value — that's your token

### 3. Genius token (optional)

Only needed as a fallback if LrcLib doesn't have lyrics for a song.

1. Go to https://genius.com/api-clients
2. Create an app and copy the **Client Access Token**

---

## Running it

Just double-click `SpotifyDiscordStatus.exe`

On first launch it will ask you to enter your tokens. After that they're saved and you won't need to enter them again.

From the menu:
- **[1] Start** — starts syncing
- **[2] Settings** — update any of your tokens
- **[3] Exit**

On first Spotify login, a browser window will open asking you to authorize the app. Just click **Agree** — this only happens once.

---

## Files it creates

| File | What it is |
|---|---|
| `config.json` | Your saved tokens (keep this private) |
| `spotify_discord.log` | Debug log in case something goes wrong |
| `.cache` | Spotify login cache (auto-managed) |

All files are created in the **same folder as the exe**. Don't move them.

---

## How lyrics sync works

1. When a new song starts, it fetches lyrics from **LrcLib** (free, no account needed)
2. LrcLib provides real `.lrc` timestamps — each line has an exact start time
3. The app sleeps until each line is due, then instantly updates your Discord status
4. Every 30 seconds it re-checks your actual Spotify position to stay in sync (in case you paused, seeked, or skipped)
5. If LrcLib doesn't have the song, it falls back to **Genius** (lyrics spread evenly across the song duration — less accurate)
6. If neither has it, it just shows the track name and artist

---

## Troubleshooting

**Discord status not updating**
- Make sure you used your account token, not a bot token
- Try grabbing the token again — it can expire if you change your password or log out

**Spotify won't connect**
- Double check your Client ID and Secret
- Make sure the Redirect URI in your Spotify app settings is exactly `http://localhost:8888/callback`
- Delete the `.cache` file next to the exe and try again

**No lyrics showing**
- LrcLib doesn't have every song, especially newer or obscure tracks
- Add a Genius token in Settings for a better fallback

**The exe gets flagged by antivirus**
- This is a false positive — PyInstaller-compiled exes commonly trigger this
- You can run the Python source directly instead if you prefer

---

## Legal / disclaimer

Using a Discord user token in an automated script (self-botting) violates Discord's Terms of Service. This tool is for personal use only. Use at your own risk.
