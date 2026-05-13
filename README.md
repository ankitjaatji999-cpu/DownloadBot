# MeraDownload4K — Premium Telegram Downloader Bot v3.0

Production-ready Telegram bot with premium UI, real download progress bars, 6-platform support, and full cookie authentication.

---

## Platforms & Qualities

| Platform    | Supported Content             | Max Quality | Methods         |
|-------------|-------------------------------|-------------|-----------------|
| YouTube     | Videos, Shorts, MP3           | 4K (2160p)  | 5-client bypass |
| Instagram   | Reels, Posts, Stories         | 1080p       | 7-method chain  |
| Facebook    | Videos, Reels                 | 1080p       | 3-method chain  |
| Snapchat    | Spotlight, Stories            | 720p        | CDN scrape + yt-dlp |
| Reddit      | Video posts (v.redd.it)       | 1080p       | 2-method chain  |
| Twitter/X   | Tweet videos                  | 1080p       | 3-method chain  |

---

## Key Features

### Premium UI
- Animated loading status cycles while downloading
- **Real progress bar** with percentage + speed from yt-dlp (throttled to avoid rate limits)
- YouTube thumbnail preview with title, channel, duration, views
- Dynamic quality keyboard — only shows qualities the video actually has
- Clean premium completion message with file size and quality label
- Never shows raw yt-dlp errors — always clean user-friendly messages

### YouTube — 5-Client Anti-Bot Bypass
Tries these player clients in order:
1. `ios` — bypasses most bot-detection without cookies
2. `mweb + ios`
3. `android_embedded + android`
4. All clients combined
5. Default fallback

### Instagram — 7-Method Chain (2025 optimized)
Ordered for maximum reliability on public content without cookies:
1. **Embed page scrape** — works on most public reels, no login needed
2. **HTML og:video scrape** — 2 mobile UAs
3. **instaloader** — best with cookies, still tries without
4. **yt-dlp mobile UA** — 3 UAs with GraphQL
5. **yt-dlp desktop UA** — 2 UAs
6. **yt-dlp IG App UA** — 2 UAs
7. **yt-dlp no-GraphQL fallback**

### Full Cookie Authentication
A single `cookies.txt` (Netscape format) unlocks all platforms:
- YouTube age-restricted / sign-in required videos
- Instagram private posts and stories
- Facebook private videos
- Twitter/X age-restricted tweets
- Reddit NSFW posts

### Analytics Commands (all users)
- `/stats` — Total users, downloads, per-platform breakdown
- `/users` — Total registered users + active today
- `/active` — Active users today, downloads, active right now
- `/downloads` — Download totals split by platform

### Broadcast System (admin only)
```
/broadcast Your message here
```
Or reply to any message/photo/video with `/broadcast`.  
Shows live progress (sent / blocked / failed) while broadcasting.

---

## Deployment — Railway

### 1. Push to GitHub
```bash
git init
git add .
git commit -m "MeraDownload4K v3"
git remote add origin https://github.com/YOUR/REPO.git
git push -u origin main
```

### 2. Deploy on Railway
- Railway → New Project → Deploy from GitHub repo
- Select your repository — `railway.toml` is auto-detected

### 3. Set environment variables
In Railway → Your Service → **Variables**:

| Variable    | Required | Description                                |
|-------------|----------|--------------------------------------------|
| `BOT_TOKEN` | ✅ Yes   | Token from @BotFather on Telegram          |
| `ADMIN_ID`  | Optional | Your numeric Telegram ID (use `/getid`)    |
| `PORT`      | Auto     | Set by Railway — do not set this manually  |

### 4. Verify health check
```
https://YOUR-APP.railway.app/health
```
Expected:
```json
{
  "status": "ok",
  "version": "3.0",
  "bot": "YourBotName",
  "cookies": false,
  "ffmpeg": true,
  "platforms": ["youtube","instagram","facebook","snapchat","reddit","twitter"]
}
```

---

## Optional — Cookie Authentication

To unlock private or age-restricted content across any platform:

1. Install **"Get cookies.txt LOCALLY"** in Chrome
2. Log into the relevant site (YouTube, Instagram, etc.)
3. Export cookies → save as `cookies.txt` next to `bot.py`
4. Add `cookies.txt` to your GitHub repo (make repo **Private** first)
5. Push → Railway auto-redeploys
6. Bot logs: `🍪 cookies: ✅ loaded`

---

## Commands

| Command              | Access    | Description                       |
|----------------------|-----------|-----------------------------------|
| `/start`             | All       | Welcome screen with platform info |
| `/help`              | All       | Usage guide                       |
| `/stats`             | All       | Full statistics                   |
| `/users`             | All       | Total registered users            |
| `/active`            | All       | Active users today                |
| `/downloads`         | All       | Download stats by platform        |
| `/audio <link>`      | All       | Download as MP3 audio             |
| `/getid`             | All       | Show your Telegram user ID        |
| `/admin`             | Admin     | Full admin dashboard              |
| `/broadcast <msg>`   | Admin     | Message all users                 |

---

## HTTP Endpoints

| Endpoint      | Description                        |
|---------------|------------------------------------|
| `GET /`       | Running status                     |
| `GET /health` | Health check (Railway monitors)    |
| `GET /stats`  | JSON stats for external monitoring |

---

## File Structure

```
bot.py              ← Main bot — v3.0, 6 platforms, real progress
requirements.txt    ← Python dependencies
railway.toml        ← Railway config (ffmpeg, healthcheck, auto-restart)
Procfile            ← worker: python bot.py
runtime.txt         ← Python 3.12 pin
.gitignore          ← Excludes cookies.txt and stats.json
README.md           ← This file
cookies.txt         ← OPTIONAL: unlocks private/age-restricted content
stats.json          ← Auto-generated, persists download stats
```
