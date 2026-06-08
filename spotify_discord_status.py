import os
import re
import sys
import time
import json
import queue
import random
import logging
import threading
import unicodedata
import io
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import syncedlyrics as synced_lib

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


# ── Constants ──────────────────────────────────────────────────────────────────

MAX_STATUS_LEN     = 128
RESYNC_EVERY       = 5.0
END_OF_SONG_WINDOW = 10
END_OF_SONG_POLL   = 3
SEEK_THRESHOLD     = 3.0
PREFETCH_DELAY     = 10.0   # seconds into a track before prefetching the next one
LYRICS_RETRY_DELAY = 2.0    # seconds to wait before retrying a failed lyrics fetch

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


# ── Config ─────────────────────────────────────────────────────────────────────

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


# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    filename=os.path.join(get_appdata_dir(), "spotify_discord.log"),
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
log = logging.getLogger(__name__)


# ── Terminal helpers ───────────────────────────────────────────────────────────

def clear_screen():
    """Full erase — use for menus only."""
    sys.stdout.write("\033[H\033[J")
    sys.stdout.flush()

def cursor_home():
    """Move cursor to top-left without erasing — flicker-free live updates."""
    sys.stdout.write("\033[H")
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
        if (unicodedata.east_asian_width(c) in ('W', 'F', 'A')
                or (0x1f000 <= ord(c) <= 0x1faff)
                or (0x2600  <= ord(c) <= 0x27bf)):
            w += 2
        else:
            w += 1
    return w

def trim_display(s: str, width: int) -> str:
    s = s or ""
    if display_len(s) <= width:
        return s
    limit   = max(0, width - 3)
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

def wrap_display(text: str, width: int) -> List[str]:
    """Word-wrap that respects the display width of emoji / wide unicode characters."""
    words   = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if display_len(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


# ── Pike mascot ────────────────────────────────────────────────────────────────

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

def _clean_art_lines(art: str) -> List[str]:
    return [line.rstrip() for line in art.strip("\n").splitlines()]

def _pike_index(seed: str) -> int:
    seed = seed or "idle"
    return sum(ord(ch) for ch in seed) % len(PIKES)

def pike_panel(seed: str, speech: str, listening: bool) -> List[str]:
    name, art    = PIKES[_pike_index(seed)]
    bubble_width = 34
    if not listening:
        speech = "hi" if int(time.time() / 4) % 2 else "hey"

    # Use display-width-aware wrapping so emoji in lyrics don't break the bubble
    words = wrap_display(speech or "hi", bubble_width)[:3] or ["hi"]

    lines = ["." + "-" * (bubble_width + 2) + "."]
    lines.extend("| " + pad(line, bubble_width) + " |" for line in words)
    lines.append("'" + "-" * (bubble_width + 2) + "'")
    lines.append("        /")
    lines.extend("  " + line for line in _clean_art_lines(art))
    return lines


# ── UI rendering ───────────────────────────────────────────────────────────────

def print_status(song, artist, progress_s, duration_s, current_line,
                 source, discord_ok, paused=False, album_art_lines=None):
    W        = 58
    bar_w    = W - 10
    ratio    = min(max(progress_s / max(duration_s, 1), 0.0), 1.0)
    filled   = int(ratio * bar_w)
    bar      = "#" * filled + "." * (bar_w - filled)
    prog_str = f"{fmt_time(progress_s)} / {fmt_time(duration_s)}"
    status   = "Paused" if paused else ("Updated" if discord_ok else "Failed")

    song_t   = trim_display(song,         W - 12) if song         else ""
    artist_t = trim_display(artist,       W - 12) if artist       else ""
    line_t   = trim_display(current_line, W - 4)  if current_line else "-"
    source_t = trim_display(source,       W - 12) if source       else "-"

    gui_lines = [
        border(W, "="),
        row("Spotify -> Discord Status", W),
        border(W),
        row(f"Song    {song_t}",   W),
        row(f"Artist  {artist_t}", W),
        row("",                    W),
        row(f"[{bar}]",            W),
        row(prog_str,              W),
        border(W),
        row(line_t,                W),
        border(W),
        row(f"Source  {source_t}", W),
        row(f"Discord {status}",   W),
        border(W),
        row("Ctrl+C to stop",      W),
        border(W, "="),
    ]

    listening    = bool(song)
    right_lines  = pike_panel(f"{song}|{artist}", current_line if listening else "", listening)
    combined     = list(right_lines)
    if album_art_lines:
        combined.append("")
        combined.extend(album_art_lines)

    max_len = max(len(gui_lines), len(combined))

    # cursor_home instead of clear_screen → no blank-screen flash between frames
    cursor_home()
    for i in range(max_len):
        left  = gui_lines[i] if i < len(gui_lines) else " " * (W + 2)
        right = combined[i]  if i < len(combined)  else ""
        sys.stdout.write(f"{left}   {right}\033[K\n")
    sys.stdout.write("\033[J")
    sys.stdout.flush()


# ── Menus ──────────────────────────────────────────────────────────────────────

def main_menu() -> str:
    clear_screen()
    W = 38
    gui_lines = [
        border(W, "="),
        row("Spotify -> Discord Status", W),
        border(W),
        row("[1] Start",    W),
        row("[2] Settings", W),
        row("[3] Exit",     W),
        border(W, "="),
    ]
    right_lines = pike_panel("menu", "pick an option!", True)
    max_len     = max(len(gui_lines), len(right_lines))
    for i in range(max_len):
        left  = gui_lines[i]    if i < len(gui_lines)    else " " * (W + 2)
        right = right_lines[i]  if i < len(right_lines)  else ""
        sys.stdout.write(f"{left}   {right}\033[K\n")
    sys.stdout.write("\033[J")
    sys.stdout.flush()
    return input("\n  > ").strip()

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
        print(row("[B] Back",       W))
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
                val  = input(f"  {label}{hint}\n  > ").strip()
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


# ── Discord client ─────────────────────────────────────────────────────────────

class DiscordStatusClient:
    """
    Manages Discord custom-status updates in a dedicated background thread.
    Enforces ≥1.5 s spacing between calls and handles 429 backoff automatically.
    All mutable state is encapsulated — no module-level globals.
    """

    def __init__(self, token: str):
        self._token          = token
        self._queue: queue.Queue = queue.Queue()
        self._backoff_until  = 0.0
        self._last_ok        = threading.Event()
        self._last_ok.set()
        self._lock           = threading.Lock()
        self._started        = False

    # ── Public API ────────────────────────────────────────────────────────────

    def set_status(self, text: str, emoji: str = "🎵") -> bool:
        self._ensure_started()
        self._drain()
        self._queue.put((self._token, text, emoji))
        return self._last_ok.is_set()

    def clear_status(self):
        self._drain()
        self._queue.put((self._token, "", ""))
        # Also fire a direct request so the status clears even if the worker
        # is mid-backoff when the program exits.
        url     = "https://discord.com/api/v9/users/@me/settings"
        headers = {"Authorization": self._token, "Content-Type": "application/json"}
        try:
            requests.patch(url, json={"custom_status": None}, headers=headers, timeout=10)
        except requests.RequestException:
            pass

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _ensure_started(self):
        with self._lock:
            if not self._started:
                t = threading.Thread(target=self._worker, daemon=True)
                t.start()
                self._started = True

    def _drain(self):
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def _worker(self):
        last_update = 0.0
        pending     = None
        while True:
            try:
                timeout = None
                if pending is not None:
                    now       = time.monotonic()
                    remaining = max(0.0, 1.5 - (now - last_update))
                    if now < self._backoff_until:
                        remaining = max(remaining, self._backoff_until - now)
                    timeout = max(0.01, remaining)
                item = self._queue.get(timeout=timeout)
                if item is None:
                    break
                pending = item
            except queue.Empty:
                pass

            if pending is None:
                continue

            now = time.monotonic()
            if (now - last_update) >= 1.5 and now >= self._backoff_until:
                token, text, emoji = pending
                url     = "https://discord.com/api/v9/users/@me/settings"
                headers = {"Authorization": token, "Content-Type": "application/json"}
                payload = {
                    "custom_status": {"text": text[:MAX_STATUS_LEN], "emoji_name": emoji} if text else None
                }
                try:
                    resp = requests.patch(url, json=payload, headers=headers, timeout=10)
                    last_update = time.monotonic()
                    if resp.status_code == 429:
                        retry_after        = float(resp.json().get("retry_after", 5))
                        self._backoff_until = time.monotonic() + retry_after
                        self._last_ok.clear()
                        log.warning("Discord 429 – backing off %.1fs", retry_after)
                    elif resp.status_code == 200:
                        self._last_ok.set()
                        pending = None
                    else:
                        self._last_ok.clear()
                        pending = None
                except requests.RequestException as exc:
                    log.warning("Discord API error: %s", exc)
                    self._last_ok.clear()
                    self._backoff_until = time.monotonic() + 2.0


# ── Album art ──────────────────────────────────────────────────────────────────

class AlbumArtRenderer:
    """
    Downloads and renders album art as ANSI half-block pixel art.
    Thread-safe: background fetch writes behind a lock; main loop reads safely.
    Requires Pillow — degrades gracefully if not installed.
    """

    ART_W = 22  # characters wide; h = ART_W → visually square in most terminals

    def __init__(self):
        self._cache: dict[str, List[str]] = {}
        self._cache_lock = threading.Lock()
        self._lines: List[str] = []
        self._lines_lock = threading.Lock()

    def get_lines(self) -> List[str]:
        with self._lines_lock:
            return list(self._lines)

    def load_async(self, url: Optional[str]):
        if url:
            threading.Thread(target=self._fetch_and_store, args=(url,), daemon=True).start()
        else:
            self.clear()

    def clear(self):
        with self._lines_lock:
            self._lines = []

    # ── Internal ──────────────────────────────────────────────────────────────

    def _fetch_and_store(self, url: str):
        lines = self._fetch(url)
        with self._lines_lock:
            self._lines = lines

    def _fetch(self, url: str) -> List[str]:
        with self._cache_lock:
            if url in self._cache:
                self._cache[url] = self._cache.pop(url)  # LRU bump
                return self._cache[url]

        if not _PIL_AVAILABLE:
            log.warning("Pillow not installed — album art disabled. Run: pip install Pillow")
            return []

        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code != 200:
                return []

            h_px = self.ART_W
            img  = Image.open(io.BytesIO(resp.content)).convert("RGB")
            img  = img.resize((self.ART_W, h_px), Image.Resampling.LANCZOS)
            px   = img.load()

            lines = []
            for r in range(0, h_px, 2):
                parts = []
                for col in range(self.ART_W):
                    rt, gt, bt = px[col, r]
                    rb, gb, bb = px[col, r + 1]
                    parts.append(
                        f"\033[48;2;{rt};{gt};{bt}m"
                        f"\033[38;2;{rb};{gb};{bb}m"
                        "▄"
                    )
                lines.append("".join(parts) + "\033[0m")

            with self._cache_lock:
                self._cache[url] = lines
                if len(self._cache) > 20:
                    del self._cache[next(iter(self._cache))]
            return lines
        except Exception as exc:
            log.warning("Album art render failed: %s", exc)
            return []


# ── Lyrics ─────────────────────────────────────────────────────────────────────

class FetchState(Enum):
    PENDING = auto()
    DONE    = auto()
    FAILED  = auto()

@dataclass
class CachedLyrics:
    state:     FetchState
    timed:     Optional[List[Tuple[float, str]]] = None
    source:    str                               = ""
    mood:      str                               = "neutral"
    plain_raw: Optional[List[str]]               = None

def parse_lrc(lrc_text: str) -> List[Tuple[float, str]]:
    pattern = re.compile(r"\[(\d+):(\d+\.\d+)\](.*)")
    result: List[Tuple[float, str]] = []
    for match in pattern.finditer(lrc_text):
        minutes = int(match.group(1))
        seconds = float(match.group(2))
        text    = match.group(3).strip()
        if text:
            result.append((minutes * 60 + seconds, text))
    return sorted(result, key=lambda x: x[0])

def detect_mood(timed_lines: List[Tuple[float, str]]) -> str:
    """
    Density-based mood detection.
    Divides hit count by total word count so long lyrics don't dominate.
    """
    text  = " ".join(l for _, l in timed_lines).lower()
    words = text.split()
    total = max(len(words), 1)
    scores = {
        mood: sum(text.count(kw) for kw in kws) / total
        for mood, kws in MOOD_KEYWORDS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "neutral"

def _clean_title(title: str) -> str:
    title = title or ""
    junk_words = (
        r"feat\.?|ft\.?|featuring|with|remaster(?:ed)?|"
        r"version|edit|mix|mono|stereo|deluxe|bonus|explicit|clean|"
        r"radio|single|album|live|acoustic|instrumental|sped up|slowed"
    )
    title = re.sub(rf"\s*[\(\[].*?(?:{junk_words}).*?[\)\]]", "", title, flags=re.IGNORECASE)
    title = re.sub(rf"\s*[-–—]\s*(?:\d{{4}}\s*)?(?:{junk_words}).*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+(?:feat\.?|ft\.?|featuring)\s+.+$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*[\(\[]\s*[\)\]]", "", title)
    title = re.sub(r"\s{2,}", " ", title)
    return title.strip()

def _lrclib_parse(data: dict, duration_s: float) -> Optional[List[Tuple[float, str]]]:
    lrc_text = data.get("syncedLyrics") or data.get("plainLyrics")
    if not lrc_text:
        return None
    if data.get("syncedLyrics"):
        return parse_lrc(lrc_text)
    lines    = [l.strip() for l in lrc_text.splitlines() if l.strip()]
    time_per = duration_s / max(len(lines), 1)
    return [(i * time_per, l) for i, l in enumerate(lines)]

def _fetch_syncedlyrics(artist: str, title: str, duration_s: float):
    clean       = _clean_title(title)
    search_term = f"{artist} {clean}"
    try:
        lrc_text = synced_lib.search(search_term)
        if lrc_text:
            if re.search(r"\[\d+:\d+\.\d+\]", lrc_text):
                result = parse_lrc(lrc_text)
                if result:
                    log.info("syncedlyrics synced: %s", search_term)
                    return result, True
            lines = [l.strip() for l in lrc_text.splitlines()
                     if l.strip() and not re.match(r"^\[.*\]$", l)]
            if lines:
                time_per = duration_s / max(len(lines), 1)
                result   = [(i * time_per, l) for i, l in enumerate(lines)]
                log.info("syncedlyrics plain: %s", search_term)
                return result, False
    except Exception as exc:
        log.warning("syncedlyrics error for '%s': %s", search_term, exc)
    return None, None

def _fetch_lrclib(artist: str, title: str, duration_s: float):
    headers = {"User-Agent": "SpotifyDiscordStatus/1.0"}
    clean   = _clean_title(title)

    for params in [
        {"artist_name": artist, "track_name": title,  "duration": int(duration_s)},
        {"artist_name": artist, "track_name": title},
        {"artist_name": artist, "track_name": clean,  "duration": int(duration_s)},
        {"artist_name": artist, "track_name": clean},
    ]:
        try:
            resp = requests.get("https://lrclib.net/api/get", params=params, timeout=8, headers=headers)
            if resp.status_code == 200:
                data   = resp.json()
                synced = bool(data.get("syncedLyrics"))
                result = _lrclib_parse(data, duration_s)
                if result:
                    log.info("LrcLib /get matched %s | synced=%s", params, synced)
                    return result, synced
        except Exception as exc:
            log.warning("LrcLib /get %s error: %s", params, exc)

    for params in [
        {"artist_name": artist, "track_name": title},
        {"artist_name": artist, "track_name": clean},
        {"q": f"{artist} {clean}"},
    ]:
        try:
            resp = requests.get("https://lrclib.net/api/search", params=params, timeout=8, headers=headers)
            if resp.status_code == 200:
                hits = resp.json()
                if isinstance(hits, list):
                    for hit in hits[:3]:
                        synced = bool(hit.get("syncedLyrics"))
                        result = _lrclib_parse(hit, duration_s)
                        if result:
                            log.info("LrcLib /search matched %s | synced=%s", params, synced)
                            return result, synced
        except Exception as exc:
            log.warning("LrcLib /search %s error: %s", params, exc)

    return None, None


class LyricsCache:
    """
    Thread-safe LRU lyrics cache.
    Uses a FetchState enum instead of sentinel strings.
    Failed fetches are retried once after LYRICS_RETRY_DELAY seconds.
    """

    MAX_SIZE = 50

    def __init__(self):
        self._cache: dict[str, CachedLyrics] = {}
        self._lock = threading.Lock()

    def get(self, track_id: str) -> Optional[CachedLyrics]:
        with self._lock:
            entry = self._cache.get(track_id)
            if entry:
                self._cache[track_id] = self._cache.pop(track_id)  # LRU bump
            return entry

    def set_pending(self, track_id: str):
        with self._lock:
            if track_id not in self._cache:
                self._cache[track_id] = CachedLyrics(state=FetchState.PENDING)
                self._evict()

    def set_result(self, track_id: str, entry: CachedLyrics):
        with self._lock:
            self._cache[track_id] = entry
            self._evict()

    def is_pending(self, track_id: str) -> bool:
        with self._lock:
            entry = self._cache.get(track_id)
            return entry is not None and entry.state == FetchState.PENDING

    def fetch_async(self, track_id: str, artist: str, title: str, duration_s: float):
        """Mark as PENDING and kick off a background thread with one retry on failure."""
        self.set_pending(track_id)
        t = threading.Thread(
            target=self._fetch_worker,
            args=(track_id, artist, title, duration_s),
            daemon=True,
        )
        t.start()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _evict(self):
        while len(self._cache) > self.MAX_SIZE:
            del self._cache[next(iter(self._cache))]

    def _fetch_worker(self, track_id: str, artist: str, title: str, duration_s: float):
        for attempt in range(2):
            if attempt > 0:
                time.sleep(LYRICS_RETRY_DELAY)
                log.info("Retrying lyrics fetch (attempt 2): %s – %s", artist, title)

            result, synced = _fetch_syncedlyrics(artist, title, duration_s)
            if result and synced:
                src, raw = "syncedlyrics (synced)", None
            elif result:
                src, raw = "syncedlyrics (plain)", [line for _, line in result]
                result   = None
            else:
                log.info("syncedlyrics miss → LrcLib: %s – %s", artist, title)
                result, synced = _fetch_lrclib(artist, title, duration_s)
                if result and synced:
                    src, raw = "LrcLib (synced)", None
                elif result:
                    src, raw = "LrcLib (plain)", [line for _, line in result]
                    result   = None
                else:
                    src, raw, result = "None (track name only)", None, None

            # If we got anything useful, don't retry
            if result is not None or raw is not None:
                break

        mood  = detect_mood(result) if result else "neutral"
        state = FetchState.DONE if (result is not None or raw is not None) else FetchState.FAILED
        self.set_result(track_id, CachedLyrics(
            state=state, timed=result, source=src, mood=mood, plain_raw=raw
        ))
        log.info("Cached: %s – %s | %s", artist, title, src)


# ── Lyrics position helpers ────────────────────────────────────────────────────

def line_index_at(timed_lines: List[Tuple[float, str]], pos_s: float) -> int:
    idx = -1
    for i, (start, _) in enumerate(timed_lines):
        if start <= pos_s:
            idx = i
        else:
            break
    return idx

def anchor_plain(raw_lines: List[str], duration_s: float, pos_s: float) -> List[Tuple[float, str]]:
    """Spread plain lines evenly, then shift so pos_s aligns with the current line."""
    if not raw_lines:
        return []
    time_per  = duration_s / len(raw_lines)
    timed     = [(i * time_per, l) for i, l in enumerate(raw_lines)]
    start_idx = max(line_index_at(timed, pos_s), 0)
    offset    = pos_s - timed[start_idx][0]
    return [(t + offset, l) for t, l in timed]


# ── Playback state ─────────────────────────────────────────────────────────────

@dataclass
class PlaybackState:
    """All mutable state for the current playback session."""
    track_id:        Optional[str]                   = None
    song:            str                             = ""
    artist:          str                             = ""
    duration_s:      float                           = 0.0
    anchor_pos_s:    float                           = 0.0
    anchor_time:     float                           = field(default_factory=time.monotonic)
    timed_lines:     List[Tuple[float, str]]         = field(default_factory=list)
    lyrics_source:   str                             = "—"
    mood:            str                             = "neutral"
    current_idx:     int                             = -1
    plain_raw:       Optional[List[str]]             = None
    plain_anchored:  bool                            = False  # prevent re-anchoring on every resync
    is_paused:       bool                            = False
    current_line:    str                             = ""
    discord_ok:      bool                            = False
    last_resync:     float                           = 0.0
    last_drift:      float                           = 0.0
    next_drift_int:  float                           = field(default_factory=lambda: random.uniform(15, 20))
    prefetch_after:  float                           = 0.0   # don't prefetch until this monotonic time

    def pos(self) -> float:
        """Estimated playback position, frozen while paused."""
        if self.is_paused:
            return self.anchor_pos_s
        return self.anchor_pos_s + (time.monotonic() - self.anchor_time)

    def reanchor(self, spotify_pos_s: float):
        """Re-sync anchor to a fresh position from the Spotify API."""
        self.anchor_pos_s = spotify_pos_s
        self.anchor_time  = time.monotonic()


# ── Player ─────────────────────────────────────────────────────────────────────

class Player:
    """
    Encapsulates the main playback loop.
    The old monolithic run() is split into focused methods that each own
    one concern: polling, seek detection, background-fetch completion,
    drift correction, lyric advancement, rendering, and sleep scheduling.
    """

    def __init__(self, sp: spotipy.Spotify, discord: DiscordStatusClient):
        self._sp      = sp
        self._discord = discord
        self._cache   = LyricsCache()
        self._art     = AlbumArtRenderer()
        self._state   = PlaybackState()

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self):
        try:
            while True:
                self._tick()
        except KeyboardInterrupt:
            self._discord.clear_status()
            sys.stdout.write("\033[H\033[J")
            sys.stdout.flush()
            print("  Stopped. Discord status cleared.\n")

    # ── Main tick ─────────────────────────────────────────────────────────────

    def _tick(self):
        s         = self._state
        near_end  = s.duration_s > 0 and s.pos() >= s.duration_s - END_OF_SONG_WINDOW
        is_idle   = s.track_id is None or s.is_paused
        poll_ivl  = END_OF_SONG_POLL if (near_end or is_idle) else RESYNC_EVERY
        need_poll = s.track_id is None or (time.monotonic() - s.last_resync) >= poll_ivl

        self._check_background_fetch()

        if need_poll:
            self._poll_spotify(near_end)

        self._drift_correction()
        self._advance_lyric()
        self._render()
        self._sleep(near_end, is_idle, poll_ivl)

    # ── Spotify poll ──────────────────────────────────────────────────────────

    def _poll_spotify(self, near_end: bool):
        s = self._state
        try:
            playback = self._sp.current_playback()
        except Exception as exc:
            log.error("Spotify poll: %s", exc)
            time.sleep(2)
            return

        if playback is None:
            time.sleep(2)
            return

        if not playback.get("is_playing") or not playback.get("item"):
            self._handle_not_playing(playback)
            s.last_resync = time.monotonic()
            time.sleep(2)
            return

        item        = playback["item"]
        track_id    = item["id"]
        track_name  = item["name"]
        artists     = item.get("artists") or []
        artist_name = artists[0]["name"] if artists else "Unknown Artist"
        progress_ms = playback["progress_ms"]
        duration_s  = item["duration_ms"] / 1000

        s.song       = track_name
        s.artist     = artist_name
        s.duration_s = duration_s

        if s.is_paused:
            self._handle_resume(progress_ms)

        if track_id != s.track_id:
            self._handle_track_change(track_id, track_name, artist_name, duration_s, item, progress_ms)
        else:
            self._handle_seek(progress_ms, duration_s)

        s.reanchor(progress_ms / 1000)
        s.last_resync = time.monotonic()

        # Prefetch is delayed PREFETCH_DELAY seconds into a track to avoid
        # hitting the queue API the instant a song starts
        if not near_end and time.monotonic() >= s.prefetch_after:
            self._prefetch_queue()
            s.prefetch_after = float("inf")  # once per track

    # ── Not playing ───────────────────────────────────────────────────────────

    def _handle_not_playing(self, playback):
        s = self._state
        if not playback or not playback.get("item"):
            if s.track_id is not None:
                self._discord.clear_status()
                s.track_id    = None
                s.timed_lines = []
                s.current_idx = -1
                s.duration_s  = 0.0
                s.is_paused   = False
            s.song          = ""
            s.artist        = ""
            s.current_line  = "Nothing playing"
            s.lyrics_source = "Idle"
            self._art.clear()
        else:
            if not s.is_paused:
                s.is_paused  = True
                s.reanchor(playback["progress_ms"] / 1000)
                pause_line   = f"⏸ {s.song} — {s.artist}" if s.song else "⏸ Paused"
                s.discord_ok = self._discord.set_status(pause_line, "⏸")
                log.info("Paused at %.1fs", s.anchor_pos_s)

    # ── Resume ────────────────────────────────────────────────────────────────

    def _handle_resume(self, progress_ms: int):
        s           = self._state
        s.is_paused = False
        s.reanchor(progress_ms / 1000)
        log.info("Resumed at %.1fs", s.anchor_pos_s)

    # ── Track change ──────────────────────────────────────────────────────────

    def _handle_track_change(self, track_id, track_name, artist_name, duration_s, item, progress_ms):
        s = self._state
        s.track_id       = track_id
        s.plain_anchored = False
        s.prefetch_after = time.monotonic() + PREFETCH_DELAY

        images  = item.get("album", {}).get("images", [])
        art_url = images[-1]["url"] if images else None
        self._art.load_async(art_url)

        start_pos    = progress_ms / 1000
        fetch_start  = time.monotonic()

        cached = self._cache.get(track_id)
        if cached and cached.state != FetchState.PENDING:
            result = cached.timed
            src    = cached.source + " (prefetched)"
            mood   = cached.mood
            raw    = cached.plain_raw
            log.info("Cache hit: %s – %s", artist_name, track_name)
        else:
            s.current_line  = "Fetching lyrics..."
            s.lyrics_source = "Fetching..."
            self._cache.fetch_async(track_id, artist_name, track_name, duration_s)
            result, src, mood, raw = None, "Fetching...", "neutral", None

        # Compensate for time spent in this function
        s.reanchor(start_pos + (time.monotonic() - fetch_start))

        if result is None and raw and not s.plain_anchored:
            result           = anchor_plain(raw, duration_s, s.anchor_pos_s)
            src              = f"{src} (anchored)"
            s.plain_anchored = True

        s.timed_lines   = result or []
        s.lyrics_source = src
        s.mood          = mood
        s.plain_raw     = raw
        s.current_idx   = line_index_at(s.timed_lines, s.anchor_pos_s) if s.timed_lines else -1

        self._push_to_discord()
        log.info("Track change: %s – %s | %s | %d lines", artist_name, track_name, src, len(s.timed_lines))

    # ── Seek detection ────────────────────────────────────────────────────────

    def _handle_seek(self, progress_ms: int, duration_s: float):
        s           = self._state
        spotify_pos = progress_ms / 1000
        estimated   = s.pos()
        if abs(spotify_pos - estimated) <= SEEK_THRESHOLD or not s.timed_lines:
            return

        if s.plain_raw:
            # Re-anchor plain lyrics to the new seek position (only on explicit seek)
            s.timed_lines    = anchor_plain(s.plain_raw, duration_s, spotify_pos)
            s.plain_anchored = True

        new_idx = line_index_at(s.timed_lines, spotify_pos)
        if new_idx != s.current_idx:
            s.current_idx = new_idx
            self._push_to_discord()
            log.info("Seek: %.1fs → %.1fs, line %d", estimated, spotify_pos, s.current_idx)

    # ── Background fetch completion ───────────────────────────────────────────

    def _check_background_fetch(self):
        s = self._state
        if s.lyrics_source != "Fetching..." or s.track_id is None:
            return

        cached = self._cache.get(s.track_id)
        if cached is None or cached.state == FetchState.PENDING:
            return

        result = cached.timed
        src    = cached.source
        raw    = cached.plain_raw

        # Anchor plain lyrics only once — don't shift again if a resync already did it
        if result is None and raw and not s.plain_anchored:
            result           = anchor_plain(raw, s.duration_s, s.pos())
            src              = f"{src} (anchored)"
            s.plain_anchored = True

        s.timed_lines   = result or []
        s.lyrics_source = src
        s.mood          = cached.mood
        s.plain_raw     = raw
        s.current_idx   = line_index_at(s.timed_lines, s.pos()) if s.timed_lines else -1

        self._push_to_discord()
        log.info("Background lyrics ready for %s", s.track_id)

    # ── Drift correction ──────────────────────────────────────────────────────

    def _drift_correction(self):
        s = self._state
        if not s.timed_lines:
            return
        if (time.monotonic() - s.last_drift) < s.next_drift_int:
            return

        expected = line_index_at(s.timed_lines, s.pos())
        if expected != s.current_idx:
            log.info("Drift: line %d → %d at %.1fs", s.current_idx, expected, s.pos())
            s.current_idx = expected
            self._push_to_discord()

        s.last_drift     = time.monotonic()
        s.next_drift_int = random.uniform(15, 20)

    # ── Lyric advancement ─────────────────────────────────────────────────────

    def _advance_lyric(self):
        s = self._state
        if not s.timed_lines:
            if s.song and not s.is_paused:
                pos_str        = f"{fmt_time(s.pos())} / {fmt_time(s.duration_s)}"
                status_txt     = f"🎵 {s.song} — {s.artist} ({pos_str})"
                s.current_line = f"{s.song} — {s.artist}"
                s.discord_ok   = self._discord.set_status(status_txt)
            return

        new_idx = line_index_at(s.timed_lines, s.pos())
        if new_idx != s.current_idx:
            s.current_idx = new_idx
            self._push_to_discord()
            log.info("Line %d: %s", s.current_idx, s.current_line)

    # ── Push current lyric to Discord ─────────────────────────────────────────

    def _push_to_discord(self):
        s = self._state
        if s.current_idx >= 0 and s.timed_lines:
            _, line        = s.timed_lines[s.current_idx]
            s.current_line = line
            emoji          = random.choice(MOOD_EMOJI.get(s.mood, MOOD_EMOJI["neutral"]))
            s.discord_ok   = self._discord.set_status(line, emoji)
        else:
            intro          = f"🎵 {s.song} — {s.artist}"
            s.current_line = "Instrumental / Intro"
            s.discord_ok   = self._discord.set_status(intro)

    # ── Render ────────────────────────────────────────────────────────────────

    def _render(self):
        s = self._state
        print_status(
            song            = s.song,
            artist          = s.artist,
            progress_s      = s.pos(),
            duration_s      = s.duration_s,
            current_line    = s.current_line,
            source          = s.lyrics_source,
            discord_ok      = s.discord_ok,
            paused          = s.is_paused,
            album_art_lines = self._art.get_lines(),
        )

    # ── Sleep scheduling ──────────────────────────────────────────────────────

    def _sleep(self, near_end: bool, is_idle: bool, poll_ivl: float):
        s          = self._state
        candidates = []

        if s.timed_lines and 0 <= s.current_idx < len(s.timed_lines) - 1:
            next_in = s.timed_lines[s.current_idx + 1][0] - s.pos()
            if next_in > 0:
                candidates.append(next_in)

        if s.lyrics_source == "Fetching...":
            candidates.append(1.0)
        elif not s.timed_lines and s.song and not s.is_paused:
            candidates.append(1.5)

        candidates.append(max(0.1, poll_ivl          - (time.monotonic() - s.last_resync)))
        candidates.append(max(0.1, s.next_drift_int  - (time.monotonic() - s.last_drift)))

        sleep_s = min(candidates) if candidates else 2.0
        sleep_s = min(sleep_s, 5.0)
        if near_end or is_idle or s.lyrics_source == "Fetching...":
            sleep_s = min(sleep_s, END_OF_SONG_POLL)

        time.sleep(sleep_s)

    # ── Prefetch ──────────────────────────────────────────────────────────────

    def _prefetch_queue(self):
        try:
            queue_data = self._sp.queue()
            if not queue_data:
                return
            upcoming = queue_data.get("queue", [])
            if not upcoming:
                return
            nxt     = upcoming[0]
            nid     = nxt.get("id")
            if not nid:
                return
            nname   = nxt["name"]
            artists = nxt.get("artists") or []
            nartist = artists[0]["name"] if artists else "Unknown Artist"
            ndur    = nxt["duration_ms"] / 1000
            if self._cache.get(nid) is None:
                log.info("Prefetching: %s – %s", nartist, nname)
                self._cache.fetch_async(nid, nartist, nname, ndur)
        except Exception as exc:
            log.warning("Prefetch error: %s", exc)


# ── Entry point ────────────────────────────────────────────────────────────────

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
            client_id     = cfg["SPOTIPY_CLIENT_ID"],
            client_secret = cfg["SPOTIPY_CLIENT_SECRET"],
            redirect_uri  = redirect,
            scope         = "user-read-currently-playing user-read-playback-state",
            cache_path    = os.path.join(get_appdata_dir(), ".cache"),
        )
        sp = spotipy.Spotify(auth_manager=auth)
    except Exception as exc:
        print(f"\n  Spotify auth failed: {exc}")
        time.sleep(3)
        return

    discord = DiscordStatusClient(cfg["DISCORD_TOKEN"])
    player  = Player(sp, discord)
    player.run()


if __name__ == "__main__":
    if os.name == "nt":
        os.system("")  # enable ANSI codes on Windows
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = load_config()
    if not cfg:
        clear_screen()
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
            clear_screen()
            print("  Bye!\n")
            sys.exit(0)
        else:
            print("  Invalid choice.")
            time.sleep(1)
