import os
import re
import sys
import time
import json
import random
import logging
import threading

import requests
import spotipy
import syncedlyrics as synced_lib
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

MAX_STATUS_LEN     = 128
RESYNC_EVERY       = 12
END_OF_SONG_WINDOW = 10
END_OF_SONG_POLL   = 3
SEEK_THRESHOLD     = 3.0   # seconds – detect manual seek if drift exceeds this

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
    ("DISCORD_TOKEN",         "Discord User Token"),
]

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def fmt_time(seconds: float) -> str:
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"

EMOJI_WIDE = re.compile(
    "[\U00010000-\U0010ffff"
    "\U0001F300-\U0001F9FF"
    "\U00002600-\U000027BF"
    "\U0000FE00-\U0000FE0F"
    "\U00020000-\U0002A6DF"
    "]+",
    flags=re.UNICODE,
)

def vis_len(s: str) -> int:
    return len(s) + len(EMOJI_WIDE.findall(s))

def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - vis_len(s))

def row(content: str, W: int) -> str:
    return "║ " + pad(content, W - 2) + " ║"

def print_status(song, artist, progress_s, duration_s, current_line, source, discord_ok, paused=False):
    clear_screen()
    W        = 52
    bar_w    = W - 6
    filled   = int((progress_s / max(duration_s, 1)) * bar_w)
    bar      = "█" * filled + "░" * (bar_w - filled)
    prog_str = f"{fmt_time(progress_s)} / {fmt_time(duration_s)}"
    if paused:
        status = "⏸ Paused"
    else:
        status = "✓ Updated" if discord_ok else "✗ Failed"

    div  = "╠" + "═" * W + "╣"
    song_t   = song[:W - 12]   if song   else ""
    artist_t = artist[:W - 12] if artist else ""
    line_t   = current_line[:W - 4] if current_line else "—"

    print("╔" + "═" * W + "╗")
    print(row("🎵  Spotify → Discord Status", W))
    print(div)
    print(row(f"Song    {song_t}", W))
    print(row(f"Artist  {artist_t}", W))
    print(row("", W))
    print(row(f"  [{bar}]", W))
    print(row(f"  {prog_str}", W))
    print(div)
    print(row(f"  {line_t}", W))
    print(div)
    print(row(f"  Source  {source}", W))
    print(row(f"  Discord {status}", W))
    print(div)
    print(row("  Ctrl+C to stop", W))
    print("╚" + "═" * W + "╝")

def main_menu() -> str:
    clear_screen()
    W = 36
    print("╔" + "═" * W + "╗")
    print(row("🎵  Spotify → Discord Status", W))
    print("╠" + "═" * W + "╣")
    print(row("  [1]  Start", W))
    print(row("  [2]  Settings", W))
    print(row("  [3]  Exit", W))
    print("╚" + "═" * W + "╝")
    return input("\n  > ").strip()

def config_menu() -> dict:
    cfg = load_config()
    while True:
        clear_screen()
        W = 52
        print("╔" + "═" * W + "╗")
        print(row("⚙   Settings", W))
        print("╠" + "═" * W + "╣")
        for i, (key, label) in enumerate(FIELDS, 1):
            val_str = mask(cfg.get(key, ""))
            print(row(f"  [{i}]  {label}", W))
            print(row(f"        {val_str}", W))
            print(row("", W))
        print("╠" + "═" * W + "╣")
        print(row("  [A]  Update all", W))
        print(row("  [B]  Back", W))
        print("╚" + "═" * W + "╝")
        choice = input("\n  > ").strip().upper()

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

# ── Discord API ──────────────────────────────────────────────────────────────

_discord_backoff_until = 0.0

def set_discord_status(token: str, text: str, emoji: str = "🎵") -> bool:
    global _discord_backoff_until
    now = time.monotonic()
    if now < _discord_backoff_until:
        log.debug("Discord rate-limited, skipping (%.1fs left)", _discord_backoff_until - now)
        return False
    url     = "https://discord.com/api/v9/users/@me/settings"
    headers = {"Authorization": token, "Content-Type": "application/json"}
    payload = {"custom_status": {"text": text[:MAX_STATUS_LEN], "emoji_name": emoji}}
    try:
        resp = requests.patch(url, json=payload, headers=headers, timeout=10)
        if resp.status_code == 429:
            retry_after = float(resp.json().get("retry_after", 5))
            _discord_backoff_until = time.monotonic() + retry_after
            log.warning("Discord 429 – backing off %.1fs", retry_after)
            return False
        return resp.status_code == 200
    except requests.RequestException as exc:
        log.warning("Discord API error: %s", exc)
        return False

def clear_discord_status(token: str):
    url     = "https://discord.com/api/v9/users/@me/settings"
    headers = {"Authorization": token, "Content-Type": "application/json"}
    try:
        requests.patch(url, json={"custom_status": None}, headers=headers, timeout=10)
    except requests.RequestException:
        pass

# ── Lyrics helpers ───────────────────────────────────────────────────────────

def parse_lrc(lrc_text: str) -> list[tuple[float, str]]:
    pattern = re.compile(r"\[(\d+):(\d+\.\d+)\](.*)")
    result  = []
    for match in pattern.finditer(lrc_text):
        minutes = int(match.group(1))
        seconds = float(match.group(2))
        text    = match.group(3).strip()
        if text:
            result.append((minutes * 60 + seconds, text))
    return sorted(result, key=lambda x: x[0])

_lyrics_cache: dict[str, tuple] = {}
_cache_lock = threading.Lock()

def _lrclib_parse_response(data: dict, duration_s: float) -> list[tuple[float, str]] | None:
    lrc_text = data.get("syncedLyrics") or data.get("plainLyrics")
    if not lrc_text:
        return None
    if data.get("syncedLyrics"):
        return parse_lrc(lrc_text)
    lines    = [l.strip() for l in lrc_text.splitlines() if l.strip()]
    time_per = duration_s / max(len(lines), 1)
    return [(i * time_per, l) for i, l in enumerate(lines)]

def _clean_title(title: str) -> str:
    title = re.sub(r"\(feat\..*?\)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\(ft\..*?\)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\(with.*?\)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\(remaster.*?\)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\(.*?version.*?\)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\[.*?\]", "", title)
    return title.strip()

# ── Primary: syncedlyrics library ────────────────────────────────────────────

def fetch_lyrics_syncedlyrics(artist: str, title: str, duration_s: float) -> tuple[list[tuple[float, str]], bool] | tuple[None, None]:
    """Try syncedlyrics (searches Musixmatch, LrcLib, NetEase, etc.)."""
    clean       = _clean_title(title)
    search_term = f"{artist} {clean}"
    try:
        lrc_text = synced_lib.search(search_term)
        if lrc_text:
            # Check for synced timestamps  [mm:ss.xx]
            if re.search(r"\[\d+:\d+\.\d+\]", lrc_text):
                result = parse_lrc(lrc_text)
                if result:
                    log.info("syncedlyrics matched (synced): %s", search_term)
                    return result, True
            # Plain text fallback (strip any bracket-only lines)
            lines = [l.strip() for l in lrc_text.splitlines()
                     if l.strip() and not re.match(r"^\[.*\]$", l)]
            if lines:
                time_per = duration_s / max(len(lines), 1)
                result   = [(i * time_per, l) for i, l in enumerate(lines)]
                log.info("syncedlyrics matched (plain): %s", search_term)
                return result, False
    except Exception as exc:
        log.warning("syncedlyrics error for '%s': %s", search_term, exc)
    return None, None

# ── Fallback: direct LrcLib API ──────────────────────────────────────────────

def fetch_lyrics_lrclib(artist: str, title: str, duration_s: float) -> tuple[list[tuple[float, str]], bool] | tuple[None, None]:
    headers = {"User-Agent": "SpotifyDiscordStatus/1.0"}
    clean   = _clean_title(title)

    get_attempts = [
        {"artist_name": artist, "track_name": title,  "duration": int(duration_s)},
        {"artist_name": artist, "track_name": title},
        {"artist_name": artist, "track_name": clean,  "duration": int(duration_s)},
        {"artist_name": artist, "track_name": clean},
    ]
    for params in get_attempts:
        try:
            resp = requests.get("https://lrclib.net/api/get", params=params, timeout=8, headers=headers)
            if resp.status_code == 200:
                data   = resp.json()
                synced = bool(data.get("syncedLyrics"))
                result = _lrclib_parse_response(data, duration_s)
                if result:
                    log.info("LrcLib /get matched %s | synced=%s", params, synced)
                    return result, synced
        except Exception as exc:
            log.warning("LrcLib /get %s error: %s", params, exc)

    search_attempts = [
        {"artist_name": artist, "track_name": title},
        {"artist_name": artist, "track_name": clean},
        {"q": f"{artist} {clean}"},
    ]
    for params in search_attempts:
        try:
            resp = requests.get("https://lrclib.net/api/search", params=params, timeout=8, headers=headers)
            if resp.status_code == 200:
                hits = resp.json()
                if isinstance(hits, list):
                    for hit in hits[:3]:
                        synced = bool(hit.get("syncedLyrics"))
                        result = _lrclib_parse_response(hit, duration_s)
                        if result:
                            log.info("LrcLib /search matched %s | synced=%s", params, synced)
                            return result, synced
        except Exception as exc:
            log.warning("LrcLib /search %s error: %s", params, exc)

    return None, None

# ── Cache layer ──────────────────────────────────────────────────────────────

def fetch_and_cache(track_id: str, artist: str, title: str, duration_s: float):
    with _cache_lock:
        if track_id in _lyrics_cache:
            return

    # 1) Primary: syncedlyrics (handles multiple providers internally)
    result, synced = fetch_lyrics_syncedlyrics(artist, title, duration_s)
    if result and synced:
        src, raw_lines = "syncedlyrics (synced)", None
    elif result:
        src, raw_lines = "syncedlyrics (plain)", None
    else:
        # 2) Fallback: direct LrcLib API
        log.info("syncedlyrics miss → falling back to LrcLib: %s – %s", artist, title)
        result, synced = fetch_lyrics_lrclib(artist, title, duration_s)
        if result and synced:
            src, raw_lines = "LrcLib (synced)", None
        elif result:
            src, raw_lines = "LrcLib (plain)", None
        else:
            src, raw_lines, result = "None (track name only)", None, None

    mood = detect_mood(result if result else []) if result else "neutral"

    with _cache_lock:
        _lyrics_cache[track_id] = (result, src, mood, raw_lines)
        if len(_lyrics_cache) > 10:
            oldest = next(iter(_lyrics_cache))
            del _lyrics_cache[oldest]

    log.info("Cached: %s – %s | Source: %s", artist, title, src)

# ── Mood + helpers ───────────────────────────────────────────────────────────

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


# ── Main loop ────────────────────────────────────────────────────────────────

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
            scope="user-read-currently-playing user-read-playback-state user-read-recently-played",
        )
        sp = spotipy.Spotify(auth_manager=auth)
    except Exception as exc:
        print(f"\n  ✗ Spotify auth failed: {exc}")
        time.sleep(3)
        return

    discord_token = cfg["DISCORD_TOKEN"]

    last_track_id       = None
    timed_lines         = []
    lyrics_source       = "—"
    mood                = "neutral"
    current_idx         = -1
    last_resync         = 0.0
    anchor_pos_s        = 0.0
    anchor_time         = time.monotonic()
    discord_ok          = False
    last_drift_check    = 0.0
    next_drift_interval = random.uniform(15, 20)
    plain_raw           = None
    is_paused           = False

    ui_song       = ""
    ui_artist     = ""
    ui_duration_s = 0.0
    ui_line       = ""

    def do_spotify_poll() -> dict | None:
        try:
            return sp.current_playback()
        except Exception as exc:
            log.error("Spotify poll: %s", exc)
            return None

    def anchor_plain(raw_lines: list[str], duration_s: float, pos_s: float) -> list[tuple[float, str]]:
        if not raw_lines:
            return []
        time_per  = duration_s / len(raw_lines)
        timed     = [(i * time_per, l) for i, l in enumerate(raw_lines)]
        start_idx = line_index_at(timed, pos_s)
        offset    = pos_s - timed[start_idx][0]
        return [(t + offset, l) for t, l in timed]

    def load_new_track(track_id, track_name, artist_name, duration_s, start_pos_s):
        nonlocal ui_line

        with _cache_lock:
            cached = _lyrics_cache.get(track_id)

        if cached:
            result, src, m, raw_lines = cached
            src = f"{src} (prefetched)"
            log.info("Cache hit: %s – %s", artist_name, track_name)
        else:
            ui_line = "Fetching lyrics..."
            print(f"  ♫  {track_name} — {artist_name}")
            print("  Fetching lyrics...")
            t = threading.Thread(
                target=fetch_and_cache,
                args=(track_id, artist_name, track_name, duration_s),
                daemon=True,
            )
            t.start()
            t.join(timeout=20)
            with _cache_lock:
                result, src, m, raw_lines = _lyrics_cache.get(track_id, (None, "None (track name only)", "neutral", None))

        if result is None and raw_lines:
            result = anchor_plain(raw_lines, duration_s, start_pos_s)
            src    = f"{src} (anchored)"

        if not result:
            result = []

        start_idx = line_index_at(result, start_pos_s) if result else -1
        log.info(
            "Now playing: %s – %s | Source: %s | Lines: %d | Mood: %s | Starting at line %d (%.1fs)",
            artist_name, track_name, src, len(result), m, start_idx, start_pos_s,
        )
        return result, src, m, start_idx, raw_lines

    def prefetch_queue():
        try:
            queue_data = sp.queue()
            if not queue_data:
                return
            upcoming = queue_data.get("queue", [])
            if not upcoming:
                return
            next_track = upcoming[0]
            nid     = next_track["id"]
            nname   = next_track["name"]
            nartist = next_track["artists"][0]["name"]
            ndur    = next_track["duration_ms"] / 1000

            with _cache_lock:
                already = nid in _lyrics_cache
            if not already:
                log.info("Prefetching next track: %s – %s", nartist, nname)
                t = threading.Thread(
                    target=fetch_and_cache,
                    args=(nid, nartist, nname, ndur),
                    daemon=True,
                )
                t.start()
        except Exception as exc:
            log.warning("Prefetch queue error: %s", exc)

    try:
        while True:
            # Freeze position while paused so it doesn't drift forward
            pos_s = anchor_pos_s if is_paused else anchor_pos_s + (time.monotonic() - anchor_time)

            near_end      = ui_duration_s > 0 and pos_s >= ui_duration_s - END_OF_SONG_WINDOW
            poll_interval = END_OF_SONG_POLL if near_end else random.uniform(10, 15)
            need_poll     = last_track_id is None or (time.monotonic() - last_resync) >= poll_interval

            if need_poll:
                playback = do_spotify_poll()

                if playback is None:
                    time.sleep(5)
                    continue

                if not playback or not playback.get("is_playing"):
                    if not playback or not playback.get("item"):
                        # ── Nothing playing at all ──
                        if last_track_id is not None:
                            clear_discord_status(discord_token)
                            last_track_id = None
                            timed_lines   = []
                            current_idx   = -1
                            ui_line       = "— nothing playing —"
                            ui_duration_s = 0.0
                            is_paused     = False
                        clear_screen()
                        print("  ⏸  Nothing playing. Waiting for next track...")
                    else:
                        # ── Track exists but is paused — freeze position ──
                        if not is_paused:
                            is_paused    = True
                            anchor_pos_s = playback["progress_ms"] / 1000
                            anchor_time  = time.monotonic()
                            pause_line   = f"⏸ {ui_song} — {ui_artist}" if ui_song else "⏸ Paused"
                            discord_ok   = set_discord_status(discord_token, pause_line, "⏸")
                            log.info("Paused at %.1fs", anchor_pos_s)
                        print_status(
                            song=ui_song, artist=ui_artist,
                            progress_s=anchor_pos_s, duration_s=ui_duration_s,
                            current_line=ui_line, source=lyrics_source,
                            discord_ok=discord_ok, paused=True,
                        )
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

                # ── Resume from pause — re-anchor position ──
                if is_paused:
                    is_paused    = False
                    anchor_pos_s = progress_ms / 1000
                    anchor_time  = time.monotonic()
                    pos_s        = anchor_pos_s
                    log.info("Resumed at %.1fs", anchor_pos_s)

                if track_id != last_track_id:
                    last_track_id       = track_id
                    last_drift_check    = time.monotonic()
                    next_drift_interval = random.uniform(15, 20)

                    start_pos_s = progress_ms / 1000
                    timed_lines, lyrics_source, mood, current_idx, plain_raw = load_new_track(
                        track_id, track_name, artist_name, duration_s, start_pos_s
                    )

                    if timed_lines and current_idx >= 0:
                        _, line    = timed_lines[current_idx]
                        ui_line    = line
                        emoji      = random.choice(MOOD_EMOJI.get(mood, MOOD_EMOJI["neutral"]))
                        discord_ok = set_discord_status(discord_token, line, emoji)
                        log.info("Started at line %d: %s", current_idx, line)
                    elif not timed_lines:
                        fallback    = f"{track_name} — {artist_name}"
                        ui_line     = fallback
                        discord_ok  = set_discord_status(discord_token, fallback)
                        current_idx = 0

                    prefetch_queue()

                else:
                    if plain_raw:
                        real_pos_s  = progress_ms / 1000
                        timed_lines = anchor_plain(plain_raw, duration_s, real_pos_s)
                        new_idx     = line_index_at(timed_lines, real_pos_s)
                        if new_idx != current_idx:
                            current_idx = new_idx
                            _, line     = timed_lines[current_idx]
                            ui_line     = line
                            emoji       = random.choice(MOOD_EMOJI.get(mood, MOOD_EMOJI["neutral"]))
                            discord_ok  = set_discord_status(discord_token, line, emoji)
                            log.info("Plain re-anchored at %.1fs → line %d: %s", real_pos_s, current_idx, line)

                # ── Seek detection ──
                spotify_pos = progress_ms / 1000
                estimated   = anchor_pos_s + (time.monotonic() - anchor_time)
                if abs(spotify_pos - estimated) > SEEK_THRESHOLD and timed_lines:
                    new_idx = line_index_at(timed_lines, spotify_pos)
                    if new_idx != current_idx:
                        current_idx = new_idx
                        _, line     = timed_lines[current_idx]
                        ui_line     = line
                        emoji       = random.choice(MOOD_EMOJI.get(mood, MOOD_EMOJI["neutral"]))
                        discord_ok  = set_discord_status(discord_token, line, emoji)
                        log.info("Seek detected: %.1fs → %.1fs, line %d: %s",
                                 estimated, spotify_pos, current_idx, line)

                anchor_pos_s = spotify_pos
                anchor_time  = time.monotonic()
                last_resync  = time.monotonic()
                pos_s        = anchor_pos_s

            # ── Periodic drift correction ──
            if (timed_lines
                    and current_idx >= 0
                    and (time.monotonic() - last_drift_check) >= next_drift_interval):

                expected_idx = line_index_at(timed_lines, pos_s)
                if expected_idx != current_idx:
                    log.info("Drift corrected: line %d → %d at %.1fs", current_idx, expected_idx, pos_s)
                    current_idx = expected_idx
                    _, line     = timed_lines[current_idx]
                    ui_line     = line
                    emoji       = random.choice(MOOD_EMOJI.get(mood, MOOD_EMOJI["neutral"]))
                    discord_ok  = set_discord_status(discord_token, line, emoji)

                last_drift_check    = time.monotonic()
                next_drift_interval = random.uniform(15, 20)

            # ── Advance to next lyric line ──
            if timed_lines:
                new_idx = line_index_at(timed_lines, pos_s)
                if new_idx != current_idx:
                    current_idx = new_idx
                    _, line     = timed_lines[current_idx]
                    ui_line     = line
                    emoji       = random.choice(MOOD_EMOJI.get(mood, MOOD_EMOJI["neutral"]))
                    discord_ok  = set_discord_status(discord_token, line, emoji)
                    log.info("Line %d: %s", current_idx, line)

            print_status(
                song         = ui_song,
                artist       = ui_artist,
                progress_s   = pos_s,
                duration_s   = ui_duration_s,
                current_line = ui_line,
                source       = lyrics_source,
                discord_ok   = discord_ok,
            )

            # ── Calculate sleep until next event ──
            pos_s      = anchor_pos_s if is_paused else anchor_pos_s + (time.monotonic() - anchor_time)
            candidates = []

            if timed_lines and current_idx + 1 < len(timed_lines):
                next_line_in = timed_lines[current_idx + 1][0] - pos_s
                if next_line_in > 0:
                    candidates.append(next_line_in)

            next_poll_in  = poll_interval - (time.monotonic() - last_resync)
            next_drift_in = next_drift_interval - (time.monotonic() - last_drift_check)
            candidates.append(max(0.1, next_poll_in))
            candidates.append(max(0.1, next_drift_in))

            sleep_s = min(candidates) if candidates else 2.0
            sleep_s = min(sleep_s, 5.0)  # Cap at 5s to catch manual skips fast
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
