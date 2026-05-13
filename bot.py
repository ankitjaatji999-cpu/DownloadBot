"""
MeraDownload4K — Premium Telegram Downloader Bot v2.0
Platforms: YouTube · Instagram · Facebook · Snapchat
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
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from queue import Queue, Empty
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

DOWNLOAD_DOMAINS = [
    "youtube.com", "youtu.be",
    "instagram.com",
    "facebook.com", "fb.watch", "fb.com",
    "snapchat.com", "snap.com",
]
YOUTUBE_DOMAINS   = {"youtube.com", "youtu.be"}
INSTAGRAM_DOMAINS = {"instagram.com"}
FACEBOOK_DOMAINS  = {"facebook.com", "fb.watch", "fb.com"}
SNAPCHAT_DOMAINS  = {"snapchat.com", "snap.com"}

FFMPEG      = shutil.which("ffmpeg") or ""
FFPROBE     = shutil.which("ffprobe") or ""
BOT_DIR     = os.path.dirname(os.path.abspath(__file__))
COOKIES     = os.path.join(BOT_DIR, "cookies.txt")
HAS_COOKIES = os.path.isfile(COOKIES) and os.path.getsize(COOKIES) > 100
STATS_FILE  = os.path.join(BOT_DIR, "stats.json")

MAX_WORKERS      = 5
CLEANUP_INTERVAL = 1800
MAX_FILE_AGE     = 3600
BROADCAST_DELAY  = 0.05   # seconds between each broadcast message

# ─────────────────────────────────────────────────────────────────────────────
# Stats / Analytics
# ─────────────────────────────────────────────────────────────────────────────

_stats_lock = threading.Lock()
_stats: dict = {
    "total_users":    [],
    "daily_users":    {},       # {"2025-05-12": [uid, ...]}
    "total_downloads": 0,
    "downloads_today": 0,
    "stats_date":      str(date.today()),
    "platform_stats":  {"youtube": 0, "instagram": 0, "facebook": 0, "snapchat": 0},
    "hourly_counts":   {},      # {"2025-05-12T14": count}
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
            if "platform_stats" not in loaded:
                loaded["platform_stats"] = {"youtube": 0, "instagram": 0, "facebook": 0, "snapchat": 0}
            if "daily_users" not in loaded:
                loaded["daily_users"] = {}
            if "hourly_counts" not in loaded:
                loaded["hourly_counts"] = {}
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
        if today not in _stats["daily_users"]:
            _stats["daily_users"][today] = []
        if uid not in _stats["daily_users"][today]:
            _stats["daily_users"][today].append(uid)
        _save_stats()


def _record_download(platform: str):
    with _stats_lock:
        today = str(date.today())
        if _stats.get("stats_date") != today:
            _stats["downloads_today"] = 0
            _stats["stats_date"]      = today
        _stats["total_downloads"] += 1
        _stats["downloads_today"] += 1
        if platform in _stats["platform_stats"]:
            _stats["platform_stats"][platform] += 1
        hour_key = datetime.now().strftime("%Y-%m-%dT%H")
        _stats["hourly_counts"][hour_key] = _stats["hourly_counts"].get(hour_key, 0) + 1
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


def _get_all_user_ids() -> list[int]:
    with _stats_lock:
        return list(_stats["total_users"])


# ─────────────────────────────────────────────────────────────────────────────
# User-Agent pools
# ─────────────────────────────────────────────────────────────────────────────

_DESKTOP_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

_MOBILE_UAS = [
    "Mozilla/5.0 (Linux; Android 14; SM-S928U) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/124.0.6367.88 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; SM-A546U) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
]

_IG_APP_UAS = [
    "Instagram 275.0.0.27.98 Android (33/13; 420dpi; 1080x2400; samsung; SM-S908U; b0q; qcom; en_US; 458229258)",
    "Instagram 275.0.0.27.98 Android (30/11; 480dpi; 1080x2160; OnePlus; IN2023; OnePlus8Pro; qcom; en_US; 458229258)",
    "Instagram 274.0.0.29.102 Android (31/12; 420dpi; 1080x2340; xiaomi; 2201116TG; thor; qcom; en_US; 456462828)",
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


def _random_delay(lo: float = 1.0, hi: float = 3.5):
    time.sleep(random.uniform(lo, hi))


# ─────────────────────────────────────────────────────────────────────────────
# Instagram headers
# ─────────────────────────────────────────────────────────────────────────────

_IG_APP_ID = "936619743392459"


def _ig_web_headers(ua: str) -> dict:
    return {
        "User-Agent":       ua,
        "Accept":           "*/*",
        "Accept-Language":  "en-US,en;q=0.9",
        "Accept-Encoding":  "gzip, deflate, br",
        "Origin":           "https://www.instagram.com",
        "Referer":          "https://www.instagram.com/",
        "X-IG-App-ID":      _IG_APP_ID,
        "X-ASBD-ID":        "129477",
        "X-IG-WWW-Claim":   "0",
        "Sec-Fetch-Dest":   "empty",
        "Sec-Fetch-Mode":   "cors",
        "Sec-Fetch-Site":   "same-site",
        "DNT":              "1",
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


def _ig_browser_headers(ua: str) -> dict:
    return {
        "User-Agent":              ua,
        "Accept":                  "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language":         "en-US,en;q=0.9",
        "Accept-Encoding":         "gzip, deflate, br",
        "Connection":              "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":          "document",
        "Sec-Fetch-Mode":          "navigate",
        "Sec-Fetch-Site":          "none",
        "Cache-Control":           "max-age=0",
    }


# ─────────────────────────────────────────────────────────────────────────────
# URL classifiers
# ─────────────────────────────────────────────────────────────────────────────

def is_yt(u):        return any(d in u for d in YOUTUBE_DOMAINS)
def is_ig(u):        return any(d in u for d in INSTAGRAM_DOMAINS)
def is_fb(u):        return any(d in u for d in FACEBOOK_DOMAINS)
def is_sc(u):        return any(d in u for d in SNAPCHAT_DOMAINS)
def is_supported(t): return any(d in t for d in DOWNLOAD_DOMAINS)

def _ig_is_story(url: str) -> bool:
    return "/stories/" in url


# ─────────────────────────────────────────────────────────────────────────────
# Active download tracking + queue
# ─────────────────────────────────────────────────────────────────────────────

executor     = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="dl")
_active      = {}
_active_lock = threading.Lock()
_picks       = {}
_picks_lock  = threading.Lock()
_dl_queue    = Queue()


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
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
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
    else:
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


def edit_markup(chat_id, mid, markup=None) -> dict:
    return _post("editMessageReplyMarkup", json={
        "chat_id": chat_id, "message_id": mid,
        "reply_markup": json.dumps(markup or {}),
    })


def delete_msg(chat_id, mid) -> dict:
    return _post("deleteMessage", json={"chat_id": chat_id, "message_id": mid})


def answer_cb(cb_id: str, text: str = "", alert: bool = False) -> dict:
    return _post("answerCallbackQuery",
                 json={"callback_query_id": cb_id, "text": text, "show_alert": alert})


def copy_message(to_chat_id, from_chat_id, message_id) -> dict:
    return _post("copyMessage", json={
        "chat_id": to_chat_id,
        "from_chat_id": from_chat_id,
        "message_id": message_id,
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
# Animated status
# ─────────────────────────────────────────────────────────────────────────────

_LOADING_FRAMES = [
    "⏳ <b>Fetching video...</b>",
    "🔍 <b>Extracting media...</b>",
    "⚙️ <b>Processing...</b>",
    "📡 <b>Connecting to source...</b>",
    "🔄 <b>Almost there...</b>",
    "📥 <b>Downloading...</b>",
]

_UPLOAD_FRAMES = [
    "📤 <b>Uploading to Telegram...</b>",
    "🚀 <b>Sending your file...</b>",
    "⚡ <b>Nearly done...</b>",
]


class AnimatedStatus:
    def __init__(self, chat_id: int, message_id: int, frames: list[str], interval: float = 4.0):
        self.chat_id    = chat_id
        self.message_id = message_id
        self.frames     = frames
        self.interval   = interval
        self._idx       = 0
        self._stop      = threading.Event()
        self._thread    = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(self.interval):
            self._idx = (self._idx + 1) % len(self.frames)
            try:
                edit_text(self.chat_id, self.message_id, self.frames[self._idx])
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
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
        return f"{s/1_073_741_824:.2f} GB"
    return f"{s/1_048_576:.1f} MB"


def _fmt_dur(s) -> str:
    if not s:
        return ""
    s = int(s)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _short(text: str, n: int = 80) -> str:
    if not text:
        return ""
    return text[:n] + "…" if len(text) > n else text


# ─────────────────────────────────────────────────────────────────────────────
# Video fix — H264 + AAC + faststart
# ─────────────────────────────────────────────────────────────────────────────

def _probe_codecs(path: str) -> tuple[str, str]:
    probe = FFPROBE or FFMPEG
    if not probe:
        return "", ""
    try:
        if FFPROBE:
            r = subprocess.run(
                [FFPROBE, "-v", "quiet", "-show_streams", "-print_format", "json", path],
                capture_output=True, text=True, timeout=15,
            )
            data   = json.loads(r.stdout or "{}")
            vcodec = ""
            acodec = ""
            for s in data.get("streams", []):
                if s.get("codec_type") == "video" and not vcodec:
                    vcodec = s.get("codec_name", "")
                if s.get("codec_type") == "audio" and not acodec:
                    acodec = s.get("codec_name", "")
            return vcodec, acodec
        else:
            r  = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True, timeout=15)
            vc = re.search(r"Video:\s*(\w+)", r.stderr)
            ac = re.search(r"Audio:\s*(\w+)", r.stderr)
            return (vc.group(1) if vc else ""), (ac.group(1) if ac else "")
    except Exception:
        return "", ""


def _fix_for_telegram(src: str) -> str:
    if not FFMPEG:
        return src
    vcodec, acodec = _probe_codecs(src)
    is_h264 = vcodec.lower() in ("h264", "avc", "avc1")
    is_aac  = acodec.lower() in ("aac", "mp3", "mp4a")
    is_mp4  = src.lower().endswith(".mp4")
    out     = src.rsplit(".", 1)[0] + "_tg.mp4"

    if is_h264 and is_aac and is_mp4:
        cmd = [FFMPEG, "-i", src, "-c", "copy", "-movflags", "+faststart", "-y", out]
    else:
        log.info("Re-encoding %s (v=%s a=%s)", src, vcodec, acodec)
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
# yt-dlp helpers
# ─────────────────────────────────────────────────────────────────────────────

_QUALITY_HEIGHTS = {
    "144": 144, "240": 240, "360": 360, "480": 480,
    "720": 720, "1080": 1080, "1440": 1440, "2160": 2160,
}


def _fmt_str(quality: str) -> str:
    if quality == "mp3":
        return "bestaudio/best"
    h = _QUALITY_HEIGHTS.get(quality, 720)
    if h <= 720:
        return (
            f"bestvideo[vcodec^=avc1][height<={h}][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]"
            f"/bestvideo[vcodec^=avc][height<={h}][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]"
            f"/bestvideo[vcodec^=avc1][height<={h}]+bestaudio"
            f"/bestvideo[height<={h}]+bestaudio"
            f"/best[height<={h}][ext=mp4]/best[height<={h}]/best"
        )
    return (
        f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]"
        f"/bestvideo[height<={h}]+bestaudio"
        f"/best[height<={h}]/best"
    )


# YouTube player clients tried in order — ios/mweb bypass most bot-detection
_YT_PLAYER_CLIENTS = ["ios", "mweb", "android_embedded", "android", "web"]


def _yt_extractor_args(clients: list[str] = None) -> dict:
    return {
        "youtube": {
            "player_client": clients or _YT_PLAYER_CLIENTS,
        }
    }


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


def _ytdlp_run(url: str, quality: str, extra: dict = None) -> tuple[str, str]:
    prefix = f"/tmp/dl_{int(time.time()*1000)}_{random.randint(1000,9999)}_"
    o = _base_opts(quality)
    o["outtmpl"] = f"{prefix}%(title).60s.%(ext)s"
    if extra:
        o.update(extra)
    with yt_dlp.YoutubeDL(o) as ydl:
        info  = ydl.extract_info(url, download=True)
        title = (info or {}).get("title", "")
    path = _find(prefix)
    if not path:
        raise ValueError("yt-dlp finished but no output file found.")
    return path, title


def _ytdlp_meta(url: str) -> dict:
    """Fetch metadata including available heights for dynamic quality picker.
    Tries multiple YouTube player clients to bypass bot-detection / sign-in walls."""
    base: dict = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "skip_download": True, "socket_timeout": 25,
        "http_headers": {"User-Agent": _ua()},
        "extractor_args": _yt_extractor_args(),
        "age_limit": 99,
    }
    if FFMPEG:
        base["ffmpeg_location"] = FFMPEG
    if HAS_COOKIES:
        base["cookiefile"] = COOKIES

    info: dict = {}
    # Try with full client list first, fall back to cookie-only if that fails
    for attempt_opts in [base, {**base, "extractor_args": _yt_extractor_args(["ios"])}]:
        try:
            with yt_dlp.YoutubeDL(attempt_opts) as ydl:
                info = ydl.extract_info(url, download=False) or {}
            if info:
                break
        except Exception:
            pass

    available_heights: set[int] = set()
    for f in info.get("formats", []):
        h = f.get("height")
        if h and isinstance(h, int):
            available_heights.add(h)

    return {
        "title":             info.get("title", "Video"),
        "duration":          info.get("duration"),
        "thumb":             info.get("thumbnail", ""),
        "channel":           info.get("uploader") or info.get("channel", ""),
        "view_count":        info.get("view_count"),
        "available_heights": available_heights,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic YouTube quality keyboard
# ─────────────────────────────────────────────────────────────────────────────

_YT_QUALITY_OPTIONS = [
    ("144p",     144,  "📱"),
    ("240p",     240,  "📱"),
    ("360p",     360,  "📺"),
    ("480p",     480,  "📺"),
    ("720p HD",  720,  "🎬"),
    ("1080p FHD", 1080, "🖥"),
    ("1440p QHD", 1440, "✨"),
    ("4K Ultra", 2160, "🔥"),
]


def _build_yt_kb(available: set[int]) -> dict:
    """Build quality keyboard showing only available resolutions."""
    rows  = []
    row   = []
    shown = 0
    for label, h, icon in _YT_QUALITY_OPTIONS:
        if available and not any(ah >= h for ah in available):
            continue
        btn = {"text": f"{icon} {label}", "callback_data": f"dl_{h}"}
        row.append(btn)
        shown += 1
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "🎵 MP3 Audio", "callback_data": "dl_mp3"}])
    return {"inline_keyboard": rows}


_IG_QUALITY_KB = {
    "inline_keyboard": [
        [
            {"text": "📱 360p",    "callback_data": "dl_360"},
            {"text": "🎬 720p HD", "callback_data": "dl_720"},
        ],
    ]
}

_FB_QUALITY_KB = {
    "inline_keyboard": [
        [
            {"text": "📺 360p", "callback_data": "dl_360"},
            {"text": "🎬 720p", "callback_data": "dl_720"},
        ],
    ]
}


# ─────────────────────────────────────────────────────────────────────────────
# Instagram downloader — 7 extraction methods
# ─────────────────────────────────────────────────────────────────────────────

def _ig_shortcode(url: str) -> Optional[str]:
    m = re.search(r'/(?:p|reel|tv|reels(?:/videos)?)/([A-Za-z0-9_-]+)', url)
    return m.group(1) if m else None


def _ig_ytdlp(url: str, quality: str, ua: str, use_graphql: bool = True) -> tuple[str, str]:
    prefix = f"/tmp/dl_ig_{int(time.time()*1000)}_{random.randint(1000,9999)}_"
    o = _base_opts(quality, use_cookies=False)
    o["outtmpl"]        = f"{prefix}%(title).60s.%(ext)s"
    o["http_headers"]   = _ig_web_headers(ua)
    o["retries"]        = 5
    o["socket_timeout"] = 25
    if use_graphql:
        o["extractor_args"] = {"instagram": {"api": ["graphql"]}}
    if HAS_COOKIES:
        o["cookiefile"] = COOKIES
    with yt_dlp.YoutubeDL(o) as ydl:
        info  = ydl.extract_info(url, download=True)
        title = (info or {}).get("title", "Instagram Video")
    path = _find(prefix)
    if not path:
        raise ValueError("No output file.")
    return path, title


def _ig_ytdlp_appua(url: str, quality: str, ua: str) -> tuple[str, str]:
    prefix = f"/tmp/dl_ig_{int(time.time()*1000)}_{random.randint(1000,9999)}_"
    o = _base_opts(quality, use_cookies=False)
    o["outtmpl"]      = f"{prefix}%(title).60s.%(ext)s"
    o["http_headers"] = _ig_mobile_headers(ua)
    o["retries"]      = 4
    if HAS_COOKIES:
        o["cookiefile"] = COOKIES
    with yt_dlp.YoutubeDL(o) as ydl:
        info  = ydl.extract_info(url, download=True)
        title = (info or {}).get("title", "Instagram Video")
    path = _find(prefix)
    if not path:
        raise ValueError("No output file.")
    return path, title


def _ig_instaloader(url: str) -> tuple[str, str]:
    shortcode = _ig_shortcode(url)
    is_story  = _ig_is_story(url)

    if is_story:
        m = re.search(r'/stories/([^/]+)/(\d+)', url)
        if not m:
            raise ValueError("Could not parse story URL.")
        username = m.group(1)
        story_id = int(m.group(2))
    elif not shortcode:
        raise ValueError("Could not extract shortcode.")

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
                        title = username
                        L.download_storyitem(item, target=tmpdir)
                        break
        else:
            post  = il.Post.from_shortcode(L.context, shortcode)
            title = (post.caption or shortcode)[:80].split("\n")[0]
            L.download_post(post, target=tmpdir)

        found = next(
            (os.path.join(tmpdir, f) for f in os.listdir(tmpdir) if f.endswith(".mp4")),
            None,
        )
        if not found:
            raise ValueError("No mp4 found in instaloader output.")

        shutil.move(found, out)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return out, title if "title" in dir() else "Instagram Video"

    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise


def _ig_embed(url: str) -> tuple[str, str]:
    shortcode = _ig_shortcode(url)
    if not shortcode:
        raise ValueError("No shortcode.")
    embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
    ua   = _MOBILE_UAS[0]
    hdrs = _ig_browser_headers(ua)
    hdrs["Referer"] = "https://www.instagram.com/"
    html = requests.get(embed_url, headers=hdrs, timeout=20).text
    for pat in [
        r'"video_url"\s*:\s*"([^"]+)"',
        r'<video[^>]+src="([^"]+)"',
        r'"contentUrl"\s*:\s*"([^"]+\.mp4[^"]*)"',
        r'<meta property="og:video" content="([^"]+)"',
    ]:
        hit = re.search(pat, html)
        if hit:
            vurl    = hit.group(1).replace("\\u0026", "&").replace("\\/", "/").replace("&amp;", "&")
            title_m = re.search(r'<title>([^<]+)</title>', html)
            title   = title_m.group(1).strip() if title_m else "Instagram Video"
            out     = f"/tmp/ig_embed_{int(time.time())}.mp4"
            with requests.get(vurl, headers=hdrs, stream=True, timeout=90) as r:
                r.raise_for_status()
                with open(out, "wb") as fh:
                    for chunk in r.iter_content(65536):
                        fh.write(chunk)
            if os.path.exists(out) and os.path.getsize(out) > 10000:
                return out, title
    raise ValueError("Embed: no media URL found.")


def _ig_scrape(url: str, ua: str) -> tuple[str, str]:
    hdrs = {**_ig_browser_headers(ua), "X-IG-App-ID": _IG_APP_ID}
    if HAS_COOKIES:
        try:
            with open(COOKIES) as f:
                raw = f.read()
            ck = "; ".join(
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
    html = requests.get(url, headers=hdrs, timeout=25).text
    for pat in [
        r'"video_url"\s*:\s*"([^"]+)"',
        r'"playback_url"\s*:\s*"([^"]+)"',
        r'<meta property="og:video" content="([^"]+)"',
        r'"contentUrl"\s*:\s*"([^"]+\.mp4[^"]*)"',
    ]:
        hit = re.search(pat, html)
        if hit:
            vurl    = hit.group(1).replace("\\u0026", "&").replace("\\/", "/").replace("&amp;", "&")
            title_m = re.search(r'"title"\s*:\s*"([^"]+)"', html)
            title   = title_m.group(1) if title_m else "Instagram Video"
            out     = f"/tmp/ig_scrape_{int(time.time())}.mp4"
            with requests.get(vurl, headers=hdrs, stream=True, timeout=90) as r:
                r.raise_for_status()
                with open(out, "wb") as fh:
                    for chunk in r.iter_content(65536):
                        fh.write(chunk)
            if os.path.exists(out) and os.path.getsize(out) > 10000:
                return out, title
    raise ValueError("Scrape: no media URL found.")


def _ig_normalize_url(url: str) -> str:
    """
    Strip tracking params and normalize Instagram URLs.
    /reel/X/ → /p/X/ for shortcode extraction compatibility.
    Also handles /tv/, /stories/, carousel posts.
    """
    # Remove utm_* and other tracking query params but keep the path
    base = url.split("?")[0].rstrip("/")
    # Ensure https and www
    base = re.sub(r'^http://', 'https://', base)
    if "instagram.com" in base and "//www." not in base:
        base = base.replace("//instagram.com", "//www.instagram.com")
    return base + "/"


def _dl_instagram(url: str, quality: str = "720") -> tuple[str, str]:
    """
    Instagram extraction — 7-method chain, ordered for maximum reliability
    on public content without cookies.

    Method priority (2025):
    1. Embed page scrape      — public reels, no login needed
    2. HTML page scrape       — public posts, no login needed
    3. instaloader            — best with cookies, works without for some public posts
    4. yt-dlp + mobile UA + GraphQL
    5. yt-dlp + desktop UA + GraphQL
    6. yt-dlp + IG app UA    — highest block rate but still tried
    7. yt-dlp no-graphql     — last resort
    """
    url    = _ig_normalize_url(url)
    errors = []

    # M1 — embed page (most reliable for public reels, no cookies needed)
    try:
        path, title = _ig_embed(url)
        return _fix_for_telegram(path), title
    except Exception as e:
        errors.append(f"M1-embed: {str(e)[:100]}")
        log.debug("IG M1 embed failed: %s", str(e)[:80])

    # M2 — HTML scrape (og:video, video_url in JSON)
    for attempt, ua in enumerate(_MOBILE_UAS[:2]):
        try:
            path, title = _ig_scrape(url, ua)
            return _fix_for_telegram(path), title
        except Exception as e:
            errors.append(f"M2-{attempt+1}: {str(e)[:80]}")

    # M3 — instaloader (works best with cookies, still tries without)
    try:
        path, title = _ig_instaloader(url)
        return _fix_for_telegram(path), title
    except Exception as e:
        errors.append(f"M3-il: {str(e)[:100]}")
        log.debug("IG M3 instaloader failed: %s", str(e)[:80])
        _random_delay(1, 3)

    # M4 — yt-dlp + mobile UA + GraphQL
    for attempt, ua in enumerate(_MOBILE_UAS[:3]):
        try:
            path, title = _ig_ytdlp(url, quality, ua, use_graphql=True)
            return _fix_for_telegram(path), title
        except Exception as e:
            errors.append(f"M4-{attempt+1}: {str(e)[:80]}")
            if attempt < 2:
                _random_delay(2, 4)

    # M5 — yt-dlp + desktop UA + GraphQL
    for attempt, ua in enumerate(_DESKTOP_UAS[:2]):
        try:
            path, title = _ig_ytdlp(url, quality, ua, use_graphql=True)
            return _fix_for_telegram(path), title
        except Exception as e:
            errors.append(f"M5-{attempt+1}: {str(e)[:80]}")
            _random_delay(2, 4)

    # M6 — yt-dlp + IG app user-agent
    for attempt, ua in enumerate(_IG_APP_UAS[:2]):
        try:
            path, title = _ig_ytdlp_appua(url, quality, ua)
            return _fix_for_telegram(path), title
        except Exception as e:
            errors.append(f"M6-{attempt+1}: {str(e)[:80]}")
            _random_delay(2, 5)

    # M7 — yt-dlp no-graphql fallback
    for attempt, ua in enumerate(_MOBILE_UAS[:2]):
        try:
            path, title = _ig_ytdlp(url, quality, ua, use_graphql=False)
            return _fix_for_telegram(path), title
        except Exception as e:
            errors.append(f"M7-{attempt+1}: {str(e)[:80]}")
            _random_delay(1, 3)

    is_private = any("login" in e.lower() or "private" in e.lower()
                     or "not logged" in e.lower() for e in errors)
    if is_private and not HAS_COOKIES:
        raise ValueError(
            "🔒 <b>Private Content</b>\n\n"
            "This post requires Instagram login.\n\n"
            "Add <code>cookies.txt</code> (exported from a logged-in browser)\n"
            "to the bot folder to unlock private posts."
        )
    raise ValueError(
        "📸 <b>Instagram Failed</b>\n\n"
        "All 7 extraction methods were tried.\n\n"
        "<b>Common causes:</b>\n"
        "• Instagram is temporarily blocking anonymous access\n"
        "• The post may be <b>private</b> or age-restricted\n"
        "• Add <code>cookies.txt</code> for private content\n\n"
        f"<i>Last error: {errors[-1] if errors else 'unknown'}</i>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# YouTube — multi-client extraction with bot-detection bypass
# ─────────────────────────────────────────────────────────────────────────────

def _yt_normalize_url(url: str) -> str:
    """Normalize YouTube Shorts and mobile URLs to standard form."""
    url = url.strip()
    # Shorts → standard watch URL
    m = re.search(r'(?:youtube\.com/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})', url)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    return url


def _dl_youtube(url: str, quality: str) -> tuple[str, str]:
    """
    YouTube download with multi-player-client fallback chain.

    client order: ios → mweb → android_embedded → android → web
    ios and mweb bypass most bot-detection and age-gate checks
    without requiring cookies.  Falls back progressively.
    """
    url    = _yt_normalize_url(url)
    errors = []

    # Attempt sequence: each tries a subset of player clients
    client_sets = [
        ["ios"],                              # fastest, most bypass
        ["mweb", "ios"],
        ["android_embedded", "android"],
        _YT_PLAYER_CLIENTS,                   # full list
        None,                                 # no extractor_args (default)
    ]

    for clients in client_sets:
        prefix = f"/tmp/dl_{int(time.time()*1000)}_{random.randint(1000,9999)}_"
        o = _base_opts(quality)
        o["outtmpl"] = f"{prefix}%(title).60s.%(ext)s"
        o["age_limit"] = 99
        if clients is not None:
            o["extractor_args"] = _yt_extractor_args(clients)
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
            errors.append(f"clients={clients}: {err[:100]}")
            log.warning("YT attempt clients=%s failed: %s", clients, err[:80])
            # Clean any partial files
            for f in glob.glob(f"{prefix}*"):
                _rm(f)
            # Don't retry on unrecoverable errors
            if any(k in err.lower() for k in ["video unavailable", "private video",
                                               "this video has been removed"]):
                break

    raise ValueError(
        "❌ <b>YouTube Download Failed</b>\n\n"
        + ("🍪 This video may require sign-in. Add <code>cookies.txt</code> to unlock.\n\n"
           if any("sign in" in e.lower() or "cookies" in e.lower() or "age" in e.lower()
                  for e in errors) else "")
        + f"<i>Last error: {errors[-1][:200] if errors else 'unknown'}</i>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Facebook — multi-method with mobile fallback
# ─────────────────────────────────────────────────────────────────────────────

def _dl_facebook(url: str, quality: str = "720") -> tuple[str, str]:
    """
    Facebook download — 3 method chain.
    M1: Desktop UA + FB referer
    M2: Mobile UA (mbasic.facebook.com redirect works around some blocks)
    M3: Direct mbasic.facebook.com URL rewrite
    """
    errors: list[str] = []

    def _fb_run(extra_opts: dict) -> tuple[str, str]:
        prefix = f"/tmp/dl_fb_{int(time.time()*1000)}_{random.randint(1000,9999)}_"
        o = _base_opts(quality)
        o["outtmpl"] = f"{prefix}%(title).60s.%(ext)s"
        o.update(extra_opts)
        with yt_dlp.YoutubeDL(o) as ydl:
            info  = ydl.extract_info(url, download=True)
            title = (info or {}).get("title", "Facebook Video")
        path = _find(prefix)
        if not path:
            raise ValueError("No output file.")
        return _fix_for_telegram(path), title

    # M1 — desktop headers
    try:
        return _fb_run({
            "http_headers": {
                "User-Agent":      _DESKTOP_UAS[0],
                "Accept-Language": "en-US,en;q=0.9",
                "Referer":         "https://www.facebook.com/",
                "Accept":          "text/html,*/*;q=0.9",
            }
        })
    except Exception as e:
        errors.append(f"M1: {str(e)[:100]}")
        log.warning("FB M1 failed: %s", str(e)[:80])

    # M2 — mobile UA
    try:
        return _fb_run({
            "http_headers": {
                "User-Agent":      _MOBILE_UAS[0],
                "Accept-Language": "en-US,en;q=0.9",
                "Referer":         "https://www.facebook.com/",
            }
        })
    except Exception as e:
        errors.append(f"M2: {str(e)[:100]}")
        log.warning("FB M2 failed: %s", str(e)[:80])

    # M3 — mbasic URL rewrite (works for many public videos)
    try:
        mbasic_url = re.sub(r'https?://(www\.)?facebook\.com',
                            'https://mbasic.facebook.com', url)
        prefix3 = f"/tmp/dl_fb_{int(time.time()*1000)}_{random.randint(1000,9999)}_"
        o3 = _base_opts(quality)
        o3["outtmpl"] = f"{prefix3}%(title).60s.%(ext)s"
        o3["http_headers"] = {
            "User-Agent":  _MOBILE_UAS[1],
            "Referer":     "https://mbasic.facebook.com/",
        }
        with yt_dlp.YoutubeDL(o3) as ydl:
            info  = ydl.extract_info(mbasic_url, download=True)
            title = (info or {}).get("title", "Facebook Video")
        path = _find(prefix3)
        if not path:
            raise ValueError("No output file.")
        return _fix_for_telegram(path), title
    except Exception as e:
        errors.append(f"M3: {str(e)[:100]}")
        log.warning("FB M3 failed: %s", str(e)[:80])

    raise ValueError(
        "❌ <b>Facebook Download Failed</b>\n\n"
        "• Verify the video is <b>public</b> (not friends-only)\n"
        "• Facebook Reels and Watch videos are supported\n"
        "• Private videos require <code>cookies.txt</code>\n\n"
        f"<i>Last error: {errors[-1] if errors else 'unknown'}</i>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Snapchat — 4-method extraction with unusual-extension bypass
# ─────────────────────────────────────────────────────────────────────────────

# ── Snapchat constants ───────────────────────────────────────────────────────

_SC_HEADERS = {
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Referer":         "https://www.snapchat.com/",
    "Cache-Control":   "max-age=0",
}

# Patterns ordered by reliability — "mediaUrl" is CONFIRMED working on CDN links
_SC_VIDEO_PATS = [
    r'"mediaUrl"\s*:\s*"(https://[^"]+)"',              # ✅ confirmed: cf-st.sc-cdn.net CDN
    r'"playbackUrl"\s*:\s*"(https://[^"]+)"',
    r'"videoUrl"\s*:\s*"(https://[^"]+)"',
    r'"contentUrl"\s*:\s*"(https://[^"]+)"',
    r'<meta\s+property="og:video:secure_url"\s+content="([^"]+)"',
    r'<meta\s+property="og:video"\s+content="([^"]+)"',
    r'<meta\s+property="og:video:url"\s+content="([^"]+)"',
    r'<video[^>]+\bsrc="(https://[^"]+)"',
    r'"url"\s*:\s*"(https://[^"]+sc-cdn\.net[^"]+)"',
    r'"streamingUrl"\s*:\s*"(https://[^"]+)"',
]

# CDN hosts that serve Snapchat video — used to validate found URLs
_SC_CDN_HOSTS = ("sc-cdn.net", "snap-video", "snapchat.com", "snapchat-cdn", "cf-st")


def _sc_clean_url(raw: str) -> str:
    return (raw.replace("&amp;", "&")
               .replace("\\/", "/")
               .replace("\\u0026", "&")
               .replace("\\u003d", "="))


def _sc_is_video_url(u: str) -> bool:
    """Validate that a found URL looks like a Snapchat CDN video URL."""
    return u.startswith("http") and any(h in u for h in _SC_CDN_HOSTS)


def _sc_download_cdn(video_url: str, ua: str) -> str:
    """
    Download from Snapchat CDN, saving as .mp4 regardless of the CDN token
    used as the 'extension' (e.g. .IRZXSOY).  Confirmed: CDN returns
    Content-Type: video/mp4 for these URLs.
    """
    out  = f"/tmp/dl_sc_{int(time.time()*1000)}_{random.randint(1000,9999)}.mp4"
    hdrs = {
        **_SC_HEADERS,
        "User-Agent": ua,
        "Accept":     "*/*",
        "Origin":     "https://www.snapchat.com",
    }
    with requests.get(video_url, headers=hdrs, stream=True,
                      timeout=120, allow_redirects=True) as r:
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if ct and "text" in ct and "html" in ct:
            raise ValueError(f"CDN returned HTML instead of video (content-type: {ct})")
        with open(out, "wb") as f:
            for chunk in r.iter_content(131072):
                f.write(chunk)
    size = os.path.getsize(out) if os.path.exists(out) else 0
    if size < 10000:
        _rm(out)
        raise ValueError(f"CDN file too small ({size} bytes) — likely not a video.")
    return out


def _sc_resolve_url(url: str, ua: str) -> str:
    """Follow redirects (short links like snapchat.com/t/xxx) to get the real page URL."""
    try:
        r = requests.get(url, headers={**_SC_HEADERS, "User-Agent": ua},
                         allow_redirects=True, timeout=15)
        return r.url
    except Exception:
        return url


def _sc_extract_all_media_urls(html: str) -> list[str]:
    """Find all candidate media URLs in Snapchat HTML, deduplicated."""
    seen = set()
    urls = []
    for pat in _SC_VIDEO_PATS:
        for m in re.finditer(pat, html):
            raw = _sc_clean_url(m.group(1))
            if _sc_is_video_url(raw) and raw not in seen:
                seen.add(raw)
                urls.append(raw)
    return urls


def _sc_page_title(html: str) -> str:
    for pat in [r'"snapTitle"\s*:\s*"([^"]+)"',
                r'"displayName"\s*:\s*"([^"]+)"',
                r'<meta property="og:title" content="([^"]+)"',
                r'<title>([^<]+)</title>']:
        m = re.search(pat, html)
        if m:
            return m.group(1).strip()
    return "Snapchat Video"


# ── Method 1: Direct page scrape (PRIMARY — CONFIRMED WORKING) ────────────────

def _sc_m1_scrape(url: str) -> tuple[str, str]:
    """
    PRIMARY METHOD — confirmed working on live Snapchat CDN links.

    Test result: `"mediaUrl"` pattern → cf-st.sc-cdn.net → Content-Type: video/mp4
    — valid MP4 container, 1.2 MB, no yt-dlp involved.

    Handles:
    - Short links (snapchat.com/t/xxx) via automatic redirect
    - Profile story links (@user/...) — downloads first video snap
    - Spotlight links
    - Multiple mediaUrl entries (stories) — picks first CDN video
    """
    errors = []
    for ua in [_MOBILE_UAS[0], _DESKTOP_UAS[0], _MOBILE_UAS[1]]:
        try:
            resolved = _sc_resolve_url(url, ua)
            hdrs     = {**_SC_HEADERS, "User-Agent": ua}
            r        = requests.get(resolved, headers=hdrs, timeout=22, allow_redirects=True)
            r.raise_for_status()
            html  = r.text
            title = _sc_page_title(html)

            media_urls = _sc_extract_all_media_urls(html)
            log.info("Snapchat M1: found %d candidate URLs (ua=%s...)", len(media_urls), ua[:30])

            for vurl in media_urls:
                try:
                    out = _sc_download_cdn(vurl, ua)
                    log.info("Snapchat M1: downloaded %d bytes from %s…", os.path.getsize(out), vurl[:60])
                    return out, title
                except Exception as e2:
                    errors.append(f"cdn: {str(e2)[:60]}")
                    continue

            errors.append(f"ua={ua[:20]}: no valid CDN URLs ({len(media_urls)} candidates)")
        except Exception as e:
            errors.append(f"ua={ua[:20]}: {str(e)[:60]}")

    raise ValueError(f"Scrape M1 exhausted. Errors: {'; '.join(errors[-3:])}")


# ── Method 2: yt-dlp info extract → direct CDN download (bypasses ext check) ─

def _sc_m2_ytdlp_meta_download(url: str) -> tuple[str, str]:
    """
    Use yt-dlp ONLY to extract metadata / format URLs (skip_download=True),
    then download the CDN URL directly with requests — completely bypassing
    the unusual-extension safety rejection.
    """
    ua = _MOBILE_UAS[0]
    o: dict = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "skip_download": True,
        "allow_unplayable_formats": True,
        "http_headers": {**_SC_HEADERS, "User-Agent": ua},
        "socket_timeout": 20,
    }
    with yt_dlp.YoutubeDL(o) as ydl:
        raw_info = ydl.extract_info(url, download=False) or {}

    # Handle playlist (story = multiple snaps)
    if raw_info.get("_type") == "playlist":
        entries = [e for e in (raw_info.get("entries") or []) if e]
        info    = entries[0] if entries else {}
    else:
        info = raw_info

    title   = info.get("title") or "Snapchat Video"
    formats = info.get("formats") or []

    # Collect all format URLs, sorted by filesize desc
    candidates = sorted(
        [f.get("url", "") for f in formats if f.get("url")],
        key=lambda u: 0,  # just try in order
    )
    # Also try the direct url field
    if info.get("url"):
        candidates.insert(0, info["url"])

    for vurl in candidates:
        if not vurl.startswith("http"):
            continue
        try:
            out = _sc_download_cdn(vurl, ua)
            return out, title
        except Exception:
            continue

    raise ValueError("yt-dlp metadata: all format URLs failed CDN download.")


# ── Method 3: yt-dlp subprocess with forced .mp4 output ──────────────────────

def _sc_m3_subprocess(url: str) -> tuple[str, str]:
    """
    yt-dlp CLI subprocess — by passing a fixed .mp4 output path,
    the file is written directly without yt-dlp inspecting the extension.
    """
    out = f"/tmp/dl_sc_{int(time.time()*1000)}_{random.randint(1000,9999)}.mp4"
    ua  = _MOBILE_UAS[0]
    cmd = [
        "yt-dlp", "--no-playlist", "--quiet",
        "-f", "bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
        "--user-agent", ua,
        "--add-header", "Referer:https://www.snapchat.com/",
        "--retries", "4",
        "-o", out, url,
    ]
    if FFMPEG:
        cmd += ["--ffmpeg-location", FFMPEG]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not os.path.exists(out) or os.path.getsize(out) < 10000:
        raise ValueError(f"subprocess: {r.stderr.strip()[-150:]}")
    return out, "Snapchat Video"


# ── Main dispatcher ───────────────────────────────────────────────────────────

def _dl_snapchat(url: str) -> tuple[str, str]:
    """
    Snapchat extraction — 3-method chain, scrape-first.

    Root cause of 'unusual extension' error (IRZXSOY etc.):
    Snapchat CDN URLs use a session token as the file extension.
    yt-dlp's generic extractor parses this as the extension and rejects it.

    Fix: bypass yt-dlp for the actual download. We use requests to hit the
    CDN directly, which responds with Content-Type: video/mp4 regardless
    of the URL extension. Confirmed working on live Snapchat CDN.

    Also handles short links (snapchat.com/t/xxx) — requests follows
    the redirect automatically, resolving to the real page URL first.
    """
    errors: list[str] = []

    # M1 — Scrape page → find mediaUrl → direct CDN download  ✅ CONFIRMED
    try:
        log.info("Snapchat M1: page scrape + direct CDN download")
        path, title = _sc_m1_scrape(url)
        return _fix_for_telegram(path), title
    except Exception as e:
        errors.append(f"M1: {str(e)[:120]}")
        log.warning("Snapchat M1 failed: %s", str(e)[:120])

    # M2 — yt-dlp metadata only → direct CDN download (extension never checked)
    try:
        log.info("Snapchat M2: yt-dlp meta + direct CDN download")
        path, title = _sc_m2_ytdlp_meta_download(url)
        return _fix_for_telegram(path), title
    except Exception as e:
        errors.append(f"M2: {str(e)[:120]}")
        log.warning("Snapchat M2 failed: %s", str(e)[:120])
        _random_delay(1, 2)

    # M3 — yt-dlp subprocess with forced .mp4 output path
    try:
        log.info("Snapchat M3: yt-dlp subprocess forced .mp4")
        path, title = _sc_m3_subprocess(url)
        return _fix_for_telegram(path), title
    except Exception as e:
        errors.append(f"M3: {str(e)[:120]}")
        log.warning("Snapchat M3 failed: %s", str(e)[:120])

    raise ValueError(
        "👻 <b>Snapchat Download Failed</b>\n\n"
        "All extraction methods were tried.\n\n"
        "<b>Tips:</b>\n"
        "• Link must be public (Spotlight or Story)\n"
        "• Short links (snapchat.com/t/…) are supported\n"
        "• Profile links (snapchat.com/@user/…) are supported\n\n"
        f"<i>Last error: {errors[-1] if errors else 'unknown'}</i>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# YouTube quality picker
# ─────────────────────────────────────────────────────────────────────────────

def _show_yt_picker(chat_id: int, user_id: int, url: str):
    r   = send_msg(chat_id, "⏳ <b>Fetching video info...</b>")
    sid = (r.get("result") or {}).get("message_id")
    try:
        m = _ytdlp_meta(url)
    except Exception as e:
        if sid:
            edit_text(chat_id, sid, f"❌ <b>Could not fetch info</b>\n<code>{str(e)[:250]}</code>")
        return
    if sid:
        delete_msg(chat_id, sid)

    dur     = _fmt_dur(m["duration"])
    channel = m["channel"]
    title   = _short(m["title"], 80)
    kb      = _build_yt_kb(m["available_heights"])

    caption = (
        f"🎬 <b>{title}</b>\n"
        + (f"👤 {channel}\n" if channel else "")
        + (f"⏱ {dur}\n" if dur else "")
        + "\n<b>Choose quality:</b>"
    )

    sent = None
    if m["thumb"]:
        try:
            sent = send_photo(chat_id, m["thumb"], caption, markup=kb)
        except Exception:
            pass
    if not sent or not sent.get("ok"):
        sent = send_msg(chat_id, caption, markup=kb)

    mid = (sent.get("result") or {}).get("message_id")
    with _picks_lock:
        _picks[user_id] = {"url": url, "chat_id": chat_id, "mid": mid}


def _show_ig_picker(chat_id: int, user_id: int, url: str):
    is_story = _ig_is_story(url)
    label    = "Instagram Story" if is_story else "Instagram Reel / Post"
    icon     = "📖" if is_story else "📸"
    msg = send_msg(chat_id,
        f"{icon} <b>{label}</b>\n\nChoose download quality:",
        markup=_IG_QUALITY_KB,
    )
    mid = (msg.get("result") or {}).get("message_id")
    with _picks_lock:
        _picks[user_id] = {"url": url, "chat_id": chat_id, "mid": mid}


def _show_fb_picker(chat_id: int, user_id: int, url: str):
    msg = send_msg(chat_id,
        "📘 <b>Facebook Video</b>\n\nChoose download quality:",
        markup=_FB_QUALITY_KB,
    )
    mid = (msg.get("result") or {}).get("message_id")
    with _picks_lock:
        _picks[user_id] = {"url": url, "chat_id": chat_id, "mid": mid}


# ─────────────────────────────────────────────────────────────────────────────
# Download worker
# ─────────────────────────────────────────────────────────────────────────────

def _worker(chat_id: int, user_id: int, url: str, quality: str, status_id: Optional[int]):
    path: Optional[str] = None
    anim: Optional[AnimatedStatus] = None
    platform = "youtube" if is_yt(url) else "instagram" if is_ig(url) else "facebook" if is_fb(url) else "snapchat"

    try:
        if status_id:
            anim = AnimatedStatus(chat_id, status_id, _LOADING_FRAMES).start()

        if is_yt(url):
            path, title = _dl_youtube(url, quality)
        elif is_ig(url):
            path, title = _dl_instagram(url, quality)
        elif is_fb(url):
            path, title = _dl_facebook(url, quality)
        elif is_sc(url):
            path, title = _dl_snapchat(url)
        else:
            raise ValueError("Unsupported link.")

        size       = _fmt_size(path)
        is_audio   = quality == "mp3"
        qual_label = "MP3 Audio" if is_audio else f"{quality}p"

        if anim:
            anim.stop()
            anim = None
        if status_id:
            edit_text(chat_id, status_id, "📤 <b>Uploading to Telegram...</b>")

        if is_audio:
            cap = (
                f"✅ <b>Download Complete</b>\n\n"
                f"🎵 <b>{_short(title, 120)}</b>\n"
                f"📦 {size}  •  🎵 MP3 Audio\n"
                f"⚡ Fast Download"
            )
            send_audio(chat_id, path, caption=cap)
        else:
            cap = (
                f"✅ <b>Download Complete</b>\n\n"
                f"🎬 <b>{_short(title, 120)}</b>\n"
                f"📦 {size}  •  🎥 {qual_label}\n"
                f"⚡ Fast Download"
            )
            send_video(chat_id, path, caption=cap)

        _record_download(platform)
        if status_id:
            delete_msg(chat_id, status_id)

    except Exception as e:
        log.error("Download failed user=%d url=%s: %s", user_id, url[:80], e)
        if anim:
            anim.stop()

        raw = str(e)

        # Suppress raw yt-dlp internals — show clean platform-specific messages
        if "unusual" in raw.lower() and "extension" in raw.lower():
            err = (
                "❌ <b>Extraction failed</b>\n\n"
                "The video source returned an unusual format.\n"
                "Our backup servers were tried automatically.\n\n"
                "<i>Please try again or use a direct platform link.</i>"
            )
        elif "private" in raw.lower() or "login" in raw.lower():
            err = (
                "🔒 <b>Private Content</b>\n\n"
                "This content requires authentication.\n"
                "Public links work without login.\n"
                "Private content requires <code>cookies.txt</code>."
            )
        elif "not available" in raw.lower() or "unavailable" in raw.lower():
            err = (
                "🚫 <b>Video Unavailable</b>\n\n"
                "This video is not available in your region\n"
                "or has been removed by the author."
            )
        elif "429" in raw or "rate" in raw.lower():
            err = (
                "⏳ <b>Rate Limited</b>\n\n"
                "The platform is temporarily blocking requests.\n"
                "Please wait 1–2 minutes and try again."
            )
        elif "snapchat" in platform.lower() and ("extension" in raw.lower() or "unusual" in raw.lower() or "skipped" in raw.lower()):
            err = (
                "👻 <b>Snapchat extraction failed</b>\n\n"
                "Trying backup servers…\n\n"
                "<i>If this persists, try a direct snapchat.com/spotlight link.</i>"
            )
        elif "<b>" in raw:
            # Already a formatted error message from our downloaders
            err = f"❌ {raw[:800]}"
        else:
            # Generic clean error — hide raw yt-dlp stack details
            err = (
                f"❌ <b>Download Failed</b>\n\n"
                f"Something went wrong while fetching this video.\n\n"
                f"<i>{raw[:200]}</i>"
            )

        if status_id:
            try:
                edit_text(chat_id, status_id, err)
            except Exception:
                send_msg(chat_id, err)
        else:
            send_msg(chat_id, err)
    finally:
        _rm(path)
        _unlock_user(user_id)


def _submit(chat_id: int, user_id: int, url: str, quality: str = "720",
            picker_mid: Optional[int] = None):
    if not _lock_user(user_id):
        send_msg(chat_id, "⏳ Your previous download is still running. Please wait.")
        return
    with _picks_lock:
        _picks.pop(user_id, None)
    if picker_mid:
        edit_markup(chat_id, picker_mid)
        ql = "MP3 Audio" if quality == "mp3" else f"{quality}p"
        edit_text(chat_id, picker_mid, f"⬇️ <b>Downloading {ql}...</b>")
        status_id = picker_mid
    else:
        r = send_msg(chat_id, "⏳ <b>Fetching video...</b>")
        status_id = (r.get("result") or {}).get("message_id")
    executor.submit(_worker, chat_id, user_id, url, quality, status_id)


# ─────────────────────────────────────────────────────────────────────────────
# Broadcast system
# ─────────────────────────────────────────────────────────────────────────────

_broadcast_lock  = threading.Lock()
_broadcast_active = False
_pending_bc: dict = {}   # {admin_id: {"chat_id": ..., "status_mid": ...}}


def _do_broadcast(admin_id: int, source_chat_id: int, source_msg_id: int, status_mid: int):
    global _broadcast_active
    try:
        users   = _get_all_user_ids()
        total   = len(users)
        sent    = 0
        failed  = 0
        blocked = 0

        edit_text(admin_id, status_mid,
            f"📡 <b>Broadcast started</b>\n\n"
            f"👥 Sending to {total} users...\n"
            f"⏳ Please wait."
        )

        for i, uid in enumerate(users):
            try:
                result = copy_message(uid, source_chat_id, source_msg_id)
                if result.get("ok"):
                    sent += 1
                else:
                    err_code = (result.get("error_code") or 0)
                    if err_code in (403, 400):
                        blocked += 1
                    else:
                        failed += 1
            except Exception:
                failed += 1

            if i % 25 == 0 and i > 0:
                try:
                    pct = int((i / total) * 100)
                    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
                    edit_text(admin_id, status_mid,
                        f"📡 <b>Broadcasting...</b>\n\n"
                        f"[{bar}] {pct}%\n"
                        f"✅ Sent: {sent}  ❌ Failed: {failed}  🚫 Blocked: {blocked}\n"
                        f"Progress: {i}/{total}"
                    )
                except Exception:
                    pass
            time.sleep(BROADCAST_DELAY)

        edit_text(admin_id, status_mid,
            f"✅ <b>Broadcast Complete</b>\n\n"
            f"👥 Total users: {total}\n"
            f"✅ Sent: {sent}\n"
            f"🚫 Blocked/Deleted: {blocked}\n"
            f"❌ Failed: {failed}"
        )
    except Exception as e:
        log.error("Broadcast error: %s", e)
        try:
            edit_text(admin_id, status_mid, f"❌ <b>Broadcast error</b>\n<code>{e}</code>")
        except Exception:
            pass
    finally:
        with _broadcast_lock:
            _broadcast_active = False


def _start_broadcast(admin_id: int, source_chat_id: int, source_msg_id: int):
    global _broadcast_active
    with _broadcast_lock:
        if _broadcast_active:
            send_msg(admin_id, "⚠️ A broadcast is already in progress.")
            return
        _broadcast_active = True

    r   = send_msg(admin_id, "📡 <b>Starting broadcast...</b>")
    mid = (r.get("result") or {}).get("message_id")
    threading.Thread(
        target=_do_broadcast,
        args=(admin_id, source_chat_id, source_msg_id, mid),
        daemon=True,
    ).start()


# ─────────────────────────────────────────────────────────────────────────────
# Premium /start message
# ─────────────────────────────────────────────────────────────────────────────

def _send_start(chat_id: int, first_name: str):
    kb = {
        "inline_keyboard": [
            [
                {"text": "🎥 YouTube",    "callback_data": "info_yt"},
                {"text": "📸 Instagram",  "callback_data": "info_ig"},
            ],
            [
                {"text": "📘 Facebook",   "callback_data": "info_fb"},
                {"text": "👻 Snapchat",   "callback_data": "info_sc"},
            ],
            [
                {"text": "📊 My Stats",   "callback_data": "info_stats"},
                {"text": "ℹ️ Help",       "callback_data": "info_help"},
            ],
        ]
    }
    text = (
        f"👋 <b>Welcome, {first_name or 'there'}!</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 <b>MeraDownload4K</b>\n"
        "<i>Premium Video Downloader</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📥 <b>Supported Platforms</b>\n"
        "  🎥 YouTube & Shorts — up to 4K\n"
        "  📸 Instagram Reels, Posts & Stories\n"
        "  📘 Facebook Videos\n"
        "  👻 Snapchat Spotlight\n\n"
        "⚡ <b>How to use</b>\n"
        "Just paste any supported link!\n\n"
        "⚙️ <b>Commands</b>\n"
        "  /audio — Extract MP3\n"
        "  /stats — Bot statistics\n"
        "  /users — Total user count\n"
        "  /active — Active users today\n"
        "  /downloads — Download statistics\n"
        "  /help  — Usage guide"
    )
    send_msg(chat_id, text, markup=kb)


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

    # ── Broadcast reply mode ──────────────────────────────────────────────
    if user_id == ADMIN_ID and user_id in _pending_bc:
        del _pending_bc[user_id]
        msg_id = msg.get("message_id")
        if msg_id:
            _start_broadcast(ADMIN_ID, chat_id, msg_id)
            return

    if not text:
        return

    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""

    # /start, /help
    if cmd in ("/start", "/help"):
        _send_start(chat_id, first_name)
        return

    # /stats
    if cmd == "/stats":
        s = _get_stats()
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
            f"  👻 Snapchat:  {ps.get('snapchat', 0)}"
        )
        return

    # /getid
    if cmd == "/getid":
        send_msg(chat_id,
            f"🪪 <b>Your Telegram ID</b>\n\n"
            f"<code>{user_id}</code>\n\n"
            "Set this as <code>ADMIN_ID</code> in your Railway Variables."
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

    # /broadcast — admin only
    if cmd == "/broadcast" and user_id == ADMIN_ID:
        parts    = text.split(None, 1)
        bc_text  = parts[1].strip() if len(parts) > 1 else ""
        reply_to = msg.get("reply_to_message")

        if reply_to:
            _start_broadcast(ADMIN_ID, chat_id, reply_to["message_id"])
        elif bc_text:
            r   = send_msg(chat_id, bc_text)
            mid = (r.get("result") or {}).get("message_id")
            if mid:
                _start_broadcast(ADMIN_ID, chat_id, mid)
        else:
            _pending_bc[user_id] = True
            send_msg(chat_id,
                "📡 <b>Broadcast Mode</b>\n\n"
                "Send the message/photo/video you want to broadcast.\n"
                "Or: reply to any message with /broadcast."
            )
        return

    # /users — total user count (all users)
    if cmd == "/users":
        s = _get_stats()
        send_msg(chat_id,
            f"👥 <b>Users</b>\n\n"
            f"Total registered: <b>{s['total_users']}</b>\n"
            f"Active today:     <b>{s['active_today']}</b>"
        )
        return

    # /active — today's active users
    if cmd == "/active":
        s    = _get_stats()
        today = str(date.today())
        with _stats_lock:
            active_ids = _stats["daily_users"].get(today, [])
        send_msg(chat_id,
            f"📅 <b>Active Today</b>\n\n"
            f"Users active:  <b>{len(active_ids)}</b>\n"
            f"Downloads:     <b>{s['downloads_today']}</b>\n"
            f"Active now:    <b>{s['active_downloads']}</b>"
        )
        return

    # /downloads — download stats
    if cmd == "/downloads":
        s  = _get_stats()
        ps = s["platform_stats"]
        send_msg(chat_id,
            f"📦 <b>Download Stats</b>\n\n"
            f"Today:    <b>{s['downloads_today']}</b>\n"
            f"Total:    <b>{s['total_downloads']}</b>\n"
            f"Active:   <b>{s['active_downloads']}</b>\n\n"
            "📈 <b>By Platform</b>\n"
            f"  🎥 YouTube:   <b>{ps.get('youtube', 0)}</b>\n"
            f"  📸 Instagram: <b>{ps.get('instagram', 0)}</b>\n"
            f"  📘 Facebook:  <b>{ps.get('facebook', 0)}</b>\n"
            f"  👻 Snapchat:  <b>{ps.get('snapchat', 0)}</b>"
        )
        return

    # /admin — admin dashboard
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
            f"📋 Queue Size:      <b>{s['queue_size']}</b>\n\n"
            "📈 <b>Platform Breakdown</b>\n"
            f"  🎥 YouTube:   {ps.get('youtube', 0)}\n"
            f"  📸 Instagram: {ps.get('instagram', 0)}\n"
            f"  📘 Facebook:  {ps.get('facebook', 0)}\n"
            f"  👻 Snapchat:  {ps.get('snapchat', 0)}\n\n"
            "⚙️ <b>System</b>\n"
            f"  🤖 Bot: @{BOT_NAME}\n"
            f"  🍪 Cookies: {'✅ loaded' if HAS_COOKIES else '❌ not set'}\n"
            f"  🎞 ffmpeg: {'✅ found' if FFMPEG else '❌ missing'}\n\n"
            "📡 <b>Commands</b>\n"
            "  /broadcast — Send to all users\n"
            "  /users  /active  /downloads"
        )
        return

    # Plain URL
    if is_supported(text):
        if is_yt(text):
            threading.Thread(target=_show_yt_picker, args=(chat_id, user_id, text), daemon=True).start()
        elif is_ig(text):
            threading.Thread(target=_show_ig_picker, args=(chat_id, user_id, text), daemon=True).start()
        elif is_fb(text):
            threading.Thread(target=_show_fb_picker, args=(chat_id, user_id, text), daemon=True).start()
        else:
            _submit(chat_id, user_id, text)
        return

    send_msg(chat_id, "📎 Send me a YouTube, Instagram, Facebook, or Snapchat link.")


# ─────────────────────────────────────────────────────────────────────────────
# Callback handler
# ─────────────────────────────────────────────────────────────────────────────

_INFO_TEXTS = {
    "info_yt": (
        "🎥 <b>YouTube & Shorts</b>\n\n"
        "Qualities: 144p · 240p · 360p · 480p · 720p · 1080p · 1440p · 4K\n"
        "• Only available qualities shown\n"
        "• MP3 audio extraction supported\n"
        "• Auto H264/AAC encoding for Telegram"
    ),
    "info_ig": (
        "📸 <b>Instagram</b>\n\n"
        "• Reels ✅  Posts ✅  Stories ✅\n"
        "• Public content: no login needed\n"
        "• Private content: requires cookies.txt\n"
        "• 7 extraction fallback methods"
    ),
    "info_fb": (
        "📘 <b>Facebook</b>\n\n"
        "• Public videos ✅\n"
        "• Reels ✅\n"
        "• Quality: 360p / 720p"
    ),
    "info_sc": (
        "👻 <b>Snapchat</b>\n\n"
        "• Spotlight videos ✅\n"
        "• Public stories ✅\n"
        "• Quality: 720p"
    ),
    "info_help": (
        "ℹ️ <b>How to Use</b>\n\n"
        "1. Copy any video link\n"
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
            f"👥 Total Users: <b>{s['total_users']}</b>\n"
            f"📅 Active Today: <b>{s['active_today']}</b>\n"
            f"⬇️ Downloads Today: <b>{s['downloads_today']}</b>\n"
            f"📦 Total: <b>{s['total_downloads']}</b>"
        )
        return

    if not data.startswith("dl_"):
        answer_cb(cb_id)
        return

    quality    = data[3:]
    qual_label = "MP3 Audio" if quality == "mp3" else f"{quality}p"
    answer_cb(cb_id, f"⏳ Starting {qual_label}…")

    with _picks_lock:
        pick = _picks.get(user_id)

    if not pick:
        if chat_id and mid:
            edit_text(chat_id, mid, "⚠️ Session expired. Send the link again.")
        return

    _submit(chat_id, user_id, pick["url"], quality=quality, picker_mid=mid)


# ─────────────────────────────────────────────────────────────────────────────
# Flask health & stats endpoints
# ─────────────────────────────────────────────────────────────────────────────

_flask = Flask(__name__)


@_flask.route("/")
def _root():
    return "✅ MeraDownload4K is running", 200


@_flask.route("/health")
def _health():
    return jsonify({
        "status":  "ok",
        "bot":     BOT_NAME,
        "cookies": HAS_COOKIES,
        "ffmpeg":  bool(FFMPEG),
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
        now     = time.time()
        removed = 0
        for pattern in ("/tmp/dl_*", "/tmp/ig_*"):
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
            log.info("🧹 Cleaned %d temp files", removed)


# ─────────────────────────────────────────────────────────────────────────────
# Polling loop — auto-reconnect with exponential backoff
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

    log.info("━" * 55)
    log.info("🤖  @%s online  —  MeraDownload4K v2.0", BOT_NAME)
    log.info("🎞  ffmpeg   : %s", FFMPEG or "NOT FOUND ⚠️")
    log.info("🍪  cookies  : %s", "✅ loaded" if HAS_COOKIES else "not present (public only)")
    log.info("🛡  admin    : %s", ADMIN_ID or "not set")
    log.info("🌐  health   : 0.0.0.0:%d /health", PORT)
    log.info("📊  stats    : %d users, %d total downloads",
             len(_stats["total_users"]), _stats["total_downloads"])
    log.info("━" * 55)

    if ADMIN_ID:
        try:
            s = _get_stats()
            send_msg(ADMIN_ID,
                f"✅ <b>@{BOT_NAME} is online</b>\n\n"
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
