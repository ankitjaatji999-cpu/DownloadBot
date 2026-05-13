# MeraDownload4K — Premium Telegram Downloader Bot v2.0

A production-ready Telegram bot that downloads videos from YouTube, Instagram, Facebook, and Snapchat with premium UI and advanced features.

---

## Features

### Downloads
| Platform   | Formats                        | Max Quality |
|------------|-------------------------------|-------------|
| YouTube    | Video + MP3 Audio             | 4K (2160p)  |
| Instagram  | Reels, Posts, Stories         | 720p        |
| Facebook   | Videos, Reels                 | 720p        |
| Snapchat   | Spotlight, Stories            | 720p        |

### YouTube Qualities
`144p` · `240p` · `360p` · `480p` · `720p` · `1080p` · `1440p` · `4K` · `MP3`

Only available qualities are shown — auto-detected per video.

### Instagram — 7 Extraction Methods
1. yt-dlp + GraphQL + mobile user agents
2. yt-dlp + GraphQL + desktop user agents
3. yt-dlp anonymous web API
4. yt-dlp + Instagram app user agents
5. instaloader (with optional cookie auth)
6. Embed page scrape
7. HTML og:video / JSON scrape

### Premium UI
- Animated loading status (cycles while downloading)
- Dynamic quality keyboard (only shows available qualities)
- Thumbnail preview with video info for YouTube
- Styled completion message with file size and quality label
- Interactive /start with platform info buttons

### Broadcast System
Send messages to all users:
```
/broadcast Your message here
```
Or reply to any message/photo/video with `/broadcast`.

### Analytics
- Total users, active today, downloads today
- Per-platform download counts (YouTube / Instagram / Facebook / Snapchat)
- Admin dashboard via `/admin`
- Public `/stats` command
- HTTP `/stats` endpoint for monitoring

---

## Deployment — Railway

### 1. Set up your repository
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR/REPO.git
git push -u origin main
```

### 2. Connect to Railway
1. Go to [railway.app](https://railway.app)
2. New Project → Deploy from GitHub repo
3. Select your repository

### 3. Set environment variables
In Railway → Your Service → Variables:

| Variable   | Required | Description                              |
|------------|----------|------------------------------------------|
| `BOT_TOKEN`| ✅ Yes   | Token from [@BotFather](https://t.me/BotFather) |
| `ADMIN_ID` | Optional | Your numeric Telegram ID (use /getid)    |
| `PORT`     | Auto     | Set automatically by Railway             |

### 4. Deploy
Railway auto-detects `railway.toml` and installs `ffmpeg` via nixPackages.

Health check endpoint: `GET /health`  
Stats endpoint: `GET /stats`

---

## Optional — Instagram Authentication (Private Content)

To download private Instagram posts:

1. Install the **"Get cookies.txt LOCALLY"** extension in Chrome
2. Log into Instagram
3. Click the extension and export cookies for `instagram.com`
4. Save the file as `cookies.txt` in the same folder as `bot.py`
5. Deploy — the bot detects it automatically

---

## File Structure

```
bot.py              ← Main bot (all-in-one)
requirements.txt    ← Python dependencies
railway.toml        ← Railway deployment config
Procfile            ← Process declaration
runtime.txt         ← Python 3.12 pin
.gitignore          ← Excludes cookies.txt and stats.json
README.md           ← This file
cookies.txt         ← (Optional) Instagram session cookies
stats.json          ← Auto-generated, persists download stats
```

---

## Commands

| Command            | Access  | Description                      |
|--------------------|---------|----------------------------------|
| `/start`           | All     | Welcome message with platform buttons |
| `/help`            | All     | Usage guide                      |
| `/stats`           | All     | Bot statistics                   |
| `/audio <link>`    | All     | Download as MP3                  |
| `/getid`           | All     | Show your Telegram ID            |
| `/admin`           | Admin   | Full admin dashboard             |
| `/broadcast <msg>` | Admin   | Broadcast to all users           |

---

## HTTP Endpoints

| Endpoint  | Description                  |
|-----------|------------------------------|
| `GET /`   | Bot running status           |
| `GET /health` | Health check (Railway) |
| `GET /stats`  | JSON stats for monitoring |

---

## Local Development

```bash
pip install -r requirements.txt
export BOT_TOKEN=your_token_here
export ADMIN_ID=your_telegram_id
python bot.py
```

> Note: ffmpeg must be installed locally. On Ubuntu: `sudo apt install ffmpeg`
