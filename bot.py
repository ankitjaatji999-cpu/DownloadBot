"""
MeraDownload4K — Premium Telegram Downloader Bot v3.0
Platforms: YouTube · Instagram · Facebook · Snapchat · Reddit · Twitter/X
"""

import glob
import http.cookiejar
import json
import logging
import os
import random
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from queue import Queue
from typing import Optional

import requests
import yt_dlp
from flask import Flask, jsonify

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bot")

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set.")

_raw_admin = os.environ.get("ADMIN_ID", "").strip()
try:
    ADMIN_ID: Optional[int] = int(_raw_admin) if _raw_admin else None
except ValueError:
    ADMIN_ID = None

API_URL  = f"https://api.telegram.org/bot{BOT_TOKEN}"
PORT     = int(os.environ.get("PORT", 8099))
BOT_NAME = ""

FFMPEG      = shutil.which("ffmpeg") or ""
FFPROBE     = shutil.which("ffprobe") or ""
BOT_DIR     = os.path.dirname(os.path.abspath(__file__))
COOKIES     = os.path.join(BOT_DIR, "cookies.txt")
HAS_COOKIES = os.path.isfile(COOKIES) and os.path.getsize(COOKIES) > 100
STATS_FILE  = os.path.join(BOT_DIR, "stats.json")

MAX_WORKERS      = 5
CLEANUP_INTERVAL = 1800   # 30 min
MAX_FILE_AGE     = 3600   # 1 hour
BROADCAST_DELAY  = 0.05

# ─────────────────────────────────────────────────────────────────────────────
# Domain sets
# ─────────────────────────────────────────────────────────────────────────────

YOUTUBE_DOMAINS   = {"youtube.com", "youtu.be", "youtube-nocookie.com"}
INSTAGRAM_DOMAINS = {"instagram.com"}
FACEBOOK_DOMAINS  = {"facebook.com", "fb.watch", "fb.com", "mbasic.facebook.com"}
SNAPCHAT_DOMAINS  = {"snapchat.com", "snap.com"}
REDDIT_DOMAINS    = {"reddit.com", "redd.it", "old.reddit.com", "v.redd.it"}
TWITTER_DOMAINS   = {"twitter.com", "x.com", "t.co", "fxtwitter.com", "vxtwitter.com"}

ALL_DOMAINS = (YOUTUBE_DOMAINS | INSTAGRAM_DOMAINS | FACEBOOK_DOMAINS |
               SNAPCHAT_DOMAINS | REDDIT_DOMAINS | TWITTER_DOMAINS)


def is_yt(u: str) -> bool:        return any(d in u for d in YOUTUBE_DOMAINS)
def is_ig(u: str) -> bool:        return any(d in u for d in INSTAGRAM_DOMAINS)
def is_fb(u: str) -> bool:        return any(d in u for d in FACEBOOK_DOMAINS)
def is_sc(u: str) -> bool:        return any(d in u for d in SNAPCHAT_DOMAINS)
def is_reddit(u: str) -> bool:    return any(d in u for d in REDDIT_DOMAINS)
def is_twitter(u: str) -> bool:   return any(d in u for d in TWITTER_DOMAINS)
def is_supported(t: str) -> bool: return any(d in t for d in ALL_DOMAINS)

def _platform(url: str) -> str:
    if is_yt(url):      return "youtube"
    if is_ig(url):      return "instagram"
    if is_fb(url):      return "facebook"
    if is_sc(url):      return "snapchat"
    if is_reddit(url):  return "reddit"
    if is_twitter(url): return "twitter"
    return "unknown"

# ─────────────────────────────────────────────────────────────────────────────
# Stats / Analytics
# ─────────────────────────────────────────────────────────────────────────────

_stats_lock = threading.Lock()
_stats: dict = {
    "total_users":     [],
    "daily_users":     {},
    "total_downloads": 0,
    "downloads_today": 0,
    "stats_date":      str(date.today()),
    "platform_stats":  {
        "youtube": 0, "instagram": 0, "facebook": 0,
        "snapchat": 0, "reddit": 0, "twitter": 0,
    },
    "hourly_counts":   {},
}


def _load_stats():
    global _stats
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE) as f:
                loaded = json.load(f)
            today = str(date.today())
            if loaded.get("stats_date") != today:
                loaded["downloads_today"] = 0
                loaded["stats_date"]      = today
            for key, default in [
                ("platform_stats", {"youtube": 0, "instagram": 0, "facebook": 0,
                                    "snapchat": 0, "reddit": 0, "twitter": 0}),
                ("daily_users",   {}),
                ("hourly_counts", {}),
            ]:
                if key not in loaded:
                    loaded[key] = default
            # Ensure new platforms present in existing stats
            for p in ("reddit", "twitter"):
                loaded["platform_stats"].setdefault(p, 0)
            _stats = loaded
    except Exception as e:
        log.warning("Could not load stats: %s", e)


def _save_stats():
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(_stats, f)
    except Exception as e:
        log.warning("Could not save stats: %s", e)


def _record_user(uid: int):
    with _stats_lock:
        if uid not in _stats["total_users"]:
            _stats["total_users"].append(uid)
        today = str(date.today())
        _stats["daily_users"].setdefault(today, [])
        if uid not in _stats["daily_users"][today]:
            _stats["daily_users"][today].append(uid)
        _save_stats()


def _record_download(platform: str):
    with _stats_lock:
        today = str(date.today())
        if _stats.get("stats_date") != today:
            _stats["downloads_today"] = 0
            _stats["stats_date"]      = today
        _stats["total_downloads"]         += 1
        _stats["downloads_today"]         += 1
        _stats["platform_stats"][platform] = _stats["platform_stats"].get(platform, 0) + 1
        hk = datetime.now().strftime("%Y-%m-%dT%H")
        _stats["hourly_counts"][hk] = _stats["hourly_counts"].get(hk, 0) + 1
        _save_stats()


def _get_stats() -> dict:
    with _stats_lock:
        today = str(date.today())
        return {
            "total_users":     len(_stats["total_users"]),
            "active_today":    len(_stats["daily_users"].get(today, [])),
            "total_downloads": _stats["total_downloads"],
            "downloads_today": _stats["downloads_today"],
            "active_downloads": sum(1 for v in _active.values() if v),
            "platform_stats":  dict(_stats["platform_stats"]),
            "queue_size":      _dl_queue.qsize(),
        }


def _get_all_user_ids() -> list:
    with _stats_lock:
        return list(_stats["total_users"])

# ─────────────────────────────────────────────────────────────────────────────
# User-Agent pools
# ─────────────────────────────────────────────────────────────────────────────

_DESKTOP_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

_MOBILE_UAS = [
    "Mozilla/5.0 (Linux; Android 14; SM-S928U) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.52 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.52 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/125.0.6422.80 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; SM-A546U) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
]

_IG_APP_UAS = [
    "Instagram 310.0.0.34.109 Android (33/13; 420dpi; 1080x2400; samsung; SM-S928U; b0q; qcom; en_US; 546558814)",
    "Instagram 309.1.0.41.113 Android (31/12; 480dpi; 1080x2160; OnePlus; IN2023; OnePlus8Pro; qcom; en_US; 543729040)",
    "Instagram 308.0.0.32.120 Android (30/11; 420dpi; 1080x2340; xiaomi; 2201116TG; thor; qcom; en_US; 541688905)",
]

_ALL_UAS = _DESKTOP_UAS + _MOBILE_UAS
_ua_lock = threading.Lock()
_ua_idx  = 0


def _ua(mobile: bool = False) -> str:
    global _ua_idx
    pool = _MOBILE_UAS if mobile else _ALL_UAS
    with _ua_lock:
        ua = pool[_ua_idx % len(pool)]
        _ua_idx += 1
        return ua


def _rand_delay(lo: float = 1.0, hi: float = 3.5):
    time.sleep(random.uniform(lo, hi))

# ─────────────────────────────────────────────────────────────────────────────
# Instagram-specific headers
# ─────────────────────────────────────────────────────────────────────────────

_IG_APP_ID = "936619743392459"


def _ig_browser_headers(ua: str) -> dict:
    return {
        "User-Agent":                ua,
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language":           "en-US,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate, br",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":            "document",
        "Sec-Fetch-Mode":            "navigate",
        "Sec-Fetch-Site":            "none",
        "Cache-Control":             "max-age=0",
        "DNT":                       "1",
    }


def _ig_mobile_headers(ua: str) -> dict:
    return {
        "User-Agent":            ua,
        "Accept":                "*/*",
        "Accept-Language":       "en-US",
        "Accept-Encoding":       "gzip, deflate",
        "X-IG-App-ID":           _IG_APP_ID,
        "X-IG-Capabilities":     "3brTvw==",
        "X-IG-Connection-Type":  "WIFI",
        "X-IG-Connection-Speed": f"{random.randint(3000, 10000)}kbps",
        "X-FB-HTTP-Engine":      "Liger",
        "X-Bloks-Version-Id":    "5f56efad68e1edec7801f630b5c122704ec5378ab6ae238c38bb8a3b91c9afd",
    }

# ─────────────────────────────────────────────────────────────────────────────
# Concurrency helpers
# ─────────────────────────────────────────────────────────────────────────────

executor     = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="dl")
_active      = {}
_active_lock = threading.Lock()
_picks       = {}
_picks_lock  = threading.Lock()
_dl_queue    = Queue()
_pending_bc  = {}


def _lock_user(uid: int) -> bool:
    with _active_lock:
        if _active.get(uid):
            return False
        _active[uid] = True
        return True


def _unlock_user(uid: int):
    with _active_lock:
        _active.pop(uid, None)

# ─────────────────────────────────────────────────────────────────────────────
# Telegram API helpers
# ─────────────────────────────────────────────────────────────────────────────

_tg_session = requests.Session()


def _post(method: str, **kwargs) -> dict:
    for attempt in range(3):
        try:
            r    = _tg_session.post(f"{API_URL}/{method}", timeout=120, **kwargs)
            data = r.json()
            if not data.get("ok") and attempt < 2:
                time.sleep(1.5)
                continue
            return data
        except Exception as e:
            log.error("POST %s #%d: %s", method, attempt + 1, e)
            if attempt < 2:
                time.sleep(2)
    return {}


def _get(method: str, **kwargs) -> dict:
    try:
        r = _tg_session.get(f"{API_URL}/{method}", timeout=35, **kwargs)
        return r.json()
    except Exception as e:
        log.error("GET %s: %s", method, e)
        return {}


def get_me() -> dict:
    try:
        return requests.get(f"{API_URL}/getMe", timeout=10).json().get("result", {})
    except Exception:
        return {}


def get_updates(offset=None) -> dict:
    params = {"timeout": 30, "allowed_updates": json.dumps(["message", "callback_query"])}
    if offset is not None:
        params["offset"] = offset
    return _get("getUpdates", params=params)


def send_msg(chat_id, text: str, markup=None, parse_mode: str = "HTML") -> dict:
    p: dict = {
        "chat_id": chat_id, "text": text[:4096],
        "parse_mode": parse_mode, "disable_web_page_preview": True,
    }
    if markup:
        p["reply_markup"] = json.dumps(markup)
    return _post("sendMessage", json=p)


def send_photo(chat_id, photo, caption: str = "", markup=None) -> dict:
    p: dict = {"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"}
    if isinstance(photo, str) and photo.startswith("http"):
        p["photo"] = photo
        if markup:
            p["reply_markup"] = json.dumps(markup)
        return _post("sendPhoto", json=p)
    data = {"chat_id": str(chat_id), "caption": caption[:1024], "parse_mode": "HTML"}
    if markup:
        data["reply_markup"] = json.dumps(markup)
    with open(photo, "rb") as f:
        return _post("sendPhoto", data=data, files={"photo": f})


def edit_text(chat_id, mid, text: str, markup=None) -> dict:
    p: dict = {
        "chat_id": chat_id, "message_id": mid,
        "text": text[:4096], "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if markup is not None:
        p["reply_markup"] = json.dumps(markup)
    return _post("editMessageText", json=p)


def delete_msg(chat_id, mid) -> dict:
    return _post("deleteMessage", json={"chat_id": chat_id, "message_id": mid})


def answer_cb(cb_id: str, text: str = "", alert: bool = False) -> dict:
    return _post("answerCallbackQuery",
                 json={"callback_query_id": cb_id, "text": text, "show_alert": alert})


def copy_message(to_chat_id, from_chat_id, message_id) -> dict:
    return _post("copyMessage", json={
        "chat_id": to_chat_id, "from_chat_id": from_chat_id, "message_id": message_id,
    })


def send_video(chat_id, path: str, caption: str = "") -> dict:
    with open(path, "rb") as f:
        return _post("sendVideo",
                     data={"chat_id": str(chat_id), "caption": caption[:1024],
                           "parse_mode": "HTML", "supports_streaming": "true"},
                     files={"video": f})


def send_audio(chat_id, path: str, caption: str = "") -> dict:
    with open(path, "rb") as f:
        return _post("sendAudio",
                     data={"chat_id": str(chat_id), "caption": caption[:1024],
                           "parse_mode": "HTML"},
                     files={"audio": f})

# ─────────────────────────────────────────────────────────────────────────────
# Premium animated status + real progress bar
# ─────────────────────────────────────────────────────────────────────────────

_LOADING_FRAMES = [
    "⏳ <b>Fetching video info...</b>",
    "🔍 <b>Analyzing source...</b>",
    "📡 <b>Connecting to server...</b>",
    "⬇️ <b>Preparing download...</b>",
    "⚙️ <b>Processing media...</b>",
    "🔄 <b>Almost there...</b>",
]

_UPLOAD_FRAMES = [
    "📤 <b>Uploading to Telegram...</b>",
    "🚀 <b>Sending your file...</b>",
    "⚡ <b>Just a moment...</b>",
    "📨 <b>Delivering your video...</b>",
]


def _progress_bar(pct: float, width: int = 12) -> str:
    filled = max(0, min(width, int(pct / 100 * width)))
    return "█" * filled + "░" * (width - filled)


class AnimatedStatus:
    """Cycles loading frames; also accepts real progress updates from yt-dlp."""

    def __init__(self, chat_id: int, message_id: int,
                 frames: list, interval: float = 4.0):
        self.chat_id    = chat_id
        self.message_id = message_id
        self.frames     = frames
        self.interval   = interval
        self._idx       = 0
        self._stop      = threading.Event()
        self._override  = None        # set when real progress arrives
        self._last_edit = 0.0
        self._thread    = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def update_progress(self, pct_str: str, speed: str = "", eta: str = ""):
        """Called by yt-dlp progress hook — throttled to 1 edit / 3.5 s."""
        now = time.time()
        if now - self._last_edit < 3.5:
            return
        self._last_edit = now
        try:
            pct_f = float(pct_str.strip().rstrip("%"))
        except (ValueError, AttributeError):
            return
        bar  = _progress_bar(pct_f)
        parts = [f"⬇️ <b>Downloading...</b>", f"<code>{bar}</code> <b>{pct_f:.0f}%</b>"]
        if speed:
            parts.append(f"⚡ {speed}")
        if eta and eta != "Unknown ETA":
            parts.append(f"⏱ ETA: {eta}")
        self._override = "\n".join(parts)

    def _run(self):
        while not self._stop.wait(self.interval):
            msg = self._override or self.frames[self._idx % len(self.frames)]
            if not self._override:
                self._idx += 1
            try:
                edit_text(self.chat_id, self.message_id, msg)
                self._last_edit = time.time()
            except Exception:
                pass

# ─────────────────────────────────────────────────────────────────────────────
# File utilities
# ─────────────────────────────────────────────────────────────────────────────

def _rm(*paths):
    for p in paths:
        if not p:
            continue
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def _find(prefix: str) -> Optional[str]:
    hits = [f for f in glob.glob(f"{prefix}*")
            if not f.endswith((".part", ".ytdl", ".temp"))]
    if not hits:
        return None
    for ext in (".mp4", ".mp3", ".m4a", ".webm", ".mkv", ".avi", ".mov"):
        m = [f for f in hits if f.endswith(ext)]
        if m:
            return m[0]
    return hits[0]


def _fmt_size(path: str) -> str:
    s = os.path.getsize(path)
    if s >= 1_073_741_824:
        return f"{s / 1_073_741_824:.2f} GB"
    return f"{s / 1_048_576:.1f} MB"


def _fmt_dur(s) -> str:
    if not s:
        return ""
    s   = int(s)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _short(text: str, n: int = 80) -> str:
    if not text:
        return ""
    return text[:n] + "…" if len(text) > n else text


def _sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', name)

# ─────────────────────────────────────────────────────────────────────────────
# ffmpeg — probe + H264/AAC/faststart fix
# ─────────────────────────────────────────────────────────────────────────────

def _probe_codecs(path: str) -> tuple:
    if not (FFPROBE or FFMPEG):
        return "", ""
    try:
        if FFPROBE:
            r = subprocess.run(
                [FFPROBE, "-v", "quiet", "-show_streams", "-print_format", "json", path],
                capture_output=True, text=True, timeout=15,
            )
            data   = json.loads(r.stdout or "{}")
            vc = ac = ""
            for s in data.get("streams", []):
                if s.get("codec_type") == "video" and not vc:
                    vc = s.get("codec_name", "")
                if s.get("codec_type") == "audio" and not ac:
                    ac = s.get("codec_name", "")
            return vc, ac
        r  = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True, timeout=15)
        vc = re.search(r"Video:\s*(\w+)", r.stderr)
        ac = re.search(r"Audio:\s*(\w+)", r.stderr)
        return (vc.group(1) if vc else ""), (ac.group(1) if ac else "")
    except Exception:
        return "", ""


def _fix_for_telegram(src: str) -> str:
    """Ensure H264 + AAC + faststart.  Re-encodes only when necessary."""
    if not FFMPEG:
        return src
    vc, ac   = _probe_codecs(src)
    is_h264  = vc.lower() in ("h264", "avc", "avc1")
    is_aac   = ac.lower() in ("aac", "mp3", "mp4a")
    is_mp4   = src.lower().endswith(".mp4")
    out      = src.rsplit(".", 1)[0] + "_tg.mp4"
    if is_h264 and is_aac and is_mp4:
        cmd = [FFMPEG, "-i", src, "-c", "copy", "-movflags", "+faststart", "-y", out]
    else:
        log.info("Re-encoding %s (v=%s a=%s)", src, vc, ac)
        cmd = [FFMPEG, "-i", src,
               "-c:v", "libx264", "-preset", "fast", "-crf", "23",
               "-c:a", "aac", "-b:a", "128k",
               "-movflags", "+faststart", "-y", out]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            _rm(src)
            return out
    except Exception as e:
        log.warning("ffmpeg fix error: %s", e)
    return src

# ─────────────────────────────────────────────────────────────────────────────
# yt-dlp base helpers
# ─────────────────────────────────────────────────────────────────────────────

_QUALITY_HEIGHTS = {
    "144": 144, "240": 240, "360": 360, "480": 480,
    "720": 720, "1080": 1080, "1440": 1440, "2160": 2160,
}
_YT_PLAYER_CLIENTS = ["ios", "mweb", "android_embedded", "android", "web"]


def _yt_extractor_args(clients: list = None) -> dict:
    return {"youtube": {"player_client": clients or _YT_PLAYER_CLIENTS}}


def _fmt_str(quality: str) -> str:
    if quality == "mp3":
        return "bestaudio/best"
    if quality == "best":
        # Absolute best: prefer 60fps, allow any codec including VP9/AV1
        return (
            "bestvideo[fps>=60]+bestaudio[ext=m4a]"
            "/bestvideo[fps>=60]+bestaudio"
            "/bestvideo+bestaudio[ext=m4a]"
            "/bestvideo+bestaudio"
            "/best"
        )
    if quality == "fast":
        quality = "720"
    h = _QUALITY_HEIGHTS.get(str(quality), 720)
    if h <= 480:
        # Low quality: H264+AAC for maximum compatibility
        return (
            f"bestvideo[vcodec^=avc1][height<={h}][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]"
            f"/bestvideo[vcodec^=avc][height<={h}][ext=mp4]+bestaudio"
            f"/bestvideo[height<={h}]+bestaudio"
            f"/best[height<={h}]/best"
        )
    if h <= 720:
        # 720p: prefer 60fps H264, fallback gracefully
        return (
            f"bestvideo[vcodec^=avc1][height<={h}][fps>=60][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]"
            f"/bestvideo[vcodec^=avc1][height<={h}][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]"
            f"/bestvideo[vcodec^=avc][height<={h}][ext=mp4]+bestaudio"
            f"/bestvideo[height<={h}]+bestaudio"
            f"/best[height<={h}]/best"
        )
    # 1080p+: prefer 60fps, allow VP9/AV1 for better quality at high res
    return (
        f"bestvideo[height<={h}][fps>=60][ext=mp4]+bestaudio[ext=m4a]"
        f"/bestvideo[height<={h}][fps>=60]+bestaudio"
        f"/bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]"
        f"/bestvideo[height<={h}]+bestaudio"
        f"/best[height<={h}]/best"
    )


def _base_opts(quality: str, use_cookies: bool = True, extra_headers: dict = None) -> dict:
    audio_only = quality == "mp3"
    o: dict = {
        "format":              _fmt_str(quality),
        "quiet":               True,
        "no_warnings":         True,
        "noplaylist":          True,
        "http_headers":        {"User-Agent": _ua()},
        "retries":             8,
        "fragment_retries":    12,
        "socket_timeout":      30,
        "prefer_ffmpeg":       True,
        "merge_output_format": "mp4",
        "outtmpl":             "",
    }
    if extra_headers:
        o["http_headers"].update(extra_headers)
    if FFMPEG:
        o["ffmpeg_location"] = FFMPEG
    if use_cookies and HAS_COOKIES:
        o["cookiefile"] = COOKIES
    if audio_only:
        o["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        o["postprocessor_args"] = {
            "merger": ["-c:v", "libx264", "-preset", "fast", "-crf", "23",
                       "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"],
        }
    return o


def _make_progress_hook(anim: Optional[AnimatedStatus]):
    """yt-dlp progress hook that feeds real percentages into AnimatedStatus."""
    def hook(d):
        if not anim or d.get("status") != "downloading":
            return
        anim.update_progress(
            d.get("_percent_str", ""),
            d.get("_speed_str", "").strip(),
            d.get("_eta_str", "").strip(),
        )
    return hook


def _ytdlp_run(url: str, quality: str, extra: dict = None,
               anim: AnimatedStatus = None) -> tuple:
    prefix = f"/tmp/dl_{int(time.time()*1000)}_{random.randint(1000,9999)}_"
    o = _base_opts(quality)
    o["outtmpl"] = f"{prefix}%(title).60s.%(ext)s"
    if extra:
        o.update(extra)
    if anim:
        o["progress_hooks"] = [_make_progress_hook(anim)]
    with yt_dlp.YoutubeDL(o) as ydl:
        info  = ydl.extract_info(url, download=True)
        title = (info or {}).get("title", "")
    path = _find(prefix)
    if not path:
        raise ValueError("yt-dlp finished but no output file found.")
    return path, title


def _ytdlp_meta(url: str) -> dict:
    """Fetch rich metadata: title, sizes, HDR, fps, playlist info — multi-client aware."""
    base_opts: dict = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "skip_download": True, "socket_timeout": 25,
        "http_headers": {"User-Agent": _ua()},
        "extractor_args": _yt_extractor_args(),
        "age_limit": 99,
    }
    if FFMPEG:
        base_opts["ffmpeg_location"] = FFMPEG
    if HAS_COOKIES:
        base_opts["cookiefile"] = COOKIES

    # Try playlist-aware fetch first so we can report count
    playlist_opts = {**base_opts, "noplaylist": False, "playlistend": 1,
                     "extract_flat": "in_playlist"}
    is_playlist   = False
    playlist_count = 0
    playlist_title = ""

    info: dict = {}
    for attempt_opts in [base_opts, {**base_opts, "extractor_args": _yt_extractor_args(["ios"])}]:
        try:
            with yt_dlp.YoutubeDL(attempt_opts) as ydl:
                raw = ydl.extract_info(url, download=False) or {}
            if raw.get("_type") == "playlist" or raw.get("entries"):
                is_playlist    = True
                playlist_count = raw.get("playlist_count") or len(list(raw.get("entries") or []))
                playlist_title = raw.get("title", "YouTube Playlist")
                # Use first entry for format info
                entries = list(raw.get("entries") or [])
                if entries:
                    first_url = entries[0].get("url") or entries[0].get("webpage_url", "")
                    if first_url:
                        try:
                            with yt_dlp.YoutubeDL(attempt_opts) as ydl2:
                                info = ydl2.extract_info(first_url, download=False) or {}
                        except Exception:
                            pass
                if not info:
                    info = raw
            else:
                info = raw
            if info:
                break
        except Exception:
            pass

    formats  = info.get("formats", [])
    duration = info.get("duration") or 0

    # Parse available heights, HDR, max fps
    heights: set = set()
    has_hdr       = False
    max_fps       = 30
    for f in formats:
        h   = f.get("height")
        fps = f.get("fps") or 0
        dr  = (f.get("dynamic_range") or "").upper()
        if h and isinstance(h, int) and h >= 100:
            heights.add(h)
        if fps and isinstance(fps, (int, float)) and fps > max_fps:
            max_fps = int(fps)
        if dr in ("HDR", "HDR10", "HDR10+", "HLG", "DOLBY_VISION"):
            has_hdr = True

    # Best audio bitrate for size estimation
    best_abr = max(
        (f.get("abr") or f.get("tbr") or 0)
        for f in formats
        if f.get("vcodec", "") in ("none", "") and f.get("acodec", "") not in ("none", "")
    ) if formats else 0

    # Size estimates per standard height
    def _est(target_h: int) -> str:
        if not duration:
            return ""
        best_vbr = 0
        for f in formats:
            fh = f.get("height") or 0
            if fh < target_h * 0.75 or fh > target_h * 1.25:
                continue
            vbr = f.get("vbr") or f.get("tbr") or 0
            if vbr > best_vbr:
                best_vbr = vbr
        if not best_vbr:
            return ""
        mb = (best_vbr + best_abr) * 1000 / 8 * duration / 1_048_576
        if mb >= 1024:
            return f"~{mb/1024:.1f} GB"
        return f"~{mb:.0f} MB" if mb >= 1 else f"~{mb*1024:.0f} KB"

    size_estimates = {h: _est(h) for h in heights}

    return {
        "title":           info.get("title", playlist_title or "Video"),
        "duration":        duration,
        "thumb":           info.get("thumbnail", ""),
        "channel":         info.get("uploader") or info.get("channel", ""),
        "view_count":      info.get("view_count"),
        "upload_date":     info.get("upload_date", ""),
        "like_count":      info.get("like_count"),
        "available_heights": heights,
        "has_hdr":         has_hdr,
        "max_fps":         max_fps,
        "size_estimates":  size_estimates,
        "is_live":         info.get("is_live", False),
        "is_playlist":     is_playlist,
        "playlist_count":  playlist_count,
        "playlist_title":  playlist_title,
    }

# ─────────────────────────────────────────────────────────────────────────────
# Platform: YouTube
# ─────────────────────────────────────────────────────────────────────────────

def _yt_normalize(url: str) -> str:
    url = url.strip()
    m = re.search(r'(?:youtube\.com/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})', url)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    return url


def _is_yt_playlist(url: str) -> bool:
    """True only when the URL points to a full playlist page (not a video with list= param)."""
    has_list  = bool(re.search(r'[?&]list=[A-Za-z0-9_-]+', url))
    has_video = bool(
        re.search(r'[?&]v=[A-Za-z0-9_-]{11}', url) or
        re.search(r'youtu\.be/[A-Za-z0-9_-]{11}', url) or
        re.search(r'youtube\.com/shorts/[A-Za-z0-9_-]{11}', url)
    )
    is_pl_page = "/playlist" in url
    return has_list and (is_pl_page or not has_video)


def _dl_youtube(url: str, quality: str, anim: AnimatedStatus = None) -> tuple:
    url     = _yt_normalize(url)
    errors  = []
    # Each tuple: (extractor_args or None)
    attempts = [
        {"extractor_args": _yt_extractor_args(["ios"])},
        {"extractor_args": _yt_extractor_args(["mweb", "ios"])},
        {"extractor_args": _yt_extractor_args(["android_embedded", "android"])},
        {"extractor_args": _yt_extractor_args(_YT_PLAYER_CLIENTS)},
        {},
    ]
    for extra in attempts:
        prefix = f"/tmp/dl_{int(time.time()*1000)}_{random.randint(1000,9999)}_"
        o = _base_opts(quality)
        o["outtmpl"]   = f"{prefix}%(title).60s.%(ext)s"
        o["age_limit"] = 99
        o.update(extra)
        if anim:
            o["progress_hooks"] = [_make_progress_hook(anim)]
        try:
            with yt_dlp.YoutubeDL(o) as ydl:
                info  = ydl.extract_info(url, download=True)
                title = (info or {}).get("title", "YouTube Video")
            path = _find(prefix)
            if not path:
                raise ValueError("No output file found.")
            if quality != "mp3":
                path = _fix_for_telegram(path)
            return path, title
        except Exception as e:
            err = str(e)
            errors.append(err[:120])
            log.warning("YT attempt extra=%s failed: %s", list(extra.keys()), err[:80])
            for f in glob.glob(f"{prefix}*"):
                _rm(f)
            if any(k in err.lower() for k in ["video unavailable", "private video",
                                               "has been removed", "not available"]):
                break

    needs_cookies = any("sign in" in e.lower() or "age" in e.lower()
                        or "cookie" in e.lower() for e in errors)
    raise ValueError(
        "❌ <b>YouTube Download Failed</b>\n\n"
        + ("🍪 <b>Sign-in required.</b> Add <code>cookies.txt</code> to unlock.\n\n"
           if needs_cookies else "")
        + f"<i>{errors[-1][:180] if errors else 'unknown error'}</i>"
    )


def _dl_youtube_playlist(url: str, quality: str,
                         chat_id: int, status_id: Optional[int]) -> tuple:
    """Download each video in a YouTube playlist, sending each one as it finishes."""
    # Fetch flat playlist (fast — no per-video info yet)
    flat_opts: dict = {
        "quiet": True, "no_warnings": True, "noplaylist": False,
        "extract_flat": "in_playlist", "socket_timeout": 30,
        "http_headers": {"User-Agent": _ua()},
        "extractor_args": _yt_extractor_args(["ios"]),
    }
    if HAS_COOKIES:
        flat_opts["cookiefile"] = COOKIES

    try:
        with yt_dlp.YoutubeDL(flat_opts) as ydl:
            raw      = ydl.extract_info(url, download=False) or {}
            entries  = list(raw.get("entries") or [])
            pl_title = raw.get("title", "Playlist")
    except Exception as e:
        raise ValueError(f"Could not fetch playlist: {e}")

    total = len(entries)
    if not total:
        raise ValueError("Playlist is empty or private.")

    done = failed = 0
    for i, entry in enumerate(entries, 1):
        entry_url   = entry.get("url") or entry.get("webpage_url", "")
        entry_title = entry.get("title", f"Video {i}")
        if not entry_url:
            continue

        # Progress update
        if status_id:
            try:
                edit_text(chat_id, status_id,
                    f"📋 <b>{_short(pl_title, 55)}</b>\n\n"
                    f"⬇️ <b>{i} / {total}</b>\n"
                    f"<i>{_short(entry_title, 65)}</i>")
            except Exception:
                pass

        path: Optional[str] = None
        try:
            path, title = _dl_youtube(entry_url, quality)
            _record_download("youtube")
            size     = _fmt_size(path)
            ql       = _QUAL_LABELS.get(quality, f"{quality}p")
            is_audio = quality == "mp3"
            cap = (
                f"{'🎵' if is_audio else '🎥'} <b>{_short(title, 80)}</b>\n"
                f"<i>{i}/{total} — {_short(pl_title, 45)}</i>\n\n"
                f"📦 {size}  •  {ql}\n⚡ MeraDownload4K"
            )
            if is_audio:
                send_audio(chat_id, path, caption=cap)
            else:
                send_video(chat_id, path, caption=cap)
            done += 1
        except Exception as e:
            failed += 1
            log.warning("Playlist %d/%d failed: %s", i, total, e)
            send_msg(chat_id,
                f"⚠️ <b>Skipped {i}/{total}</b>\n<i>{_short(str(e), 120)}</i>")
        finally:
            _rm(path)

        time.sleep(0.5)

    return done, failed, total, pl_title


def _playlist_worker(chat_id: int, user_id: int,
                     url: str, quality: str, status_id: Optional[int]):
    try:
        done, failed, total, pl_title = _dl_youtube_playlist(url, quality, chat_id, status_id)
        summary = (
            f"✅ <b>Playlist Complete!</b>\n\n"
            f"📋 {_short(pl_title, 60)}\n\n"
            f"✅ Downloaded: <b>{done}</b>\n"
            f"❌ Failed:     <b>{failed}</b>\n"
            f"📦 Total:      <b>{total}</b>"
        )
        if status_id:
            edit_text(chat_id, status_id, summary)
        else:
            send_msg(chat_id, summary)
    except Exception as e:
        err = str(e)
        if "<b>" not in err:
            err = f"⚠️ <b>Playlist Failed</b>\n\n<i>{_short(err, 300)}</i>"
        if status_id:
            edit_text(chat_id, status_id, err)
        else:
            send_msg(chat_id, err)
    finally:
        _unlock_user(user_id)

# ─────────────────────────────────────────────────────────────────────────────
# Platform: Instagram
# ─────────────────────────────────────────────────────────────────────────────

def _ig_shortcode(url: str) -> Optional[str]:
    m = re.search(r'/(?:p|reel|tv)/([A-Za-z0-9_-]+)', url)
    return m.group(1) if m else None


def _ig_normalize(url: str) -> str:
    base = url.split("?")[0].rstrip("/")
    base = re.sub(r'^http://', 'https://', base)
    if "instagram.com" in base and "//www." not in base:
        base = base.replace("//instagram.com", "//www.instagram.com")
    return base + "/"


def _ig_load_cookies(session: requests.Session):
    """Load cookies.txt into a requests Session for Instagram."""
    if not HAS_COOKIES:
        return
    try:
        jar = http.cookiejar.MozillaCookieJar(COOKIES)
        jar.load(ignore_discard=True, ignore_expires=True)
        session.cookies.update(jar)
    except Exception as e:
        log.debug("IG cookie load error: %s", e)


def _ig_embed(url: str) -> tuple:
    sc = _ig_shortcode(url)
    if not sc:
        raise ValueError("No shortcode.")
    embed_url = f"https://www.instagram.com/p/{sc}/embed/captioned/"
    ua   = _MOBILE_UAS[0]
    hdrs = _ig_browser_headers(ua)
    hdrs["Referer"] = "https://www.instagram.com/"
    sess = requests.Session()
    _ig_load_cookies(sess)
    html = sess.get(embed_url, headers=hdrs, timeout=25).text
    for pat in [
        r'"video_url"\s*:\s*"([^"]+)"',
        r'<video[^>]+src="([^"]+)"',
        r'"contentUrl"\s*:\s*"([^"]+\.mp4[^"]*)"',
        r'<meta property="og:video" content="([^"]+)"',
        r'"playbackUrl"\s*:\s*"([^"]+)"',
    ]:
        hit = re.search(pat, html)
        if hit:
            vurl  = hit.group(1).replace("\\u0026", "&").replace("\\/", "/").replace("&amp;", "&")
            tm    = re.search(r'<title>([^<]+)</title>', html)
            title = tm.group(1).strip() if tm else "Instagram Video"
            out   = f"/tmp/ig_embed_{int(time.time())}.mp4"
            with sess.get(vurl, headers=hdrs, stream=True, timeout=90) as r:
                r.raise_for_status()
                with open(out, "wb") as fh:
                    for chunk in r.iter_content(65536):
                        fh.write(chunk)
            if os.path.exists(out) and os.path.getsize(out) > 10_000:
                return out, title
    raise ValueError("Embed: no media URL found.")


def _ig_scrape(url: str, ua: str) -> tuple:
    hdrs = {**_ig_browser_headers(ua), "X-IG-App-ID": _IG_APP_ID}
    sess = requests.Session()
    _ig_load_cookies(sess)
    if HAS_COOKIES:
        try:
            raw = open(COOKIES).read()
            ck  = "; ".join(
                f"{p[5]}={p[6]}"
                for line in raw.splitlines()
                if not line.startswith("#")
                for p in [line.split("\t")]
                if len(p) >= 7 and "instagram.com" in p[0]
            )
            if ck:
                hdrs["Cookie"] = ck
        except Exception:
            pass
    html = sess.get(url, headers=hdrs, timeout=25).text
    for pat in [
        r'"video_url"\s*:\s*"([^"]+)"',
        r'"playback_url"\s*:\s*"([^"]+)"',
        r'<meta property="og:video" content="([^"]+)"',
        r'"contentUrl"\s*:\s*"([^"]+\.mp4[^"]*)"',
        r'"videoUrl"\s*:\s*"([^"]+)"',
    ]:
        hit = re.search(pat, html)
        if hit:
            vurl  = hit.group(1).replace("\\u0026", "&").replace("\\/", "/").replace("&amp;", "&")
            tm    = re.search(r'"title"\s*:\s*"([^"]+)"', html)
            title = tm.group(1) if tm else "Instagram Video"
            out   = f"/tmp/ig_scrape_{int(time.time())}.mp4"
            with sess.get(vurl, headers=hdrs, stream=True, timeout=90) as r:
                r.raise_for_status()
                with open(out, "wb") as fh:
                    for chunk in r.iter_content(65536):
                        fh.write(chunk)
            if os.path.exists(out) and os.path.getsize(out) > 10_000:
                return out, title
    raise ValueError("Scrape: no media URL found.")


def _ig_instaloader(url: str) -> tuple:
    sc       = _ig_shortcode(url)
    is_story = "/stories/" in url
    if is_story:
        m = re.search(r'/stories/([^/]+)/(\d+)', url)
        if not m:
            raise ValueError("Cannot parse story URL.")
        username = m.group(1)
        story_id = int(m.group(2))
    elif not sc:
        raise ValueError("No shortcode.")

    stamp  = int(time.time())
    tmpdir = f"/tmp/ig_il_{stamp}"
    out    = f"/tmp/ig_il_{stamp}.mp4"
    os.makedirs(tmpdir, exist_ok=True)
    try:
        import instaloader as il
        L = il.Instaloader(
            download_videos=True, download_video_thumbnails=False,
            download_geotags=False, download_comments=False,
            save_metadata=False, compress_json=False,
            quiet=True, user_agent=_MOBILE_UAS[0],
        )
        L.dirname_pattern  = tmpdir
        L.filename_pattern = "video"
        if HAS_COOKIES:
            jar = http.cookiejar.MozillaCookieJar(COOKIES)
            jar.load(ignore_discard=True, ignore_expires=True)
            L.context._session.cookies.update(jar)
        if is_story:
            profile = il.Profile.from_username(L.context, username)
            for story in L.get_stories(userids=[profile.userid]):
                for item in story.get_items():
                    if item.mediaid == story_id:
                        L.download_storyitem(item, target=tmpdir)
                        break
            title = username
        else:
            post  = il.Post.from_shortcode(L.context, sc)
            title = (post.caption or sc)[:80].split("\n")[0]
            L.download_post(post, target=tmpdir)
        found = next(
            (os.path.join(tmpdir, f) for f in os.listdir(tmpdir) if f.endswith(".mp4")),
            None,
        )
        if not found:
            raise ValueError("No mp4 in instaloader output.")
        shutil.move(found, out)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return out, title
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise


def _ig_ytdlp(url: str, quality: str, ua: str, anim: AnimatedStatus = None) -> tuple:
    prefix = f"/tmp/dl_ig_{int(time.time()*1000)}_{random.randint(1000,9999)}_"
    o = _base_opts(quality, use_cookies=False)
    o["outtmpl"]      = f"{prefix}%(title).60s.%(ext)s"
    o["http_headers"] = _ig_mobile_headers(ua)
    o["retries"]      = 4
    if HAS_COOKIES:
        o["cookiefile"] = COOKIES
    if anim:
        o["progress_hooks"] = [_make_progress_hook(anim)]
    with yt_dlp.YoutubeDL(o) as ydl:
        info  = ydl.extract_info(url, download=True)
        title = (info or {}).get("title", "Instagram Video")
    path = _find(prefix)
    if not path:
        raise ValueError("No output file.")
    return path, title


def _dl_instagram(url: str, quality: str = "720",
                  anim: AnimatedStatus = None) -> tuple:
    """
    7-method chain ordered for maximum reliability on public content.
    1. Embed page scrape (best for public reels, no login needed)
    2. HTML page scrape (og:video / JSON patterns)
    3. instaloader (best with cookies, still tries without)
    4-5. yt-dlp mobile UA (GraphQL + no-graphql)
    6. yt-dlp desktop UA
    7. yt-dlp IG App UA
    """
    url    = _ig_normalize(url)
    errors = []

    # M1 — embed scrape
    try:
        return _ig_embed(url)
    except Exception as e:
        errors.append(f"embed: {str(e)[:80]}")

    # M2 — HTML scrape (2 mobile UAs)
    for ua in _MOBILE_UAS[:2]:
        try:
            return _ig_scrape(url, ua)
        except Exception as e:
            errors.append(f"scrape: {str(e)[:80]}")

    # M3 — instaloader
    try:
        return _ig_instaloader(url)
    except Exception as e:
        errors.append(f"il: {str(e)[:80]}")
        _rand_delay(1, 3)

    # M4 — yt-dlp mobile GraphQL
    for ua in _MOBILE_UAS[:3]:
        try:
            return _ig_ytdlp(url, quality, ua, anim)
        except Exception as e:
            errors.append(f"ytm: {str(e)[:80]}")
            _rand_delay(2, 4)

    # M5 — yt-dlp desktop
    for ua in _DESKTOP_UAS[:2]:
        try:
            return _ig_ytdlp(url, quality, ua, anim)
        except Exception as e:
            errors.append(f"ytd: {str(e)[:80]}")
            _rand_delay(2, 4)

    # M6 — yt-dlp IG App UA
    for ua in _IG_APP_UAS[:2]:
        try:
            return _ig_ytdlp(url, quality, ua, anim)
        except Exception as e:
            errors.append(f"ytapp: {str(e)[:80]}")
            _rand_delay(2, 4)

    # M7 — yt-dlp desktop no-graphql
    for ua in _DESKTOP_UAS[:1]:
        try:
            prefix = f"/tmp/dl_ig_{int(time.time()*1000)}_{random.randint(1000,9999)}_"
            o = _base_opts(quality)
            o["outtmpl"]      = f"{prefix}%(title).60s.%(ext)s"
            o["http_headers"] = _ig_browser_headers(ua)
            if HAS_COOKIES:
                o["cookiefile"] = COOKIES
            with yt_dlp.YoutubeDL(o) as ydl:
                info  = ydl.extract_info(url, download=True)
                title = (info or {}).get("title", "Instagram Video")
            path = _find(prefix)
            if path:
                return path, title
        except Exception as e:
            errors.append(f"ytfb: {str(e)[:80]}")

    is_private = any("login" in e.lower() or "private" in e.lower()
                     or "not logged" in e.lower() for e in errors)
    if is_private and not HAS_COOKIES:
        raise ValueError(
            "🔒 <b>Private Content</b>\n\n"
            "This post requires an Instagram login.\n"
            "Add <code>cookies.txt</code> (exported from a logged-in browser)\n"
            "to the bot folder to unlock private posts."
        )
    raise ValueError(
        "📸 <b>Instagram Extraction Failed</b>\n\n"
        "All 7 methods were tried.\n\n"
        "<b>Tips:</b>\n"
        "• Make sure the post is <b>public</b>\n"
        "• For private content, add <code>cookies.txt</code>\n"
        "• Try again in a few minutes (rate limit)\n\n"
        f"<i>{errors[-1][:180] if errors else 'unknown error'}</i>"
    )

# ─────────────────────────────────────────────────────────────────────────────
# Platform: Facebook
# ─────────────────────────────────────────────────────────────────────────────

def _dl_facebook(url: str, quality: str = "720",
                 anim: AnimatedStatus = None) -> tuple:
    """3-method chain: desktop → mobile UA → mbasic URL rewrite."""
    errors = []

    def _run(dl_url: str, extra_hdr: dict) -> tuple:
        prefix = f"/tmp/dl_fb_{int(time.time()*1000)}_{random.randint(1000,9999)}_"
        o = _base_opts(quality)
        o["outtmpl"]      = f"{prefix}%(title).60s.%(ext)s"
        o["http_headers"] = {**o["http_headers"], **extra_hdr}
        if anim:
            o["progress_hooks"] = [_make_progress_hook(anim)]
        with yt_dlp.YoutubeDL(o) as ydl:
            info  = ydl.extract_info(dl_url, download=True)
            title = (info or {}).get("title", "Facebook Video")
        path = _find(prefix)
        if not path:
            raise ValueError("No output file.")
        return _fix_for_telegram(path), title

    # M1 — desktop UA + Facebook referer
    try:
        return _run(url, {
            "User-Agent":      _DESKTOP_UAS[0],
            "Accept-Language": "en-US,en;q=0.9",
            "Referer":         "https://www.facebook.com/",
            "Accept":          "text/html,*/*;q=0.9",
        })
    except Exception as e:
        errors.append(f"M1: {str(e)[:80]}")

    # M2 — mobile UA
    try:
        return _run(url, {
            "User-Agent":      _MOBILE_UAS[0],
            "Accept-Language": "en-US,en;q=0.9",
            "Referer":         "https://www.facebook.com/",
        })
    except Exception as e:
        errors.append(f"M2: {str(e)[:80]}")

    # M3 — mbasic.facebook.com rewrite
    try:
        mbasic = re.sub(r'https?://(?:www\.)?facebook\.com',
                        'https://mbasic.facebook.com', url)
        return _run(mbasic, {
            "User-Agent": _MOBILE_UAS[1],
            "Referer":    "https://mbasic.facebook.com/",
        })
    except Exception as e:
        errors.append(f"M3: {str(e)[:80]}")

    raise ValueError(
        "📘 <b>Facebook Download Failed</b>\n\n"
        "• Verify the video is <b>public</b>\n"
        "• Private or age-restricted videos need <code>cookies.txt</code>\n\n"
        f"<i>{errors[-1][:180] if errors else 'unknown error'}</i>"
    )

# ─────────────────────────────────────────────────────────────────────────────
# Platform: Snapchat — CDN scrape + fallbacks
# ─────────────────────────────────────────────────────────────────────────────

_SC_SESS = requests.Session()
_SC_SESS.headers.update({
    "User-Agent":      _MOBILE_UAS[0],
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
})


def _sc_resolve_url(url: str) -> str:
    """Follow short links (snapchat.com/t/XXX → real page)."""
    try:
        r = _SC_SESS.head(url, allow_redirects=True, timeout=15)
        return r.url
    except Exception:
        return url


def _sc_cdn_download(cdn_url: str, title: str = "Snapchat Video") -> tuple:
    """Download directly from a Snapchat CDN URL, force .mp4."""
    out = f"/tmp/sc_cdn_{int(time.time())}.mp4"
    with _SC_SESS.get(cdn_url, stream=True, timeout=90) as r:
        r.raise_for_status()
        with open(out, "wb") as fh:
            for chunk in r.iter_content(65536):
                fh.write(chunk)
    if os.path.exists(out) and os.path.getsize(out) > 10_000:
        return out, title
    raise ValueError("CDN file too small or empty.")


def _sc_scrape_html(url: str) -> tuple:
    """Scrape Snapchat page for mediaUrl / og:video."""
    real_url = _sc_resolve_url(url)
    html = _SC_SESS.get(real_url, timeout=25).text
    # Pattern 1: JSON "mediaUrl" field
    for pat in [
        r'"mediaUrl"\s*:\s*"([^"]+)"',
        r'"snapUrl"\s*:\s*"([^"]+)"',
        r'"contentUrl"\s*:\s*"([^"]+)"',
        r'<meta property="og:video" content="([^"]+)"',
        r'<meta property="og:video:url" content="([^"]+)"',
        r'"playbackUrl"\s*:\s*"([^"]+)"',
        r'"media_url"\s*:\s*"([^"]+)"',
    ]:
        hit = re.search(pat, html)
        if hit:
            cdn = hit.group(1).replace("\\u0026", "&").replace("\\/", "/").replace("&amp;", "&")
            if cdn.startswith("http"):
                tm    = re.search(r'<title>([^<]+)</title>', html)
                title = (tm.group(1).strip() if tm else "Snapchat Video")[:80]
                return _sc_cdn_download(cdn, title)
    # Pattern 2: all CDN URLs in page
    cdns = re.findall(r'https://[^"\'<> ]+\.(?:mp4|m4v|mov)[^"\'<> ]*', html)
    cdns += re.findall(r'https://cf-st\.sc-cdn\.net[^"\'<> ]+', html)
    cdns += re.findall(r'https://bs\.sc-cdn\.net[^"\'<> ]+', html)
    for cdn in cdns:
        try:
            r = _SC_SESS.head(cdn, timeout=10, allow_redirects=True)
            ct = r.headers.get("Content-Type", "")
            if "video" in ct or "octet" in ct:
                return _sc_cdn_download(cdn)
        except Exception:
            continue
    raise ValueError("No CDN URL found in HTML.")


def _sc_ytdlp(url: str) -> tuple:
    """yt-dlp fallback for Snapchat — forces .mp4 output."""
    prefix = f"/tmp/dl_sc_{int(time.time()*1000)}_{random.randint(1000,9999)}_"
    o: dict = {
        "quiet": True, "no_warnings": True, "format": "best[ext=mp4]/best",
        "outtmpl": f"{prefix}%(title).60s.%(ext)s",
        "retries": 4, "socket_timeout": 30,
        "http_headers": {"User-Agent": _MOBILE_UAS[0]},
    }
    if FFMPEG:
        o["ffmpeg_location"] = FFMPEG
    if HAS_COOKIES:
        o["cookiefile"] = COOKIES
    with yt_dlp.YoutubeDL(o) as ydl:
        info  = ydl.extract_info(url, download=True)
        title = (info or {}).get("title", "Snapchat Video")
    path = _find(prefix)
    if not path:
        raise ValueError("No output file.")
    # Rename to .mp4 regardless of extension to bypass Telegram's check
    if not path.endswith(".mp4"):
        mp4 = path.rsplit(".", 1)[0] + ".mp4"
        if FFMPEG:
            subprocess.run(
                [FFMPEG, "-i", path, "-c", "copy", "-y", mp4],
                capture_output=True, timeout=120,
            )
            if os.path.exists(mp4) and os.path.getsize(mp4) > 0:
                _rm(path)
                path = mp4
        else:
            os.rename(path, mp4)
            path = mp4
    return path, title


def _dl_snapchat(url: str) -> tuple:
    """
    M1: HTML scrape → CDN download (bypasses yt-dlp extension issue entirely)
    M2: yt-dlp + forced .mp4 output
    """
    errors = []
    try:
        path, title = _sc_scrape_html(url)
        return _fix_for_telegram(path), title
    except Exception as e:
        errors.append(f"M1-scrape: {str(e)[:100]}")
        log.warning("SC M1 failed: %s", str(e)[:80])

    try:
        path, title = _sc_ytdlp(url)
        return _fix_for_telegram(path), title
    except Exception as e:
        errors.append(f"M2-ytdlp: {str(e)[:100]}")
        log.warning("SC M2 failed: %s", str(e)[:80])

    raise ValueError(
        "👻 <b>Snapchat Download Failed</b>\n\n"
        "• Spotlight videos and public stories are supported\n"
        "• Private snaps cannot be downloaded\n\n"
        f"<i>{errors[-1][:180] if errors else 'unknown error'}</i>"
    )

# ─────────────────────────────────────────────────────────────────────────────
# Platform: Reddit
# ─────────────────────────────────────────────────────────────────────────────

def _dl_reddit(url: str, quality: str = "720",
               anim: AnimatedStatus = None) -> tuple:
    """
    Reddit video download via yt-dlp.
    M1: standard yt-dlp (handles v.redd.it + embeds)
    M2: old.reddit.com URL rewrite (sometimes easier to scrape)
    """
    errors = []

    def _run(dl_url: str) -> tuple:
        prefix = f"/tmp/dl_rd_{int(time.time()*1000)}_{random.randint(1000,9999)}_"
        o = _base_opts(quality)
        o["outtmpl"]      = f"{prefix}%(title).60s.%(ext)s"
        o["http_headers"] = {
            "User-Agent":      _DESKTOP_UAS[0],
            "Accept":          "text/html,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if anim:
            o["progress_hooks"] = [_make_progress_hook(anim)]
        with yt_dlp.YoutubeDL(o) as ydl:
            info  = ydl.extract_info(dl_url, download=True)
            title = (info or {}).get("title", "Reddit Video")
        path = _find(prefix)
        if not path:
            raise ValueError("No output file.")
        return _fix_for_telegram(path), title

    # M1 — direct
    try:
        return _run(url)
    except Exception as e:
        errors.append(f"M1: {str(e)[:80]}")

    # M2 — old.reddit.com rewrite
    try:
        old_url = re.sub(r'https?://(www\.)?reddit\.com', 'https://old.reddit.com', url)
        return _run(old_url)
    except Exception as e:
        errors.append(f"M2: {str(e)[:80]}")

    raise ValueError(
        "🟠 <b>Reddit Download Failed</b>\n\n"
        "• Works with v.redd.it video posts\n"
        "• Image posts are not supported\n"
        "• NSFW posts may need <code>cookies.txt</code>\n\n"
        f"<i>{errors[-1][:180] if errors else 'unknown error'}</i>"
    )

# ─────────────────────────────────────────────────────────────────────────────
# Platform: Twitter / X
# ─────────────────────────────────────────────────────────────────────────────

def _dl_twitter(url: str, quality: str = "720",
                anim: AnimatedStatus = None) -> tuple:
    """
    Twitter/X video download.
    M1: yt-dlp direct (handles most public tweets)
    M2: Use fxtwitter.com redirect — often works when direct fails
    M3: nitter.net mirror as last resort
    """
    errors  = []
    # Normalize — strip tracking params
    clean_url = url.split("?")[0]
    # Make sure we're using x.com domain (yt-dlp prefers it over twitter.com)
    clean_url = re.sub(r'https?://(?:www\.)?twitter\.com', 'https://x.com', clean_url)

    def _run(dl_url: str, extra_hdr: dict = None) -> tuple:
        prefix = f"/tmp/dl_tw_{int(time.time()*1000)}_{random.randint(1000,9999)}_"
        o = _base_opts(quality)
        o["outtmpl"] = f"{prefix}%(title).60s.%(ext)s"
        if extra_hdr:
            o["http_headers"] = {**o["http_headers"], **extra_hdr}
        if anim:
            o["progress_hooks"] = [_make_progress_hook(anim)]
        with yt_dlp.YoutubeDL(o) as ydl:
            info  = ydl.extract_info(dl_url, download=True)
            title = (info or {}).get("title", "Twitter Video")
        path = _find(prefix)
        if not path:
            raise ValueError("No output file.")
        return _fix_for_telegram(path), title

    # M1 — direct x.com
    try:
        return _run(clean_url)
    except Exception as e:
        errors.append(f"M1: {str(e)[:80]}")

    # M2 — fxtwitter.com (often resolves CDN better)
    try:
        fx_url = re.sub(r'https?://(www\.)?(twitter\.com|x\.com)',
                        'https://fxtwitter.com', clean_url)
        return _run(fx_url)
    except Exception as e:
        errors.append(f"M2: {str(e)[:80]}")

    # M3 — vxtwitter.com
    try:
        vx_url = re.sub(r'https?://(www\.)?(twitter\.com|x\.com)',
                        'https://vxtwitter.com', clean_url)
        return _run(vx_url)
    except Exception as e:
        errors.append(f"M3: {str(e)[:80]}")

    raise ValueError(
        "🐦 <b>Twitter/X Download Failed</b>\n\n"
        "• Works with public video tweets\n"
        "• Age-restricted content needs <code>cookies.txt</code>\n"
        "• Private accounts cannot be downloaded\n\n"
        f"<i>{errors[-1][:180] if errors else 'unknown error'}</i>"
    )

# ─────────────────────────────────────────────────────────────────────────────
# Quality keyboards
# ─────────────────────────────────────────────────────────────────────────────

_YT_QUALITY_ROWS = [
    (144,  "📱 144p"),
    (360,  "📺 360p"),
    (480,  "📺 480p"),
    (720,  "🎬 720p HD"),
    (1080, "🖥 1080p FHD"),
    (1440, "✨ 1440p 2K"),
    (2160, "🔥 4K Ultra"),
]


def _yt_quality_kb(available: set,
                   has_hdr: bool = False,
                   max_fps: int = 30) -> dict:
    """Premium keyboard: shortcuts at top, per-quality grid, MP3 at bottom."""
    # Shortcut row always present
    buttons = [
        [{"text": "⚡ Fast Download",  "callback_data": "dl_fast"},
         {"text": "🏆 Best Quality",   "callback_data": "dl_best"}],
    ]
    row = []
    for height, label in _YT_QUALITY_ROWS:
        if available and not any(h >= height * 0.85 for h in available):
            continue
        # Append HDR / 60fps badge directly on high-quality buttons
        btn_label = label
        if height >= 1080 and has_hdr:
            btn_label += " ·HDR"
        if height >= 720 and max_fps >= 60:
            btn_label += " ·60fps"
        row.append({"text": btn_label, "callback_data": f"dl_{height}"})
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([{"text": "🎵 MP3 Audio", "callback_data": "dl_mp3"}])
    return {"inline_keyboard": buttons}


_IG_QUALITY_KB = {"inline_keyboard": [
    [{"text": "📺 360p",     "callback_data": "dl_360"},
     {"text": "🎬 720p HD",  "callback_data": "dl_720"}],
    [{"text": "🖥 1080p",    "callback_data": "dl_1080"},
     {"text": "🎵 MP3 Audio","callback_data": "dl_mp3"}],
]}

_FB_QUALITY_KB = {"inline_keyboard": [
    [{"text": "📺 360p",    "callback_data": "dl_360"},
     {"text": "🎬 720p HD", "callback_data": "dl_720"}],
    [{"text": "🖥 1080p",   "callback_data": "dl_1080"}],
]}

_RD_QUALITY_KB = {"inline_keyboard": [
    [{"text": "📺 360p",    "callback_data": "dl_360"},
     {"text": "🎬 720p HD", "callback_data": "dl_720"}],
    [{"text": "🖥 1080p",   "callback_data": "dl_1080"}],
]}

_TW_QUALITY_KB = {"inline_keyboard": [
    [{"text": "📺 360p",    "callback_data": "dl_360"},
     {"text": "🎬 720p HD", "callback_data": "dl_720"}],
    [{"text": "🖥 1080p",   "callback_data": "dl_1080"}],
]}

# ─────────────────────────────────────────────────────────────────────────────
# Quality pickers (fetch metadata then show keyboard)
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_views(n) -> str:
    if not n:
        return ""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M views"
    if n >= 1_000:
        return f"{n/1_000:.0f}K views"
    return f"{n} views"


def _yt_picker_caption(m: dict) -> str:
    """Build the premium quality-picker caption with size estimates per quality."""
    parts = [f"🎥 <b>{_short(m['title'], 90)}</b>"]

    meta_row = []
    if m.get("channel"):
        meta_row.append(f"👤 {_short(m['channel'], 45)}")
    dur = _fmt_dur(m.get("duration"))
    if dur:
        meta_row.append(f"⏱ {dur}")
    vc = _fmt_views(m.get("view_count"))
    if vc:
        meta_row.append(f"👁 {vc}")
    if meta_row:
        parts.append("  •  ".join(meta_row))

    badges = []
    if m.get("is_live"):
        badges.append("🔴 LIVE")
    if m.get("has_hdr"):
        badges.append("✨ HDR")
    if m.get("max_fps", 30) >= 60:
        badges.append("🎞 60fps")
    if badges:
        parts.append(" ".join(badges))

    # Size table — only show qualities with estimates
    sizes      = m.get("size_estimates", {})
    available  = m.get("available_heights", set())
    size_lines = []
    for height, label in _YT_QUALITY_ROWS:
        if available and not any(h >= height * 0.85 for h in available):
            continue
        est = sizes.get(height, "")
        if height <= 480:
            icon = "📱" if height <= 240 else "📺"
        elif height <= 720:
            icon = "🎬"
        elif height <= 1080:
            icon = "🖥"
        elif height <= 1440:
            icon = "✨"
        else:
            icon = "🔥"
        size_lines.append(f"  {icon} {label:<12}{est}")
    if size_lines:
        parts.append("\n📊 <b>Qualities &amp; sizes:</b>")
        parts.extend(size_lines)
        # MP3 estimate (~5 min ≈ 6 MB)
        dur_s = m.get("duration") or 0
        mp3_mb = f"~{max(1, int(dur_s * 192_000 / 8 / 1_048_576))} MB" if dur_s else ""
        parts.append(f"  🎵 MP3 Audio    {mp3_mb}")

    parts.append("\n<b>⬇ Select quality:</b>")
    return "\n".join(parts)


def _show_yt_picker(chat_id: int, user_id: int, url: str):
    loading = send_msg(chat_id, "🔍 <b>Fetching video info...</b>")
    mid     = (loading.get("result") or {}).get("message_id")
    try:
        m      = _ytdlp_meta(url)
        kb     = _yt_quality_kb(m["available_heights"], m.get("has_hdr", False), m.get("max_fps", 30))
        caption = _yt_picker_caption(m)[:1024]

        sent = None
        if m.get("thumb"):
            try:
                sent = send_photo(chat_id, m["thumb"], caption=caption, markup=kb)
            except Exception:
                pass
        if not sent or not sent.get("ok"):
            if mid:
                edit_text(chat_id, mid, caption, markup=kb)
                sent = {"ok": True}
            else:
                sent = send_msg(chat_id, caption, markup=kb)
        if mid and sent and sent.get("ok"):
            try:
                delete_msg(chat_id, mid)
            except Exception:
                pass
        final_mid = (sent.get("result") or {}).get("message_id") if sent else mid
        with _picks_lock:
            _picks[user_id] = {"url": url, "chat_id": chat_id, "mid": final_mid}
    except Exception as e:
        log.warning("YT picker error: %s", e)
        kb  = _yt_quality_kb(set())
        msg = send_msg(chat_id, "🎥 <b>YouTube Video</b>\n\n<b>Select quality:</b>", markup=kb)
        if mid:
            try:
                delete_msg(chat_id, mid)
            except Exception:
                pass
        with _picks_lock:
            _picks[user_id] = {
                "url": url, "chat_id": chat_id,
                "mid": (msg.get("result") or {}).get("message_id"),
            }


def _show_yt_playlist_picker(chat_id: int, user_id: int, url: str):
    loading = send_msg(chat_id, "📋 <b>Fetching playlist info...</b>")
    mid     = (loading.get("result") or {}).get("message_id")
    try:
        m         = _ytdlp_meta(url)
        pl_title  = m.get("playlist_title") or m.get("title", "YouTube Playlist")
        pl_count  = m.get("playlist_count", 0)
        count_txt = f"<b>{pl_count} videos</b>" if pl_count else "videos"
        chan_txt   = f"👤 {_short(m['channel'], 50)}\n" if m.get("channel") else ""
        kb = {"inline_keyboard": [
            [{"text": "⚡ Fast All (720p)", "callback_data": "dl_fast"},
             {"text": "🏆 Best All",        "callback_data": "dl_best"}],
            [{"text": "🖥 HD All (1080p)",  "callback_data": "dl_1080"},
             {"text": "🎵 MP3 All",         "callback_data": "dl_mp3"}],
        ]}
        caption = (
            f"📋 <b>{_short(pl_title, 80)}</b>\n\n"
            f"{chan_txt}"
            f"🎞 {count_txt}\n\n"
            "<i>All videos will be downloaded and sent one by one.</i>\n\n"
            "<b>Choose quality for all:</b>"
        )
        msg = send_msg(chat_id, caption, markup=kb)
        if mid:
            try:
                delete_msg(chat_id, mid)
            except Exception:
                pass
        final_mid = (msg.get("result") or {}).get("message_id")
        with _picks_lock:
            _picks[user_id] = {"url": url, "chat_id": chat_id,
                               "mid": final_mid, "playlist": True}
    except Exception as e:
        log.warning("Playlist picker error: %s", e)
        kb = {"inline_keyboard": [
            [{"text": "⚡ Fast (720p)",  "callback_data": "dl_fast"},
             {"text": "🏆 Best Quality", "callback_data": "dl_best"}],
            [{"text": "🎵 MP3 All",      "callback_data": "dl_mp3"}],
        ]}
        msg = send_msg(chat_id,
                       "📋 <b>YouTube Playlist</b>\n\n<b>Choose quality:</b>", markup=kb)
        if mid:
            try:
                delete_msg(chat_id, mid)
            except Exception:
                pass
        with _picks_lock:
            _picks[user_id] = {"url": url, "chat_id": chat_id,
                               "mid": (msg.get("result") or {}).get("message_id"),
                               "playlist": True}


def _show_picker(chat_id: int, user_id: int, url: str,
                 icon: str, label: str, kb: dict):
    msg = send_msg(chat_id, f"{icon} <b>{label}</b>\n\n<b>Choose quality:</b>", markup=kb)
    mid = (msg.get("result") or {}).get("message_id")
    with _picks_lock:
        _picks[user_id] = {"url": url, "chat_id": chat_id, "mid": mid}

# ─────────────────────────────────────────────────────────────────────────────
# Download worker
# ─────────────────────────────────────────────────────────────────────────────

_QUAL_LABELS = {
    "mp3":  "🎵 MP3 Audio",
    "best": "🏆 Best Quality",
    "fast": "⚡ Fast (720p)",
    "144":  "📱 144p",
    "240":  "📱 240p",
    "360":  "📺 360p",
    "480":  "📺 480p",
    "720":  "🎬 720p HD",
    "1080": "🖥 1080p FHD",
    "1440": "✨ 1440p 2K",
    "2160": "🔥 4K Ultra",
}

_PLATFORM_ICONS = {
    "youtube": "🎥", "instagram": "📸", "facebook": "📘",
    "snapchat": "👻", "reddit": "🟠", "twitter": "🐦",
}


def _worker(chat_id: int, user_id: int, url: str,
            quality: str, status_id: Optional[int]):
    path: Optional[str] = None
    anim: Optional[AnimatedStatus] = None
    plat = _platform(url)

    try:
        if status_id:
            anim = AnimatedStatus(chat_id, status_id, _LOADING_FRAMES).start()

        if is_yt(url):
            path, title = _dl_youtube(url, quality, anim)
        elif is_ig(url):
            path, title = _dl_instagram(url, quality, anim)
        elif is_fb(url):
            path, title = _dl_facebook(url, quality, anim)
        elif is_sc(url):
            path, title = _dl_snapchat(url)
        elif is_reddit(url):
            path, title = _dl_reddit(url, quality, anim)
        elif is_twitter(url):
            path, title = _dl_twitter(url, quality, anim)
        else:
            raise ValueError("Unsupported link.")

        _record_download(plat)

        size      = _fmt_size(path)
        is_audio  = quality == "mp3"
        ql        = _QUAL_LABELS.get(quality, f"{quality}p")
        icon      = _PLATFORM_ICONS.get(plat, "📥")

        if anim:
            anim.stop()
            anim = None
        if status_id:
            edit_text(chat_id, status_id, "📤 <b>Uploading to Telegram...</b>")

        if is_audio:
            cap = (
                f"✅ <b>Download Complete</b>\n\n"
                f"🎵 <b>{_short(title, 100)}</b>\n\n"
                f"📦 {size}  •  {ql}\n"
                f"{icon} MeraDownload4K"
            )
            send_audio(chat_id, path, caption=cap)
        else:
            cap = (
                f"✅ <b>Download Complete</b>\n\n"
                f"{icon} <b>{_short(title, 100)}</b>\n\n"
                f"📦 {size}  •  {ql}\n"
                f"⚡ MeraDownload4K"
            )
            send_video(chat_id, path, caption=cap)

        if status_id:
            try:
                delete_msg(chat_id, status_id)
            except Exception:
                pass

    except Exception as e:
        if anim:
            anim.stop()
        err_text = str(e)
        # Strip raw yt-dlp exception prefixes
        for prefix in ["ERROR: ", "yt_dlp.utils.DownloadError: ", "DownloadError: "]:
            if err_text.startswith(prefix):
                err_text = err_text[len(prefix):]
        # If it already looks like a premium HTML message, send as-is
        if "<b>" not in err_text:
            err_text = (
                "⚠️ <b>Download Failed</b>\n\n"
                f"<i>{_short(err_text, 300)}</i>"
            )
        if status_id:
            edit_text(chat_id, status_id, err_text)
        else:
            send_msg(chat_id, err_text)
    finally:
        _rm(path)
        _unlock_user(user_id)


def _submit(chat_id: int, user_id: int, url: str,
            quality: str = "720", picker_mid: int = None):
    if not _lock_user(user_id):
        send_msg(chat_id, "⏳ <b>Please wait</b> — your previous download is still in progress.")
        return
    if picker_mid:
        try:
            delete_msg(chat_id, picker_mid)
        except Exception:
            pass
    icon    = _PLATFORM_ICONS.get(_platform(url), "📥")
    ql      = _QUAL_LABELS.get(quality, f"{quality}p")
    status  = send_msg(chat_id, f"{icon} <b>Starting {ql} download...</b>")
    stat_id = (status.get("result") or {}).get("message_id")
    executor.submit(_worker, chat_id, user_id, url, quality, stat_id)


def _submit_playlist(chat_id: int, user_id: int, url: str,
                     quality: str = "720", picker_mid: int = None):
    if not _lock_user(user_id):
        send_msg(chat_id, "⏳ <b>Please wait</b> — your previous download is still in progress.")
        return
    if picker_mid:
        try:
            delete_msg(chat_id, picker_mid)
        except Exception:
            pass
    ql     = _QUAL_LABELS.get(quality, f"{quality}p")
    status = send_msg(chat_id, f"📋 <b>Starting playlist — {ql}...</b>")
    stat_id = (status.get("result") or {}).get("message_id")
    executor.submit(_playlist_worker, chat_id, user_id, url, quality, stat_id)

# ─────────────────────────────────────────────────────────────────────────────
# Broadcast system (admin only)
# ─────────────────────────────────────────────────────────────────────────────

def _do_broadcast(admin_id: int, src_chat: int, src_mid: int):
    uids = _get_all_user_ids()
    sent = failed = blocked = 0
    prog = send_msg(admin_id,
                    f"📡 <b>Broadcasting...</b>\n\n"
                    f"👥 Recipients: <b>{len(uids)}</b>")
    prog_mid = (prog.get("result") or {}).get("message_id")

    for i, uid in enumerate(uids):
        try:
            copy_message(uid, src_chat, src_mid)
            sent += 1
        except Exception as e:
            if "blocked" in str(e).lower() or "kicked" in str(e).lower():
                blocked += 1
            else:
                failed += 1
        time.sleep(BROADCAST_DELAY)
        if prog_mid and (i + 1) % 20 == 0:
            try:
                edit_text(admin_id, prog_mid,
                          f"📡 <b>Broadcasting...</b>\n\n"
                          f"✅ Sent: {sent}  ❌ Failed: {failed}  🚫 Blocked: {blocked}\n"
                          f"📊 Progress: {i+1}/{len(uids)}")
            except Exception:
                pass

    summary = (
        f"📡 <b>Broadcast Complete</b>\n\n"
        f"✅ Sent:    <b>{sent}</b>\n"
        f"🚫 Blocked: <b>{blocked}</b>\n"
        f"❌ Failed:  <b>{failed}</b>\n"
        f"👥 Total:   <b>{len(uids)}</b>"
    )
    if prog_mid:
        edit_text(admin_id, prog_mid, summary)
    else:
        send_msg(admin_id, summary)

# ─────────────────────────────────────────────────────────────────────────────
# /start welcome message
# ─────────────────────────────────────────────────────────────────────────────

def _send_start(chat_id: int, first_name: str):
    kb = {"inline_keyboard": [
        [{"text": "🎥 YouTube",   "callback_data": "info_yt"},
         {"text": "📸 Instagram", "callback_data": "info_ig"}],
        [{"text": "📘 Facebook",  "callback_data": "info_fb"},
         {"text": "👻 Snapchat",  "callback_data": "info_sc"}],
        [{"text": "🟠 Reddit",    "callback_data": "info_rd"},
         {"text": "🐦 Twitter/X", "callback_data": "info_tw"}],
        [{"text": "📊 Stats",     "callback_data": "info_stats"},
         {"text": "ℹ️ Help",      "callback_data": "info_help"}],
    ]}
    text = (
        f"👋 <b>Welcome, {first_name or 'there'}!</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 <b>MeraDownload4K</b>\n"
        "<i>Premium Video Downloader</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📥 <b>Supported Platforms</b>\n"
        "  🎥 YouTube &amp; Shorts — up to 4K\n"
        "  📸 Instagram Reels, Posts &amp; Stories\n"
        "  📘 Facebook Videos &amp; Reels\n"
        "  👻 Snapchat Spotlight\n"
        "  🟠 Reddit Videos\n"
        "  🐦 Twitter/X Videos\n\n"
        "⚡ <b>How to use</b>\n"
        "Just paste any supported link!\n\n"
        "⚙️ <b>Commands</b>\n"
        "  /audio — Extract MP3\n"
        "  /stats — Bot statistics\n"
        "  /users — Total users\n"
        "  /active — Active today\n"
        "  /downloads — Download stats\n"
        "  /help — Usage guide"
    )
    send_msg(chat_id, text, markup=kb)

# ─────────────────────────────────────────────────────────────────────────────
# Info texts (inline button responses)
# ─────────────────────────────────────────────────────────────────────────────

_INFO_TEXTS = {
    "info_yt": (
        "🎥 <b>YouTube &amp; Shorts</b>\n\n"
        "Qualities: 144p · 240p · 360p · 480p · 720p · 1080p · 1440p · 4K\n"
        "• Only available qualities shown per video\n"
        "• MP3 audio extraction supported\n"
        "• Shorts normalized automatically\n"
        "• Multi-client extraction — bypasses bot-detection"
    ),
    "info_ig": (
        "📸 <b>Instagram</b>\n\n"
        "• Reels ✅  Posts ✅  Stories ✅\n"
        "• Public content: no login needed\n"
        "• Private content: requires <code>cookies.txt</code>\n"
        "• 7 extraction methods with auto-fallback"
    ),
    "info_fb": (
        "📘 <b>Facebook</b>\n\n"
        "• Public videos ✅\n"
        "• Reels ✅\n"
        "• Private videos need <code>cookies.txt</code>\n"
        "• 3 extraction methods"
    ),
    "info_sc": (
        "👻 <b>Snapchat</b>\n\n"
        "• Spotlight videos ✅\n"
        "• Public stories ✅\n"
        "• Direct CDN extraction — bypasses extension issue"
    ),
    "info_rd": (
        "🟠 <b>Reddit</b>\n\n"
        "• v.redd.it hosted videos ✅\n"
        "• Reddit Reels ✅\n"
        "• NSFW posts may need <code>cookies.txt</code>\n"
        "• Image-only posts not supported"
    ),
    "info_tw": (
        "🐦 <b>Twitter / X</b>\n\n"
        "• Public tweet videos ✅\n"
        "• Age-restricted content needs <code>cookies.txt</code>\n"
        "• Private account tweets cannot be downloaded\n"
        "• 3 extraction methods (direct + fx/vx mirrors)"
    ),
    "info_help": (
        "ℹ️ <b>How to Use</b>\n\n"
        "1. Copy any supported video link\n"
        "2. Paste it here\n"
        "3. Choose quality\n"
        "4. Receive your file!\n\n"
        "⚙️ <b>Commands</b>\n"
        "/audio &lt;link&gt; — Extract MP3\n"
        "/stats — Bot statistics\n"
        "/users — Total user count\n"
        "/active — Active users today\n"
        "/downloads — Download stats\n"
        "/getid — Your Telegram ID"
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# Message handler
# ─────────────────────────────────────────────────────────────────────────────

def _on_message(msg: dict):
    chat_id    = msg.get("chat", {}).get("id")
    user       = msg.get("from", {})
    user_id    = user.get("id")
    first_name = user.get("first_name", "")
    text       = (msg.get("text") or "").strip()

    if not chat_id or not user_id:
        return

    _record_user(user_id)

    # Broadcast reply mode
    if user_id == ADMIN_ID and user_id in _pending_bc:
        del _pending_bc[user_id]
        mid = msg.get("message_id")
        if mid:
            threading.Thread(
                target=_do_broadcast, args=(ADMIN_ID, chat_id, mid), daemon=True
            ).start()
            return

    if not text:
        return

    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""

    # /start /help
    if cmd in ("/start", "/help"):
        _send_start(chat_id, first_name)
        return

    # /stats
    if cmd == "/stats":
        s  = _get_stats()
        ps = s["platform_stats"]
        send_msg(chat_id,
            "📊 <b>Bot Statistics</b>\n\n"
            f"👥 Total Users:     <b>{s['total_users']}</b>\n"
            f"📅 Active Today:    <b>{s['active_today']}</b>\n"
            f"⬇️ Downloads Today: <b>{s['downloads_today']}</b>\n"
            f"📦 Total Downloads: <b>{s['total_downloads']}</b>\n"
            f"⚡ Active Now:      <b>{s['active_downloads']}</b>\n\n"
            "📈 <b>By Platform</b>\n"
            f"  🎥 YouTube:   {ps.get('youtube', 0)}\n"
            f"  📸 Instagram: {ps.get('instagram', 0)}\n"
            f"  📘 Facebook:  {ps.get('facebook', 0)}\n"
            f"  👻 Snapchat:  {ps.get('snapchat', 0)}\n"
            f"  🟠 Reddit:    {ps.get('reddit', 0)}\n"
            f"  🐦 Twitter/X: {ps.get('twitter', 0)}"
        )
        return

    # /users
    if cmd == "/users":
        s = _get_stats()
        send_msg(chat_id,
            "👥 <b>Users</b>\n\n"
            f"Total registered: <b>{s['total_users']}</b>\n"
            f"Active today:     <b>{s['active_today']}</b>"
        )
        return

    # /active
    if cmd == "/active":
        s     = _get_stats()
        today = str(date.today())
        with _stats_lock:
            active_list = _stats["daily_users"].get(today, [])
        send_msg(chat_id,
            "📅 <b>Active Today</b>\n\n"
            f"Users active:  <b>{len(active_list)}</b>\n"
            f"Downloads:     <b>{s['downloads_today']}</b>\n"
            f"Active now:    <b>{s['active_downloads']}</b>"
        )
        return

    # /downloads
    if cmd == "/downloads":
        s  = _get_stats()
        ps = s["platform_stats"]
        send_msg(chat_id,
            "📦 <b>Download Stats</b>\n\n"
            f"Today:   <b>{s['downloads_today']}</b>\n"
            f"Total:   <b>{s['total_downloads']}</b>\n"
            f"Active:  <b>{s['active_downloads']}</b>\n\n"
            "📈 <b>By Platform</b>\n"
            f"  🎥 YouTube:   <b>{ps.get('youtube', 0)}</b>\n"
            f"  📸 Instagram: <b>{ps.get('instagram', 0)}</b>\n"
            f"  📘 Facebook:  <b>{ps.get('facebook', 0)}</b>\n"
            f"  👻 Snapchat:  <b>{ps.get('snapchat', 0)}</b>\n"
            f"  🟠 Reddit:    <b>{ps.get('reddit', 0)}</b>\n"
            f"  🐦 Twitter/X: <b>{ps.get('twitter', 0)}</b>"
        )
        return

    # /getid
    if cmd == "/getid":
        send_msg(chat_id,
            f"🪪 <b>Your Telegram ID</b>\n\n"
            f"<code>{user_id}</code>\n\n"
            "Set this as <code>ADMIN_ID</code> in Railway Variables."
        )
        return

    # /audio <url>
    if cmd == "/audio":
        parts = text.split(None, 1)
        url   = parts[1].strip() if len(parts) > 1 else ""
        if not is_supported(url):
            send_msg(chat_id, "Usage: <code>/audio &lt;link&gt;</code>")
            return
        _submit(chat_id, user_id, url, quality="mp3")
        return

    # /broadcast (admin only)
    if cmd == "/broadcast" and user_id == ADMIN_ID:
        parts   = text.split(None, 1)
        bc_text = parts[1].strip() if len(parts) > 1 else ""
        reply   = msg.get("reply_to_message")
        if reply:
            threading.Thread(
                target=_do_broadcast, args=(ADMIN_ID, chat_id, reply["message_id"]),
                daemon=True,
            ).start()
        elif bc_text:
            r   = send_msg(chat_id, bc_text)
            mid = (r.get("result") or {}).get("message_id")
            if mid:
                threading.Thread(
                    target=_do_broadcast, args=(ADMIN_ID, chat_id, mid), daemon=True
                ).start()
        else:
            _pending_bc[user_id] = True
            send_msg(chat_id,
                "📡 <b>Broadcast Mode</b>\n\n"
                "Send the message/photo/video to broadcast.\n"
                "Or reply to any message with /broadcast."
            )
        return

    # /admin (admin only)
    if cmd == "/admin" and user_id == ADMIN_ID:
        s  = _get_stats()
        ps = s["platform_stats"]
        send_msg(chat_id,
            "🛡 <b>Admin Dashboard</b>\n\n"
            f"👥 Total Users:     <b>{s['total_users']}</b>\n"
            f"📅 Active Today:    <b>{s['active_today']}</b>\n"
            f"⬇️ Downloads Today: <b>{s['downloads_today']}</b>\n"
            f"📦 Total Downloads: <b>{s['total_downloads']}</b>\n"
            f"⚡ Active Now:      <b>{s['active_downloads']}</b>\n"
            f"📋 Queue:           <b>{s['queue_size']}</b>\n\n"
            "📈 <b>Platform Breakdown</b>\n"
            f"  🎥 YouTube:   {ps.get('youtube', 0)}\n"
            f"  📸 Instagram: {ps.get('instagram', 0)}\n"
            f"  📘 Facebook:  {ps.get('facebook', 0)}\n"
            f"  👻 Snapchat:  {ps.get('snapchat', 0)}\n"
            f"  🟠 Reddit:    {ps.get('reddit', 0)}\n"
            f"  🐦 Twitter/X: {ps.get('twitter', 0)}\n\n"
            "⚙️ <b>System</b>\n"
            f"  🤖 Bot: @{BOT_NAME}\n"
            f"  🍪 Cookies: {'✅ loaded' if HAS_COOKIES else '❌ not set'}\n"
            f"  🎞 ffmpeg: {'✅ found' if FFMPEG else '❌ missing'}\n\n"
            "📡 <b>Commands</b>\n"
            "  /broadcast — Message all users\n"
            "  /users  /active  /downloads"
        )
        return

    # Plain URL
    if is_supported(text):
        url = text
        if is_yt(url):
            if _is_yt_playlist(url):
                threading.Thread(
                    target=_show_yt_playlist_picker, args=(chat_id, user_id, url), daemon=True
                ).start()
            else:
                threading.Thread(
                    target=_show_yt_picker, args=(chat_id, user_id, url), daemon=True
                ).start()
        elif is_ig(url):
            threading.Thread(
                target=_show_picker,
                args=(chat_id, user_id, url, "📸", "Instagram Video", _IG_QUALITY_KB),
                daemon=True,
            ).start()
        elif is_fb(url):
            threading.Thread(
                target=_show_picker,
                args=(chat_id, user_id, url, "📘", "Facebook Video", _FB_QUALITY_KB),
                daemon=True,
            ).start()
        elif is_reddit(url):
            threading.Thread(
                target=_show_picker,
                args=(chat_id, user_id, url, "🟠", "Reddit Video", _RD_QUALITY_KB),
                daemon=True,
            ).start()
        elif is_twitter(url):
            threading.Thread(
                target=_show_picker,
                args=(chat_id, user_id, url, "🐦", "Twitter/X Video", _TW_QUALITY_KB),
                daemon=True,
            ).start()
        else:
            # Snapchat — no quality picker, download directly
            _submit(chat_id, user_id, url, quality="720")
        return

    send_msg(chat_id,
             "📎 <b>Send me a video link!</b>\n\n"
             "Supported: YouTube · Instagram · Facebook · Snapchat · Reddit · Twitter/X")

# ─────────────────────────────────────────────────────────────────────────────
# Callback handler
# ─────────────────────────────────────────────────────────────────────────────

def _on_callback(update: dict):
    cb      = update.get("callback_query", {})
    cb_id   = cb.get("id", "")
    data    = cb.get("data", "")
    user    = cb.get("from", {})
    user_id = user.get("id")
    chat_id = (cb.get("message") or {}).get("chat", {}).get("id")
    mid     = (cb.get("message") or {}).get("message_id")

    # Info buttons
    if data in _INFO_TEXTS:
        answer_cb(cb_id)
        send_msg(chat_id, _INFO_TEXTS[data])
        return

    if data == "info_stats":
        answer_cb(cb_id)
        s  = _get_stats()
        ps = s["platform_stats"]
        send_msg(chat_id,
            "📊 <b>Bot Statistics</b>\n\n"
            f"👥 Total Users:  <b>{s['total_users']}</b>\n"
            f"📅 Active Today: <b>{s['active_today']}</b>\n"
            f"📦 Total:        <b>{s['total_downloads']}</b>\n"
            f"⬇️ Today:        <b>{s['downloads_today']}</b>\n\n"
            "📈 <b>By Platform</b>\n"
            f"  🎥 YouTube:   {ps.get('youtube', 0)}\n"
            f"  📸 Instagram: {ps.get('instagram', 0)}\n"
            f"  📘 Facebook:  {ps.get('facebook', 0)}\n"
            f"  👻 Snapchat:  {ps.get('snapchat', 0)}\n"
            f"  🟠 Reddit:    {ps.get('reddit', 0)}\n"
            f"  🐦 Twitter/X: {ps.get('twitter', 0)}"
        )
        return

    if not data.startswith("dl_"):
        answer_cb(cb_id)
        return

    quality   = data[3:]
    ql        = _QUAL_LABELS.get(quality, f"{quality}p")
    answer_cb(cb_id, f"⏳ Starting {ql}…")

    with _picks_lock:
        pick = _picks.get(user_id)

    if not pick:
        if chat_id and mid:
            edit_text(chat_id, mid, "⚠️ Session expired. Please send the link again.")
        return

    if pick.get("playlist"):
        _submit_playlist(chat_id, user_id, pick["url"], quality=quality, picker_mid=mid)
    else:
        _submit(chat_id, user_id, pick["url"], quality=quality, picker_mid=mid)

# ─────────────────────────────────────────────────────────────────────────────
# Flask health + stats endpoints
# ─────────────────────────────────────────────────────────────────────────────

_flask = Flask(__name__)


@_flask.route("/")
def _root():
    return "✅ MeraDownload4K v3 is running", 200


@_flask.route("/health")
def _health():
    return jsonify({
        "status":   "ok",
        "version":  "3.0",
        "bot":      BOT_NAME,
        "cookies":  HAS_COOKIES,
        "ffmpeg":   bool(FFMPEG),
        "platforms": ["youtube", "instagram", "facebook", "snapchat", "reddit", "twitter"],
    }), 200


@_flask.route("/stats")
def _stats_ep():
    return jsonify(_get_stats()), 200


def _run_flask():
    _flask.run(host="0.0.0.0", port=PORT, use_reloader=False)

# ─────────────────────────────────────────────────────────────────────────────
# Background cleanup
# ─────────────────────────────────────────────────────────────────────────────

def _cleanup():
    while True:
        time.sleep(CLEANUP_INTERVAL)
        now = time.time()
        removed = 0
        for pattern in ("/tmp/dl_*", "/tmp/ig_*", "/tmp/sc_*"):
            for f in glob.glob(pattern):
                try:
                    if now - os.path.getmtime(f) > MAX_FILE_AGE:
                        if os.path.isdir(f):
                            shutil.rmtree(f, ignore_errors=True)
                        else:
                            os.remove(f)
                        removed += 1
                except OSError:
                    pass
        if removed:
            log.info("🧹 Cleaned %d temp file(s)", removed)

# ─────────────────────────────────────────────────────────────────────────────
# Polling loop — exponential backoff
# ─────────────────────────────────────────────────────────────────────────────

def _poll():
    offset  = None
    backoff = 2
    while True:
        try:
            updates = get_updates(offset=offset)
            backoff = 2
            for u in updates.get("result", []):
                offset = u["update_id"] + 1
                try:
                    if "message" in u:
                        _on_message(u["message"])
                    elif "callback_query" in u:
                        _on_callback(u)
                except Exception as e:
                    log.error("Handler error: %s", e)
        except Exception as e:
            log.warning("Polling error: %s — retry in %ds", e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)

# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    global BOT_NAME
    _load_stats()

    me       = get_me()
    BOT_NAME = me.get("username", "UnknownBot")

    log.info("━" * 60)
    log.info("🤖  @%s — MeraDownload4K v3.0", BOT_NAME)
    log.info("🌍  Platforms: YouTube · Instagram · Facebook · Snapchat · Reddit · Twitter/X")
    log.info("🎞  ffmpeg   : %s", FFMPEG or "NOT FOUND ⚠️")
    log.info("🍪  cookies  : %s", "✅ loaded" if HAS_COOKIES else "not present (public only)")
    log.info("🛡  admin    : %s", ADMIN_ID or "not set")
    log.info("🌐  health   : 0.0.0.0:%d /health", PORT)
    log.info("📊  stats    : %d users, %d total downloads",
             len(_stats["total_users"]), _stats["total_downloads"])
    log.info("━" * 60)

    if ADMIN_ID:
        try:
            s = _get_stats()
            send_msg(ADMIN_ID,
                f"✅ <b>@{BOT_NAME} v3.0 is online</b>\n\n"
                f"🌍 Platforms: YouTube · IG · FB · SC · Reddit · Twitter\n"
                f"🎞 ffmpeg: {'✅' if FFMPEG else '❌ NOT FOUND'}\n"
                f"🍪 cookies: {'✅ loaded' if HAS_COOKIES else '❌ not present'}\n"
                f"👥 users: {s['total_users']}\n"
                f"📦 total downloads: {s['total_downloads']}"
            )
        except Exception:
            pass

    threading.Thread(target=_run_flask, daemon=True).start()
    threading.Thread(target=_cleanup,   daemon=True).start()
    _poll()


if __name__ == "__main__":
    main()
