import os
import re
import sys
import time
import json
import queue
import random
import logging
import threading
import textwrap
import unicodedata

import io
import requests
import spotipy
try:
    from PIL import Image
except ImportError:
    Image = None
import syncedlyrics as synced_lib
from spotipy.oauth2 import SpotifyOAuth

def get_appdata_dir() -> str:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    path = os.path.join(appdata, "SpotifyDiscordStatus")
    os.makedirs(path, exist_ok=True)
    return path

def config_path() -> str:
    return os.path.join(get_appdata_dir(), "config.json")

def load_config() -> dict:
    try:
        with open(config_path(), "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_config(cfg: dict):
    with open(config_path(), "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"  Saved to {config_path()}")

logging.basicConfig(
    filename=os.path.join(get_appdata_dir(), "spotify_discord.log"),
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
log = logging.getLogger(__name__)




MAX_STATUS_LEN     = 128
RESYNC_EVERY       = 5.0
END_OF_SONG_WINDOW = 10
END_OF_SONG_POLL   = 3
SEEK_THRESHOLD     = 3.0

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
    sys.stdout.write("\033[H\033[J")
    sys.stdout.flush()

def fmt_time(seconds: float) -> str:
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"

def display_len(s: str) -> int:
    if not s:
        return 0
    w = 0
    for c in s:
        if unicodedata.category(c) == 'Mn' or c in ('\u200d', '\ufe0f'):
            continue
        if unicodedata.east_asian_width(c) in ('W', 'F', 'A') or (0x1f000 <= ord(c) <= 0x1faff) or (0x2600 <= ord(c) <= 0x27bf):
            w += 2
        else:
            w += 1
    return w

def trim_display(s: str, width: int) -> str:
    s = s or ""
    if display_len(s) <= width:
        return s
    limit = max(0, width - 3)
    trimmed = ""
    for c in s:
        if display_len(trimmed + c) > limit:
            break
        trimmed += c
    return trimmed + "..."

def pad(s: str, width: int) -> str:
    s = trim_display(s, width)
    return s + " " * max(0, width - display_len(s))

def row(content: str, W: int) -> str:
    return "| " + pad(content, W - 2) + " |"

def border(W: int, char: str = "-") -> str:
    return "+" + char * W + "+"

def art_row(art_line: str, art_width: int, W: int) -> str:
    inner     = W - 2
    total_pad = max(0, inner - art_width)
    left_pad  = total_pad // 2
    right_pad = total_pad - left_pad
    return "| " + " " * left_pad + art_line + " " * right_pad + " |"

PIKES = [
    ("Cunning Pike", r"""
  |
-O_O-
"""),
    ("King Cunning Pike", r"""
  W
-O_O-
"""),
    ("Queen Cunning Pike", r"""
  +
-O_O-
"""),
    ("Cunning Pike Winking", r"""
  |
-O_--
"""),
    ("Cunning Pike Sleeping", r"""
   |
- -_- -
"""),
    ("Cow Trying To Be A Cunning Pike", r"""
 (  )
--OO--
"""),
    ("Cunning Pike Hiding Behind Weeds", r"""
} { }
{{ }_O-
 }{
 {
"""),
    ("Shocked Cunning Pike", r"""
  |
-O O-
  o
"""),
    ("Cunning Pike With Necklace", r"""
  |
-O_O-
 \o/
"""),
    ("Cunning Cyclops Pike", r"""
   |
--(O)--
"""),
    ("Cunning Pike Having Seen Nuclear Sub", r"""
  |
-@_@-
"""),
    ("Cunning Pike Having Taken Something Illegal", r"""
  |
-X X-
  ~
"""),
    ("Vibrating Cunning Pike", r"""
  |  |   |   |  |
-( -(  -O_O-  )- )-
"""),
    ("Synchronised Swimming Pikes", r"""
      |
    -O_O-
  |       |
-O_O-   -O_O-
      |
    -O_O-
"""),
    ("Cunning Pike With Monocle", r"""
  |
-O_q-
"""),
    ("Cunning Pike On Skateboard", r"""
 --      |
  --   -O_O-
--   ==========
      O      O
"""),
    ("Limbo Dancing Cunning Pike", r"""
____|____
| -O_O- |
"""),
    ("Acrobatic Cunning Pikes", r"""
__________________
|     |    |     |
|  |  |    |     |
|-O_O-|    |     |
           |  |  |
           |-O_O-|
"""),
    ("Cunning Pike On Stilts", r"""
  |
-O_O-
 | |
 | |
 | |
 | |
 ^ ^
"""),
    ("Cunning Pike Doing The High Dive", r"""
       --------------|
    |   |            |
                     |
      |              |
    -O_O-            |
~~~~~~~~~~~~~~~~~~~~~
"""),
    ("A School Of Cunning Pikes", r"""
     ____     ___________
      |  *   |  Pikes R  |
    -O_O---  | Cunning ! |
             |___________|
   |     |     |
 -(|)- -(|)- -(|)-
"""),
    ("Cunning Pike Wallpaper", r"""
  _   _   _   _
-O_O-O_O-O_O-O_O-
-O_O-O_O-O_O-O_O-
-O_O-O_O-O_O-O_O-
"""),
    ("James Pond", r"""
  |
-0_0-7
"""),
    ("Cunning Pike Pole Vaulting", r"""
         /|
  |     | |
-O_O-|  | |
     |  | |
     |  | |_______________
     |  |_/_____________/ |
     |  |_______________|/
"""),
    ("Cunning Pike Disguised As Spider", r"""
  |
  |
\ | /
-O_O-
/ | \
"""),
]


def clean_art_lines(art: str) -> list[str]:
    return [line.rstrip() for line in art.strip("\n").splitlines()]


def pike_index(seed: str) -> int:
    seed = seed or "idle"
    return sum(ord(ch) for ch in seed) % len(PIKES)


def pike_panel(seed: str, speech: str, listening: bool) -> list[str]:
    name, art = PIKES[pike_index(seed)]
    bubble_width = 34
    if not listening:
        speech = "hi" if int(time.time() / 4) % 2 else "hey"
    words = textwrap.wrap(
        speech or "hi",
        width=bubble_width,
        break_long_words=False,
        break_on_hyphens=False,
    )[:3] or ["hi"]

    lines = ["." + "-" * (bubble_width + 2) + "."]
    lines.extend("| " + pad(line, bubble_width) + " |" for line in words)
    lines.append("'" + "-" * (bubble_width + 2) + "'")
    lines.append("        /")
    lines.extend("  " + line for line in clean_art_lines(art))
    return lines

def print_status(song, artist, progress_s, duration_s, current_line, source, discord_ok, paused=False, album_art_lines=None):
    clear_screen()
    W        = 58
    bar_w    = W - 10
    ratio    = min(max(progress_s / max(duration_s, 1), 0.0), 1.0)
    filled   = int(ratio * bar_w)
    bar      = "#" * filled + "." * (bar_w - filled)
    prog_str = f"{fmt_time(progress_s)} / {fmt_time(duration_s)}"
    status   = "Paused" if paused else ("Updated" if discord_ok else "Failed")

    song_t   = trim_display(song, W - 12) if song else ""
    artist_t = trim_display(artist, W - 12) if artist else ""
    line_t   = trim_display(current_line, W - 4) if current_line else "-"
    source_t = trim_display(source, W - 12) if source else "-"

    gui_lines = [
        border(W, "="),
        row("Spotify -> Discord Status", W),
        border(W),
    ]
    if album_art_lines:
        gui_lines.extend(art_row(line, _ART_W, W) for line in album_art_lines)
        gui_lines.append(row("", W))
    gui_lines.extend([
        row(f"Song    {song_t}", W),
        row(f"Artist  {artist_t}", W),
        row("", W),
        row(f"[{bar}]", W),
        row(f"{prog_str}", W),
        border(W),
        row(line_t, W),
        border(W),
        row(f"Source  {source_t}", W),
        row(f"Discord {status}", W),
        border(W),
        row("Ctrl+C to stop", W),
        border(W, "="),
    ])

    listening = bool(song)
    speech = current_line if listening else ""
    seed = f"{song}|{artist}"
    right_lines = pike_panel(seed, speech, listening)

    max_len = max(len(gui_lines), len(right_lines))
    for i in range(max_len):
        left = gui_lines[i] if i < len(gui_lines) else " " * (W + 2)
        right = right_lines[i] if i < len(right_lines) else ""
        sys.stdout.write(f"{left}   {right}\033[K\n")
    sys.stdout.write("\033[J")
    sys.stdout.flush()


def main_menu() -> str:
    sys.stdout.write("\033[H\033[J")
    sys.stdout.flush()
    W = 38
    gui_lines = [
        border(W, "="),
        row("Spotify -> Discord Status", W),
        border(W),
        row("[1] Start", W),
        row("[2] Settings", W),
        row("[3] Exit", W),
        border(W, "="),
    ]
    right_lines = pike_panel("menu", "pick an option!", True)
    max_len = max(len(gui_lines), len(right_lines))
    for i in range(max_len):
        left  = gui_lines[i] if i < len(gui_lines) else " " * (W + 2)
        right = right_lines[i] if i < len(right_lines) else ""
        sys.stdout.write(f"{left}   {right}\033[K\n")
    sys.stdout.write("\033[J")
    sys.stdout.flush()
    return input("\n  > ").strip()


def config_menu() -> dict:
    cfg = load_config()
    while True:
        sys.stdout.write("\033[H\033[J")
        sys.stdout.flush()
        W = 58
        print(border(W, "="))
        print(row("Settings", W))
        print(border(W))
        for i, (key, label) in enumerate(FIELDS, 1):
            val_str = mask(cfg.get(key, ""))
            print(row(f"[{i}] {label}", W))
            print(row(f"    {val_str}", W))
            print(row("", W))
        print(border(W))
        print(row("[A] Update all", W))
        print(row("[B] Back", W))
        print(border(W, "="))
        choice = input("\n  > ").strip().upper()

        if choice == "B":
            break
        elif choice == "A":
            print("\n  Press Enter to keep current value.\n")
            for key, label in FIELDS:
                current = cfg.get(key, "")
                if key == "SPOTIPY_REDIRECT_URI" and not current:
                    current = "http://127.0.0.1:23435/callback"
                hint = f" [{mask(current)}]" if current else ""
                val = input(f"  {label}{hint}\n  > ").strip()
                if val:
                    cfg[key] = val
                elif not cfg.get(key) and key == "SPOTIPY_REDIRECT_URI":
                    cfg[key] = "http://127.0.0.1:23435/callback"
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
# Discord API

_discord_backoff_until = 0.0
_discord_queue = queue.Queue()
_discord_thread_started = False
_discord_thread_lock = threading.Lock()
_discord_last_status_ok = threading.Event()
_discord_last_status_ok.set()

def _discord_worker():
    global _discord_backoff_until
    last_update = 0.0
    pending = None
    
    while True:
        try:
            timeout = None
            if pending is not None:
                now = time.monotonic()
                time_since_last = now - last_update
                remaining = max(0.0, 1.5 - time_since_last)
                if now < _discord_backoff_until:
                    remaining = max(remaining, _discord_backoff_until - now)
                timeout = max(0.01, remaining)
                
            item = _discord_queue.get(timeout=timeout)
            if item is None: # sentinel to stop
                break
            pending = item
        except queue.Empty:
            pass
            
        if pending is not None:
            now = time.monotonic()
            time_since_last = now - last_update
            if time_since_last >= 1.5 and now >= _discord_backoff_until:
                token, text, emoji = pending
                url     = "https://discord.com/api/v9/users/@me/settings"
                headers = {"Authorization": token, "Content-Type": "application/json"}
                payload = {"custom_status": {"text": text[:MAX_STATUS_LEN], "emoji_name": emoji} if text else None}
                try:
                    resp = requests.patch(url, json=payload, headers=headers, timeout=10)
                    last_update = time.monotonic()
                    if resp.status_code == 429:
                        retry_after = float(resp.json().get("retry_after", 5))
                        _discord_backoff_until = time.monotonic() + retry_after
                        _discord_last_status_ok.clear()
                        log.warning("Discord 429 in background – backing off %.1fs", retry_after)
                    elif resp.status_code == 200:
                        _discord_last_status_ok.set()
                        pending = None
                    else:
                        _discord_last_status_ok.clear()
                        pending = None
                except requests.RequestException as exc:
                    log.warning("Discord API background error: %s", exc)
                    _discord_last_status_ok.clear()
                    _discord_backoff_until = time.monotonic() + 2.0

def set_discord_status(token: str, text: str, emoji: str = "🎵") -> bool:
    global _discord_thread_started
    with _discord_thread_lock:
        if not _discord_thread_started:
            t = threading.Thread(target=_discord_worker, daemon=True)
            t.start()
            _discord_thread_started = True
            
    while not _discord_queue.empty():
        try:
            _discord_queue.get_nowait()
        except queue.Empty:
            break
            
    _discord_queue.put((token, text, emoji))
    return _discord_last_status_ok.is_set()

def clear_discord_status(token: str):
    while not _discord_queue.empty():
        try:
            _discord_queue.get_nowait()
        except queue.Empty:
            break
    _discord_queue.put((token, "", ""))
    url     = "https://discord.com/api/v9/users/@me/settings"
    headers = {"Authorization": token, "Content-Type": "application/json"}
    try:
        requests.patch(url, json={"custom_status": None}, headers=headers, timeout=10)
    except requests.RequestException:
        pass


# ── Album Art ASCII Cache & Helper ───────────────────────────────────────────

_album_art_cache = {}
_album_art_lock = threading.Lock()

# Width in characters for album art.
# Terminal cells are ~2× taller than wide, so h_px = _ART_W gives a visually square cube:
#   22 chars wide × 11 rows tall  →  22×cell_w : 11×cell_h ≈ square
_ART_W = 22

def fetch_album_art_ascii(url: str) -> list[str]:
    """
    Downloads album art and renders it using the Unicode half-block technique.
    Each character cell encodes 2 vertical pixels:
      - Top pixel  → ANSI background color  (48;2;r;g;b)
      - Bottom pixel → ANSI foreground color (38;2;r;g;b)  drawn as ▄
    This gives _ART_W × _ART_W effective pixel resolution with
    perfect single-character-wide pixel columns — no gaps, no misalignment.
    """
    if not url:
        return []
    if Image is None:
        return []

    with _album_art_lock:
        if url in _album_art_cache:
            _album_art_cache[url] = _album_art_cache.pop(url)  # LRU bump
            return _album_art_cache[url]

    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code != 200:
            return []

        h_px = _ART_W              # h_px = _ART_W → _ART_W/2 rows → visually square
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        img = img.resize((_ART_W, h_px), Image.Resampling.LANCZOS)
        px  = img.load()

        lines = []
        for row in range(0, h_px, 2):
            parts = []
            for col in range(_ART_W):
                rt, gt, bt = px[col, row]          # top pixel
                rb, gb, bb = px[col, row + 1]      # bottom pixel
                parts.append(
                    f"\033[48;2;{rt};{gt};{bt}m"   # bg = top colour
                    f"\033[38;2;{rb};{gb};{bb}m"   # fg = bottom colour
                    "▄"                             # lower-half block
                )
            lines.append("".join(parts) + "\033[0m")

        with _album_art_lock:
            _album_art_cache[url] = lines
            if len(_album_art_cache) > 20:
                del _album_art_cache[next(iter(_album_art_cache))]
        return lines
    except Exception as exc:
        log.warning("Failed to generate album art ASCII: %s", exc)
        return []

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
    """Remove Spotify metadata that usually hurts lyric-provider matching."""
    title = title or ""

    # Drop bracketed tags like "(feat. X)", "[Remastered 2011]", or "(Radio Edit)".
    junk_words = (
        r"feat\.?|ft\.?|featuring|with|remaster(?:ed)?|"
        r"version|edit|mix|mono|stereo|deluxe|bonus|explicit|clean|"
        r"radio|single|album|live|acoustic|instrumental|sped up|slowed"
    )
    title = re.sub(rf"\s*[\(\[].*?(?:{junk_words}).*?[\)\]]", "", title, flags=re.IGNORECASE)

    # Drop suffix tags like "- Remastered 2011" or "– Radio Edit".
    title = re.sub(rf"\s*[-–—]\s*(?:\d{{4}}\s*)?(?:{junk_words}).*$", "", title, flags=re.IGNORECASE)

    # Drop trailing "feat. X" when Spotify puts it outside parentheses.
    title = re.sub(r"\s+(?:feat\.?|ft\.?|featuring)\s+.+$", "", title, flags=re.IGNORECASE)

    # Normalize leftover whitespace and empty brackets.
    title = re.sub(r"\s*[\(\[]\s*[\)\]]", "", title)
    title = re.sub(r"\s{2,}", " ", title)
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
        if track_id in _lyrics_cache and _lyrics_cache[track_id][1] != "Fetching...":
            return

    # 1) Primary: syncedlyrics (handles multiple providers internally)
    result, synced = fetch_lyrics_syncedlyrics(artist, title, duration_s)
    if result and synced:
        src, raw_lines = "syncedlyrics (synced)", None
    elif result:
        src, raw_lines = "syncedlyrics (plain)", [line for _, line in result]
        result = None
    else:
        # 2) Fallback: direct LrcLib API
        log.info("syncedlyrics miss → falling back to LrcLib: %s – %s", artist, title)
        result, synced = fetch_lyrics_lrclib(artist, title, duration_s)
        if result and synced:
            src, raw_lines = "LrcLib (synced)", None
        elif result:
            src, raw_lines = "LrcLib (plain)", [line for _, line in result]
            result = None
        else:
            src, raw_lines, result = "None (track name only)", None, None

    mood = detect_mood(result if result else []) if result else "neutral"

    with _cache_lock:
        _lyrics_cache[track_id] = (result, src, mood, raw_lines)
        if len(_lyrics_cache) > 50:
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
    idx = -1
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
        print(f"\n  Missing: {', '.join(missing)}")
        print("    Go to Settings [2] to fill them in.")
        time.sleep(3)
        return

    redirect = cfg.get("SPOTIPY_REDIRECT_URI") or "http://127.0.0.1:23435/callback"
    try:
        auth = SpotifyOAuth(
            client_id=cfg["SPOTIPY_CLIENT_ID"],
            client_secret=cfg["SPOTIPY_CLIENT_SECRET"],
            redirect_uri=redirect,
            scope="user-read-currently-playing user-read-playback-state",
            cache_path=os.path.join(get_appdata_dir(), ".cache"),
        )
        sp = spotipy.Spotify(auth_manager=auth)
    except Exception as exc:
        print(f"\n  Spotify auth failed: {exc}")
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
    discord_ok          = True
    last_drift_check    = 0.0
    next_drift_interval = random.uniform(15, 20)
    plain_raw           = None
    is_paused           = False
    prefetched_this_track = False
    album_art_lines       = []

    def load_art_async(url, track_id):
        nonlocal album_art_lines
        art = fetch_album_art_ascii(url) if url else []
        if track_id == last_track_id:
            album_art_lines = art

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
        if start_idx < 0:
            start_idx = 0
        offset    = pos_s - timed[start_idx][0]
        return [(t + offset, l) for t, l in timed]

    def load_new_track(track_id, track_name, artist_name, duration_s, start_pos_s):
        nonlocal ui_line

        with _cache_lock:
            cached = _lyrics_cache.get(track_id)
            ready  = cached is not None and cached[1] != "Fetching..."
            if ready:
                _lyrics_cache[track_id] = _lyrics_cache.pop(track_id)

        if ready:
            result, src, m, raw_lines = cached
            src = f"{src} (prefetched)"
            log.info("Cache hit: %s – %s", artist_name, track_name)
        else:
            ui_line = "Fetching lyrics..."
            print(f"  Now playing: {track_name} - {artist_name}")
            print("  Fetching lyrics...")
            if cached is None:
                with _cache_lock:
                    _lyrics_cache[track_id] = (None, "Fetching...", "neutral", None)
                t = threading.Thread(
                    target=fetch_and_cache,
                    args=(track_id, artist_name, track_name, duration_s),
                    daemon=True,
                )
                t.start()
            result, src, m, raw_lines = None, "Fetching...", "neutral", None

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
            nid     = next_track.get("id")
            if not nid:
                return
            nname   = next_track["name"]
            nartist = next_track["artists"][0]["name"]
            ndur    = next_track["duration_ms"] / 1000

            with _cache_lock:
                already = nid in _lyrics_cache
                if not already:
                    _lyrics_cache[nid] = (None, "Fetching...", "neutral", None)
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

            # ── Check if background lyric fetch completed ──
            if lyrics_source == "Fetching..." and last_track_id is not None:
                with _cache_lock:
                    cached = _lyrics_cache.get(last_track_id)
                if cached and cached[1] != "Fetching...":
                    result, src, m, raw_lines = cached
                    if result is None and raw_lines:
                        result = anchor_plain(raw_lines, ui_duration_s, pos_s)
                        src    = f"{src} (anchored)"
                    if not result:
                        result = []
                    
                    timed_lines   = result
                    lyrics_source = src
                    mood          = m
                    plain_raw     = raw_lines
                    current_idx   = line_index_at(timed_lines, pos_s) if timed_lines else -1
                    
                    if timed_lines and current_idx >= 0:
                        _, line    = timed_lines[current_idx]
                        ui_line    = line
                        emoji      = random.choice(MOOD_EMOJI.get(mood, MOOD_EMOJI["neutral"]))
                        discord_ok = set_discord_status(discord_token, line, emoji)
                        log.info("Loaded background lyrics: line %d: %s", current_idx, line)
                    elif timed_lines and current_idx == -1:
                        intro_status = f"🎵 {ui_song} — {ui_artist}"
                        ui_line      = "Instrumental / Intro"
                        discord_ok   = set_discord_status(discord_token, intro_status)
                        log.info("Loaded background lyrics (intro): %s", intro_status)
                    else:
                        fallback    = f"{ui_song} — {ui_artist}"
                        ui_line     = fallback
                        discord_ok  = set_discord_status(discord_token, fallback)
                        current_idx = 0

            near_end      = ui_duration_s > 0 and pos_s >= ui_duration_s - END_OF_SONG_WINDOW
            is_idle       = last_track_id is None or is_paused
            poll_interval = END_OF_SONG_POLL if (near_end or is_idle) else RESYNC_EVERY
            need_poll     = last_track_id is None or (time.monotonic() - last_resync) >= poll_interval

            if need_poll:
                playback = do_spotify_poll()

                if playback is None:
                    time.sleep(2)
                    continue

                if not playback or not playback.get("is_playing"):
                    if not playback or not playback.get("item"):
                        # ── Nothing playing at all ──
                        if last_track_id is not None:
                            clear_discord_status(discord_token)
                            last_track_id = None
                            timed_lines   = []
                            current_idx   = -1
                            ui_duration_s = 0.0
                            is_paused     = False
                        ui_song       = ""
                        ui_artist     = ""
                        ui_line       = "Nothing playing"
                        lyrics_source = "Idle"
                        album_art_lines = []
                        print_status(
                            song="", artist="",
                            progress_s=0.0, duration_s=0.0,
                            current_line=ui_line, source=lyrics_source,
                            discord_ok=discord_ok, paused=False,
                            album_art_lines=album_art_lines,
                        )
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
                            album_art_lines=album_art_lines,
                        )
                    last_resync = time.monotonic()
                    time.sleep(2)
                    continue

                item        = playback["item"]
                track_id    = item["id"]
                track_name  = item["name"]
                artists     = item.get("artists")
                artist_name = artists[0]["name"] if artists else item.get("show", {}).get("name", "Unknown")
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
                    prefetched_this_track = False

                    images = item.get("album", {}).get("images", [])
                    art_url = images[-1]["url"] if images else None
                    album_art_lines = []
                    if art_url:
                        threading.Thread(target=load_art_async, args=(art_url, track_id), daemon=True).start()

                    start_pos_s = progress_ms / 1000
                    fetch_start_time = time.monotonic()
                    timed_lines, lyrics_source, mood, current_idx, plain_raw = load_new_track(
                        track_id, track_name, artist_name, duration_s, start_pos_s
                    )
                    time_spent = time.monotonic() - fetch_start_time
                    anchor_pos_s = start_pos_s + time_spent
                    anchor_time  = time.monotonic()
                    pos_s        = anchor_pos_s

                    if timed_lines:
                        current_idx = line_index_at(timed_lines, pos_s)
                        if current_idx >= 0:
                            _, line    = timed_lines[current_idx]
                            ui_line    = line
                            emoji      = random.choice(MOOD_EMOJI.get(mood, MOOD_EMOJI["neutral"]))
                            discord_ok = set_discord_status(discord_token, line, emoji)
                            log.info("Started at line %d: %s", current_idx, line)
                        else:
                            intro_status = f"🎵 {track_name} — {artist_name}"
                            ui_line      = "Instrumental / Intro"
                            discord_ok   = set_discord_status(discord_token, intro_status)
                            log.info("Started in intro: %s", intro_status)
                    else:
                        fallback    = f"{track_name} — {artist_name}"
                        ui_line     = fallback
                        discord_ok  = set_discord_status(discord_token, fallback)
                        current_idx = 0

                # ── Seek detection ──
                spotify_pos = progress_ms / 1000
                estimated   = anchor_pos_s + (time.monotonic() - anchor_time)
                if abs(spotify_pos - estimated) > SEEK_THRESHOLD and timed_lines:
                    if plain_raw:
                        timed_lines = anchor_plain(plain_raw, duration_s, spotify_pos)
                    new_idx = line_index_at(timed_lines, spotify_pos)
                    if new_idx != current_idx:
                        current_idx = new_idx
                        if current_idx >= 0:
                            _, line     = timed_lines[current_idx]
                            ui_line     = line
                            emoji       = random.choice(MOOD_EMOJI.get(mood, MOOD_EMOJI["neutral"]))
                            discord_ok  = set_discord_status(discord_token, line, emoji)
                            log.info("Seek detected: %.1fs → %.1fs, line %d: %s",
                                     estimated, spotify_pos, current_idx, line)
                        else:
                            intro_status = f"🎵 {ui_song} — {ui_artist}"
                            ui_line      = "Instrumental / Intro"
                            discord_ok   = set_discord_status(discord_token, intro_status)
                            log.info("Seek detected: %.1fs → %.1fs, intro: %s",
                                     estimated, spotify_pos, intro_status)

                anchor_pos_s = spotify_pos
                anchor_time  = time.monotonic()
                last_resync  = time.monotonic()
                pos_s        = anchor_pos_s

                # Prefetch queue once per track to avoid hitting the Spotify API on every 5s poll.
                if not near_end and not prefetched_this_track:
                    prefetch_queue()
                    prefetched_this_track = True

            # ── Periodic drift correction ──
            if (timed_lines
                    and current_idx >= -1
                    and (time.monotonic() - last_drift_check) >= next_drift_interval):

                expected_idx = line_index_at(timed_lines, pos_s)
                if expected_idx != current_idx:
                    log.info("Drift corrected: line %d → %d at %.1fs", current_idx, expected_idx, pos_s)
                    current_idx = expected_idx
                    if current_idx >= 0:
                        _, line     = timed_lines[current_idx]
                        ui_line     = line
                        emoji       = random.choice(MOOD_EMOJI.get(mood, MOOD_EMOJI["neutral"]))
                        discord_ok  = set_discord_status(discord_token, line, emoji)
                    else:
                        intro_status = f"🎵 {ui_song} — {ui_artist}"
                        ui_line      = "Instrumental / Intro"
                        discord_ok   = set_discord_status(discord_token, intro_status)

                last_drift_check    = time.monotonic()
                next_drift_interval = random.uniform(15, 20)

            # ── Advance to next lyric line ──
            if timed_lines:
                new_idx = line_index_at(timed_lines, pos_s)
                if new_idx != current_idx:
                    current_idx = new_idx
                    if current_idx >= 0:
                        _, line     = timed_lines[current_idx]
                        ui_line     = line
                        emoji       = random.choice(MOOD_EMOJI.get(mood, MOOD_EMOJI["neutral"]))
                        discord_ok  = set_discord_status(discord_token, line, emoji)
                        log.info("Line %d: %s", current_idx, line)
                    else:
                        intro_status = f"🎵 {ui_song} — {ui_artist}"
                        ui_line      = "Instrumental / Intro"
                        discord_ok   = set_discord_status(discord_token, intro_status)
                        log.info("Intro/Instrumental: %s", intro_status)
            else:
                if ui_song and not is_paused:
                    progress_str = f"{fmt_time(pos_s)} / {fmt_time(ui_duration_s)}"
                    status_text  = f"🎵 {ui_song} — {ui_artist} ({progress_str})"
                    ui_line      = f"{ui_song} — {ui_artist}"
                    discord_ok   = set_discord_status(discord_token, status_text)

            print_status(
                song         = ui_song,
                artist       = ui_artist,
                progress_s   = pos_s,
                duration_s   = ui_duration_s,
                current_line = ui_line,
                source       = lyrics_source,
                discord_ok   = discord_ok,
                paused       = is_paused,
                album_art_lines = album_art_lines,
            )

            # ── Calculate sleep until next event ──
            pos_s      = anchor_pos_s if is_paused else anchor_pos_s + (time.monotonic() - anchor_time)
            candidates = []

            if timed_lines and current_idx + 1 < len(timed_lines):
                next_line_in = timed_lines[current_idx + 1][0] - pos_s
                if next_line_in > 0:
                    candidates.append(next_line_in)

            # Wake up every 1.0s to check the cache if currently fetching
            if lyrics_source == "Fetching...":
                candidates.append(1.0)
            # Ticking progress for songs without lyrics (at most once every 1.5s via background thread)
            elif not timed_lines and ui_song and not is_paused:
                candidates.append(1.5)

            next_poll_in  = poll_interval - (time.monotonic() - last_resync)
            next_drift_in = next_drift_interval - (time.monotonic() - last_drift_check)
            candidates.append(max(0.1, next_poll_in))
            candidates.append(max(0.1, next_drift_in))

            sleep_s = min(candidates) if candidates else 2.0
            sleep_s = min(sleep_s, 5.0)  # Cap at 5s to catch manual skips fast
            if near_end or is_idle or lyrics_source == "Fetching...":
                sleep_s = min(sleep_s, END_OF_SONG_POLL)

            time.sleep(sleep_s)

    except KeyboardInterrupt:
        clear_discord_status(discord_token)
        sys.stdout.write("\033[H\033[J")
        sys.stdout.flush()
        print("  Stopped. Discord status cleared.\n")

if __name__ == "__main__":
    if os.name == "nt":
        os.system("")  # enable ANSI codes
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = load_config()

    if not cfg:
        sys.stdout.write("\033[H\033[J")
        sys.stdout.flush()
        print("  Welcome! No config found. Let's set up your tokens.\n")
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
            sys.stdout.write("\033[H\033[J")
            sys.stdout.flush()
            print("  Bye!\n")
            sys.exit(0)
        else:
            print("  Invalid choice.")
            time.sleep(1)
