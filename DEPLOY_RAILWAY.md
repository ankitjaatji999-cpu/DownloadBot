# Railway Deployment Guide — MeraDownload4K v2

Complete step-by-step deployment. Follow in order.

---

## STEP 1 — Create a GitHub Repository

1. Go to https://github.com/new
2. Name it anything (e.g. `meradownload4k`)
3. Set to **Private**
4. Do NOT initialize with README
5. Click **Create repository**

---

## STEP 2 — Upload Your Bot Files

Extract `MeraDownload4K_v2.zip` and push to GitHub:

```bash
# In the extracted folder:
git init
git add .
git commit -m "MeraDownload4K v2 — initial deploy"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/meradownload4k.git
git push -u origin main
```

Or use GitHub Desktop / drag-drop files into the repo via browser.

The repo must contain these files at the **root level** (not in a subfolder):
```
bot.py
requirements.txt
railway.toml
Procfile
runtime.txt
.gitignore
```

---

## STEP 3 — Deploy on Railway

1. Go to https://railway.app and log in
2. Click **+ New Project**
3. Select **Deploy from GitHub repo**
4. Authorize Railway to access GitHub (first time only)
5. Select your `meradownload4k` repository
6. Railway detects `railway.toml` automatically
7. Click **Deploy Now**

Railway will:
- Install Python 3.12
- Install ffmpeg + ffprobe via Nix
- Install all pip packages from `requirements.txt`
- Start: `python bot.py`

---

## STEP 4 — Set Environment Variables

In Railway → Your Service → **Variables** tab, add:

| Key | Value | Notes |
|-----|-------|-------|
| `BOT_TOKEN` | `7123456789:AAF...` | From @BotFather — **required** |
| `ADMIN_ID` | `123456789` | Your numeric Telegram ID — optional |

**How to get your BOT_TOKEN:**
1. Open Telegram → search @BotFather
2. Send `/newbot`
3. Follow prompts, copy the token

**How to get your ADMIN_ID:**
1. After bot is deployed, message your bot `/getid`
2. Or message @userinfobot on Telegram

After adding variables, Railway **redeploys automatically**.

---

## STEP 5 — Verify Health Check

Railway checks `/health` every 30 seconds. Once deployed:

```
https://YOUR-APP.railway.app/health
```

Expected response:
```json
{
  "status": "ok",
  "bot": "YourBotUsername",
  "cookies": false,
  "ffmpeg": true
}
```

If `ffmpeg` is `false` — check that `railway.toml` contains:
```toml
[build.nixpacks]
nixPackages = ["ffmpeg", "ffprobe", "python312"]
```

---

## STEP 6 — Test Your Bot

Open Telegram and test each platform:

### YouTube
```
https://youtu.be/dQw4w9WgXcQ
```
Expected: Quality picker appears (144p–4K + MP3)

### YouTube MP3
```
/audio https://youtu.be/dQw4w9WgXcQ
```
Expected: MP3 file sent

### YouTube 1080p
Send any YouTube link → tap 🖥 1080p FHD

### YouTube 4K
Send a 4K YouTube video → tap 🔥 4K Ultra (only shown if video has 4K)

### Instagram Reel
```
https://www.instagram.com/reel/XXXXXXXXX/
```
Expected: Quality picker → download

### Instagram Story
```
https://www.instagram.com/stories/username/1234567890/
```

### Snapchat Short Link
```
https://snapchat.com/t/mCcbpWFJ
```
Expected: Video downloaded via direct CDN scrape (no yt-dlp extension errors)

### Snapchat Profile Story
```
https://www.snapchat.com/@username/STORYID
```

### Facebook
```
https://www.facebook.com/watch?v=XXXXXXXXXX
```

---

## STEP 7 — Test Broadcast System (Admin Only)

Send to your bot:
```
/broadcast Hello! Bot is live 🚀
```

Expected:
- Progress bar updates
- Message sent to all registered users
- Completion report (sent / blocked / failed)

To broadcast a photo or video:
1. Send the photo/video to your bot chat
2. Reply to it with `/broadcast`

---

## STEP 8 — Verify 24/7 Uptime

Railway keeps your bot alive automatically:
- `restartPolicyType = "ON_FAILURE"` — auto-restarts on crashes
- `restartPolicyMaxRetries = 10` — up to 10 restart attempts
- Health check at `/health` — Railway monitors this

**Optional: Uptime monitoring**
Add a free monitor at https://uptimerobot.com:
- URL: `https://YOUR-APP.railway.app/health`
- Interval: 5 minutes
- Alert: email if down

---

## STEP 9 — Optional: Instagram Private Content

To download private Instagram posts:

1. In Chrome, install **"Get cookies.txt LOCALLY"** extension
2. Log into instagram.com
3. Click the extension → Export → select `instagram.com`
4. Save the file as `cookies.txt`
5. Add it to your GitHub repo (same level as `bot.py`)
6. **Important:** Make the repo Private first (cookies = sensitive)
7. Push the file — Railway redeploys automatically

The bot detects `cookies.txt` on startup and logs: `🍪 cookies: ✅ loaded`

---

## Monitoring & Logs

### View live logs
Railway → Your Service → **Logs** tab

### Bot statistics endpoint
```
https://YOUR-APP.railway.app/stats
```

Returns:
```json
{
  "total_users": 42,
  "active_today": 7,
  "total_downloads": 150,
  "downloads_today": 12,
  "platform_stats": {
    "youtube": 80,
    "instagram": 45,
    "facebook": 15,
    "snapchat": 10
  }
}
```

### Admin dashboard in Telegram
```
/admin
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Bot doesn't respond | Check `BOT_TOKEN` is set correctly in Variables |
| `ffmpeg: false` in /health | Verify `nixPackages` in `railway.toml` |
| Instagram fails | Bot uses 7 fallback methods. If all fail, add `cookies.txt` |
| Snapchat extension error | Fixed in v2 — uses direct CDN download, bypasses yt-dlp |
| YouTube age-restricted | Add cookies.txt from a logged-in Google account |
| Bot crashes on startup | Check Logs tab for Python errors |
| Deploy fails | Ensure all files are at repo root, not in a subfolder |

---

## File Structure (in GitHub repo root)

```
bot.py              ← Main bot
requirements.txt    ← pip packages (yt-dlp nightly from GitHub)
railway.toml        ← Railway config (ffmpeg, healthcheck)
Procfile            ← worker: python bot.py
runtime.txt         ← python-3.12
.gitignore          ← excludes cookies.txt, stats.json
cookies.txt         ← OPTIONAL: Instagram auth (gitignored by default)
```

---

## Environment Variables Summary

| Variable | Required | Where to get |
|----------|----------|-------------|
| `BOT_TOKEN` | ✅ Yes | @BotFather on Telegram |
| `ADMIN_ID` | Optional | /getid command or @userinfobot |
| `PORT` | Auto | Set by Railway, do not set manually |
