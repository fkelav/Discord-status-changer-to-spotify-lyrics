import os
import re
import sys
import time
import json
import random
import logging

import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth

def config_path() -> str:
    base = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    return os.path.join(base, "config.json")

def load_config() -> dict:
    try:
        with open(config_path(), "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_config(cfg: dict):
    with open(config_path(), "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"  ✓ Saved to {config_path()}")

logging.basicConfig(
    filename="spotify_discord.log",
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
log = logging.getLogger(__name__)

MAX_STATUS_LEN   = 128
RESYNC_EVERY     = 30
DRIFT_CHECK_EVERY = random.uniform(15, 20)
DRIFT_THRESHOLD  = 2.5
END_OF_SONG_WINDOW = 10
END_OF_SONG_POLL   = 3

MOOD_EMOJI = {
    "happy":    ["😄", "✨", "🌟", "🎉", "🥳"],
    "sad":      ["💙", "🌧️", "😔", "🫂", "🥺"],
    "angry":    ["🔥", "💢", "😤", "⚡", "🌪️"],
    "romantic": ["❤️", "🌹", "💞", "🥰", "✨"],
    "chill":    ["😌", "🌊", "🍃", "💭", "🌙"],
    "hype":     ["🚀", "💥", "🎵", "🔊", "🤘"],
    "neutral":  ["🎵", "🎶", "🎧", "💫", "✨"],
}

MOOD_KEYWORDS = {
    "happy":    ["happy", "joy", "smile", "laugh", "sunshine", "wonderful", "great", "bright", "celebrate"],
    "sad":      ["cry", "tears", "sad", "alone", "miss", "hurt", "pain", "broken", "lost", "empty"],
    "angry":    ["hate", "rage", "angry", "war", "fight", "burn", "destroy", "kill", "mad", "furious"],
    "romantic": ["love", "heart", "kiss", "darling", "baby", "hold", "together", "forever", "mine"],
    "chill":    ["dream", "float", "easy", "breeze", "slow", "calm", "flow", "drift", "fade"],
    "hype":     ["run", "jump", "loud", "hard", "fast", "go", "push", "rise", "power", "wild"],
}

FIELDS = [
    ("SPOTIPY_CLIENT_ID",     "Spotify Client ID"),
    ("SPOTIPY_CLIENT_SECRET", "Spotify Client Secret"),
    ("SPOTIPY_REDIRECT_URI",  "Spotify Redirect URI"),
    ("GENIUS_ACCESS_TOKEN",   "Genius Access Token (optional, fallback lyrics)"),
    ("DISCORD_TOKEN",         "Discord User Token"),
]

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def fmt_time(seconds: float) -> str:
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"

def print_status(
    song: str,
    artist: str,
    progress_s: float,
    duration_s: float,
    current_line: str,
    source: str,
    discord_ok: bool,
):
    clear_screen()
    W = 54
    bar_width = W - 2
    filled = int((progress_s / max(duration_s, 1)) * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)

    print("╔" + "═" * W + "╗")
    print(f"║  🎵  Spotify → Discord Status{' ' * (W - 29)}║")
    print("╠" + "═" * W + "╣")
    print(f"║  Song:     {song[:W-13]:<{W-13}}║")
    print(f"║  Author:   {artist[:W-13]:<{W-13}}║")
    print("║" + " " * W + "║")
    print(f"║  [{bar}]  ║")
    prog_str = f"{fmt_time(progress_s)} / {fmt_time(duration_s)}"
    print(f"║  {prog_str:<{W-2}}║")
    print("╠" + "═" * W + "╣")
    line_display = current_line[:W-4] if current_line else "—"
    print(f"║  ❝ {line_display:<{W-4}}║")
    print("╠" + "═" * W + "╣")
    src_str   = f"  Lyrics source : {source}"
    disc_str  = f"  Discord status: {'✓ Updated' if discord_ok else '✗ Failed'}"
    print(f"║{src_str:<{W}}║")
    print(f"║{disc_str:<{W}}║")
    print("╠" + "═" * W + "╣")
    print(f"║  Press Ctrl+C to stop{' ' * (W - 21)}║")
    print("╚" + "═" * W + "╝")

def set_discord_status(token: str, text: str, emoji: str = "🎵") -> bool:
    url     = "https://discord.com/api/v9/users/@me/settings"
    headers = {"Authorization": token, "Content-Type": "application/json"}
    payload = {"custom_status": {"text": text[:MAX_STATUS_LEN], "emoji_name": emoji}}
    try:
        resp = requests.patch(url, json=payload, headers=headers, timeout=10)
        return resp.status_code == 200
    except requests.RequestException:
        return False

def clear_discord_status(token: str):
    url     = "https://discord.com/api/v9/users/@me/settings"
    headers = {"Authorization": token, "Content-Type": "application/json"}
    try:
        requests.patch(url, json={"custom_status": None}, headers=headers, timeout=10)
    except requests.RequestException:
        pass

def parse_lrc(lrc_text: str) -> list[tuple[float, str]]:
    pattern = re.compile(r"\[(\d+):(\d+\.\d+)\](.*)")
    result  = []
    for match in pattern.finditer(lrc_text):
        minutes  = int(match.group(1))
        seconds  = float(match.group(2))
        text     = match.group(3).strip()
        if text:
            result.append((minutes * 60 + seconds, text))
    return sorted(result, key=lambda x: x[0])

def fetch_lyrics_lrclib(artist: str, title: str, duration_s: float) -> list[tuple[float, str]] | None:
    try:
        resp = requests.get(
            "https://lrclib.net/api/get",
            params={
                "artist_name": artist,
                "track_name":  title,
                "duration":    int(duration_s),
            },
            timeout=10,
            headers={"User-Agent": "SpotifyDiscordStatus/1.0"},
        )
        if resp.status_code != 200:
            return None
        data     = resp.json()
        lrc_text = data.get("syncedLyrics") or data.get("plainLyrics")
        if not lrc_text:
            return None
        if data.get("syncedLyrics"):
            return parse_lrc(lrc_text)
        lines = [l.strip() for l in lrc_text.splitlines() if l.strip()]
        time_per = duration_s / max(len(lines), 1)
        return [(i * time_per, l) for i, l in enumerate(lines)]
    except Exception as exc:
        log.warning("LrcLib error: %s", exc)
        return None

def fetch_lyrics_genius(genius_token: str, artist: str, title: str, duration_s: float) -> list[tuple[float, str]] | None:
    if not genius_token:
        return None
    headers = {"Authorization": f"Bearer {genius_token}"}
    try:
        resp = requests.get(
            "https://api.genius.com/search",
            params={"q": f"{title} {artist}"},
            headers=headers,
            timeout=10,
        )
        hits = resp.json().get("response", {}).get("hits", [])
        if not hits:
            return None
        song_url = hits[0]["result"]["url"]
        page     = requests.get(song_url, timeout=10)
        raw      = re.findall(r'data-lyrics-container="true"[^>]*>(.*?)</div>', page.text, re.DOTALL)
        if not raw:
            return None
        text = " ".join(raw)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\[.*?\]", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        lines    = [l.strip() for l in text.splitlines() if l.strip()]
        time_per = duration_s / max(len(lines), 1)
        return [(i * time_per, l) for i, l in enumerate(lines)]
    except Exception as exc:
        log.warning("Genius error: %s", exc)
        return None

def detect_mood(timed_lines: list[tuple[float, str]]) -> str:
    text   = " ".join(l for _, l in timed_lines).lower()
    scores = {mood: sum(text.count(kw) for kw in kws) for mood, kws in MOOD_KEYWORDS.items()}
    best   = max(scores, key=scores.get)
    return best if scores[best] > 0 else "neutral"

def line_index_at(timed_lines: list[tuple[float, str]], pos_s: float) -> int:
    idx = 0
    for i, (start, _) in enumerate(timed_lines):
        if start <= pos_s:
            idx = i
        else:
            break
    return idx

def mask(value: str) -> str:
    if not value:
        return "(not set)"
    if len(value) <= 12:
        return "*" * len(value)
    return value[:6] + "*" * (len(value) - 10) + value[-4:]

def config_menu() -> dict:
    cfg = load_config()
    while True:
        clear_screen()
        print("╔" + "═" * 52 + "╗")
        print("║  ⚙  Settings" + " " * 39 + "║")
        print("╠" + "═" * 52 + "╣")
        for i, (key, label) in enumerate(FIELDS, 1):
            val_str = mask(cfg.get(key, ""))
            print(f"║  [{i}] {label:<35}║")
            print(f"║      {val_str:<46}║")
            print("║" + " " * 52 + "║")
        print("║  [A] Update ALL" + " " * 36 + "║")
        print("║  [B] Back" + " " * 42 + "║")
        print("╚" + "═" * 52 + "╝")
        choice = input("\n  Choice: ").strip().upper()

        if choice == "B":
            break
        elif choice == "A":
            print("\n  Press Enter to keep current value.\n")
            for key, label in FIELDS:
                current = cfg.get(key, "")
                if key == "SPOTIPY_REDIRECT_URI" and not current:
                    current = "http://127.0.0.1:8888/callback"
                hint = f" [{mask(current)}]" if current else ""
                val = input(f"  {label}{hint}\n  > ").strip()
                if val:
                    cfg[key] = val
                elif not cfg.get(key) and key == "SPOTIPY_REDIRECT_URI":
                    cfg[key] = "http://127.0.0.1:8888/callback"
            save_config(cfg)
        elif choice.isdigit() and 1 <= int(choice) <= len(FIELDS):
            key, label = FIELDS[int(choice) - 1]
            current    = cfg.get(key, "")
            hint       = f" [{mask(current)}]" if current else ""
            val        = input(f"\n  {label}{hint}\n  > ").strip()
            if val:
                cfg[key] = val
                save_config(cfg)
            else:
                print("  (no change)")
                time.sleep(1)
        else:
            print("  Invalid choice.")
            time.sleep(1)

    return load_config()

def main_menu() -> str:
    clear_screen()
    print("╔" + "═" * 38 + "╗")
    print("║  🎵  Spotify → Discord Status     ║")
    print("╠" + "═" * 38 + "╣")
    print("║  [1] Start                        ║")
    print("║  [2] Settings                     ║")
    print("║  [3] Exit                         ║")
    print("╚" + "═" * 38 + "╝")
    return input("\n  Choice: ").strip()

def run(cfg: dict):
    required = {"SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "DISCORD_TOKEN"}
    missing  = [label for key, label in FIELDS if key in required and not cfg.get(key)]
    if missing:
        print(f"\n  ✗ Missing: {', '.join(missing)}")
        print("    Go to Settings [2] to fill them in.")
        time.sleep(3)
        return

    redirect = cfg.get("SPOTIPY_REDIRECT_URI") or "http://127.0.0.1:8888/callback"
    try:
        auth = SpotifyOAuth(
            client_id=cfg["SPOTIPY_CLIENT_ID"],
            client_secret=cfg["SPOTIPY_CLIENT_SECRET"],
            redirect_uri=redirect,
            scope="user-read-currently-playing user-read-playback-state",
        )
        sp = spotipy.Spotify(auth_manager=auth)
    except Exception as exc:
        print(f"\n  ✗ Spotify auth failed: {exc}")
        time.sleep(3)
        return

    discord_token = cfg["DISCORD_TOKEN"]
    genius_token  = cfg.get("GENIUS_ACCESS_TOKEN", "")

    last_track_id   = None
    timed_lines     = []
    lyrics_source   = "—"
    mood            = "neutral"
    current_idx     = -1
    last_resync     = 0.0
    anchor_pos_s    = 0.0
    anchor_time     = time.monotonic()
    discord_ok      = False
    last_drift_check = 0.0
    next_drift_interval = random.uniform(15, 20)

    ui_song        = ""
    ui_artist      = ""
    ui_duration_s  = 0.0
    ui_line        = ""

    def do_spotify_poll() -> dict | None:
        try:
            return sp.current_playback()
        except Exception as exc:
            log.error("Spotify poll: %s", exc)
            return None

    def load_new_track(track_id, track_name, artist_name, duration_s):
        nonlocal ui_line
        ui_line = "Fetching lyrics..."
        clear_screen()
        print(f"  ♫  {track_name} — {artist_name}")
        print("  Fetching lyrics from LrcLib...")

        result = fetch_lyrics_lrclib(artist_name, track_name, duration_s)
        if result:
            src = "LrcLib (synced)" if result[0][0] > 0 else "LrcLib (plain)"
        else:
            print("  LrcLib failed, trying Genius...")
            result = fetch_lyrics_genius(genius_token, artist_name, track_name, duration_s)
            src = "Genius (estimated)" if result else "None (track name only)"
            if not result:
                result = []

        m = detect_mood(result) if result else "neutral"
        log.info("Now playing: %s – %s | Source: %s | Lines: %d | Mood: %s",
                 artist_name, track_name, src, len(result), m)
        return result, src, m

    try:
        while True:
            pos_s = anchor_pos_s + (time.monotonic() - anchor_time)

            near_end = (
                ui_duration_s > 0
                and pos_s >= ui_duration_s - END_OF_SONG_WINDOW
            )
            poll_interval = END_OF_SONG_POLL if near_end else RESYNC_EVERY
            time_since_resync = time.monotonic() - last_resync
            need_poll = last_track_id is None or time_since_resync >= poll_interval

            if need_poll:
                playback = do_spotify_poll()

                if playback is None:
                    time.sleep(5)
                    continue

                if not playback or not playback.get("is_playing"):
                    if last_track_id is not None:
                        clear_discord_status(discord_token)
                        last_track_id = None
                        timed_lines   = []
                        current_idx   = -1
                        ui_line       = "— nothing playing —"
                        ui_duration_s = 0.0
                    clear_screen()
                    print("  ⏸  Nothing playing. Waiting for next track...")
                    last_resync = time.monotonic()
                    time.sleep(END_OF_SONG_POLL)
                    continue

                item        = playback["item"]
                track_id    = item["id"]
                track_name  = item["name"]
                artist_name = item["artists"][0]["name"]
                progress_ms = playback["progress_ms"]
                duration_ms = item["duration_ms"]
                duration_s  = duration_ms / 1000

                ui_song       = track_name
                ui_artist     = artist_name
                ui_duration_s = duration_s

                if track_id != last_track_id:
                    last_track_id       = track_id
                    current_idx         = -1
                    last_drift_check    = time.monotonic()
                    next_drift_interval = random.uniform(15, 20)
                    timed_lines, lyrics_source, mood = load_new_track(
                        track_id, track_name, artist_name, duration_s
                    )

                anchor_pos_s = progress_ms / 1000
                anchor_time  = time.monotonic()
                last_resync  = time.monotonic()
                pos_s = anchor_pos_s

            if (timed_lines
                    and current_idx >= 0
                    and (time.monotonic() - last_drift_check) >= next_drift_interval):

                expected_idx = line_index_at(timed_lines, pos_s)
                if expected_idx != current_idx:
                    log.info(
                        "Drift corrected: line %d → %d at %.1fs",
                        current_idx, expected_idx, pos_s,
                    )
                    current_idx = expected_idx
                    _, line     = timed_lines[current_idx]
                    ui_line     = line
                    emoji       = random.choice(MOOD_EMOJI.get(mood, MOOD_EMOJI["neutral"]))
                    discord_ok  = set_discord_status(discord_token, line, emoji)

                last_drift_check    = time.monotonic()
                next_drift_interval = random.uniform(15, 20)

            if timed_lines:
                new_idx = line_index_at(timed_lines, pos_s)
                if new_idx != current_idx:
                    current_idx = new_idx
                    _, line     = timed_lines[current_idx]
                    ui_line     = line
                    emoji       = random.choice(MOOD_EMOJI.get(mood, MOOD_EMOJI["neutral"]))
                    discord_ok  = set_discord_status(discord_token, line, emoji)
                    log.info("Line %d: %s", current_idx, line)
            else:
                if current_idx == -1:
                    fallback    = f"{ui_song} — {ui_artist}"
                    ui_line     = fallback
                    discord_ok  = set_discord_status(discord_token, fallback)
                    current_idx = 0

            print_status(
                song         = ui_song,
                artist       = ui_artist,
                progress_s   = pos_s,
                duration_s   = ui_duration_s,
                current_line = ui_line,
                source       = lyrics_source,
                discord_ok   = discord_ok,
            )

            pos_s = anchor_pos_s + (time.monotonic() - anchor_time)
            candidates = []

            if timed_lines and current_idx + 1 < len(timed_lines):
                next_line_in = timed_lines[current_idx + 1][0] - pos_s
                if next_line_in > 0:
                    candidates.append(next_line_in)

            next_poll_in = poll_interval - (time.monotonic() - last_resync)
            candidates.append(max(0.1, next_poll_in))

            next_drift_in = next_drift_interval - (time.monotonic() - last_drift_check)
            candidates.append(max(0.1, next_drift_in))

            sleep_s = min(candidates) if candidates else 2.0

            if near_end:
                sleep_s = min(sleep_s, END_OF_SONG_POLL)
            time.sleep(sleep_s)

    except KeyboardInterrupt:
        clear_discord_status(discord_token)
        clear_screen()
        print("  Stopped. Discord status cleared.\n")

if __name__ == "__main__":
    cfg = load_config()

    if not cfg:
        clear_screen()
        print("  Welcome! No config found — let's set up your tokens.\n")
        time.sleep(1)
        cfg = config_menu()

    while True:
        choice = main_menu()
        if choice == "1":
            run(cfg)
            cfg = load_config()
        elif choice == "2":
            cfg = config_menu()
        elif choice == "3":
            clear_screen()
            print("  Bye!\n")
            sys.exit(0)
        else:
            print("  Invalid choice.")
            time.sleep(1)
            #