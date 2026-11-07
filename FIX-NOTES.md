# Masai Downloader — Fix v1.2 (07 Nov 2026)
# v1.1 (ffmpeg) + v1.2 (Task not found) — dono fix ek saath.
# Agar v1.1 wala ZIP laga liya tha to koi baat nahi, isse overwrite kar do.
# Agar nahi lagaya tha to v1.1 ko chhodo, seedha yahi lagao.

====================================================================
BUG 2 (v1.2, naya): "Task not found" — tumhare screenshot wala error
====================================================================

## Hua kya tha

"Check Available Qualities" dabate hi **Task not found** aa raha tha —
video tak baat pahunch hi nahi rahi thi.

Code me tasks ek in-memory dictionary me rehte the:

```python
TASKS = {}
```

Lekin Render par gunicorn ke **multiple worker processes** chalte hain
(dashboard me jo Start Command pada hai, wohi chalta hai — Procfile wala
`--workers 1` Render use hi nahi karta). Har worker ki apni alag memory
hoti hai:

- Download/fetch request **Worker 1** ke paas gayi → task usi ki memory me bana
- Browser ki polling request **Worker 2** ke paas gayi → uske paas woh
  task tha hi nahi → **404 Task not found** → site ne wahi dikha diya

**Maine reproduce karke dekha:** purane code ko 4 workers ke saath chalaya
to 48 polls me se **46 baar "Task not found"** aaya. Matlab site lagbhag
har baar isi par atak rahi thi — video download hona to door ki baat thi.

Ek aur khaas baat: ye SQLite wala fix tumhare repo ke purane commit
`93aec17` me **tha bhi**, lekin baad ke `fix bugs` commit (`d62fbf6`) ne
use galti se hata diya. v1.2 me wohi fix wapas + behtar karke lagaya hai.

## Fix

1. **Tasks ab SQLite file (`downloads/tasks.db`) me rehte hain** — saare
   workers ek hi file se padhte/likhte hain, aur koi worker restart ho
   jaye to bhi task gayab nahi hota. Code me ek chhota sa dict-jaisa
   class lagaya hai, isliye baaki `TASKS[...]` wali koi line badli nahi.
2. **Frontend me bhi suraksha:** polling ke pehle kuch polls me agar 404
   aaye to site turant error dikhane ke bajaye thoda aur intezaar karegi.
3. **Procfile** me `--worker-class gthread` joda, taaki `--threads 8`
   asal me kaam kare.
4. `/health` me ab `task_store: "sqlite"` aur `task_db_exists` bhi
   dikhega — deploy ke baad wahi se confirm kar lena.

## Live test (4 workers ke saath — wahi haalaat jisme purana code fail tha)

- Wahi polling test jo pehle 46/48 baar fail tha → ab **48/48 pass, 0 baar
  Task not found** ✅
- YouTube video download (Best) → merge hua → **asli MP4 file mili** ✅
- Universal M3U8 download → merge hua → **7.9 MB MP4 mili** ✅
- **Tumhare screenshot wala link** (`youtu.be/lZx_xitlhyI`) bhi test kiya:
  Task not found gaya, task pura hua — lekin is link par YouTube ne khud
  jawab diya **"This video is unavailable"**. Ye ek baar apne browser me
  kholke dekho: agar wahan bhi video nahi chalta to video private/delete
  ho chuka hai (app ki galti nahi). Agar browser me chalta hai aur site
  par ye error aaye, to mujhe batao — phir woh cookies/IP wala masla
  hoga, uska ilaaj alag hai.

====================================================================
BUG 1 (v1.1): Render par ffmpeg mil hi nahi raha tha
====================================================================

Purana code sirf imageio-ffmpeg ka folder PATH me daalta tha, lekin us
binary ka naam `ffmpeg` hota hi nahi — woh `ffmpeg-linux-x86_64-v9.0.2`
jaisa hota hai. yt-dlp exact naam `ffmpeg` dhundhta hai, isliye Render
par har merge is error par fail tha:

```
ERROR: You have requested merging of multiple formats but ffmpeg is
not installed. Aborting due to --abort-on-error
```

Fix: har yt-dlp call me `'ffmpeg_location'` seedha pass kiya +
binary ko `ffmpeg` naam se link karke PATH me rakha. Isi wajah se
YouTube ka har quality download aur MP3 fail ho raha tha. Local PC par
system ffmpeg hone se ye bug chhup jata tha.

Saath me: `/health` page (ffmpeg/cookies ki sthiti bina logs khode) aur
`MASAI_COOKIES` / `YOUTUBE_COOKIES` env-var se cookies dene ka support
bhi wapas hai.

====================================================================
TUMHE KYA KARNA HAI
====================================================================

1. ZIP extract karo. Teen files apne project me **overwrite** karo:
   **`app.py`**, **`templates/index.html`**, **`Procfile`**
   (baaki files — requirements.txt, render-build.sh — same hain.)
2. Push karo:
   ```
   git add app.py templates/index.html Procfile
   git commit -m "fix: SQLite task store (Task not found) + ffmpeg_location"
   git push
   ```
3. Render deploy (2-4 min) ke baad pehle **`/health`** kholo:
   `task_store: "sqlite"`, `task_db_exists: true`,
   `ffmpeg_exists: true` — teeno aane chahiye.
4. Phir wahi YouTube link dobara try karo. "Task not found" ab nahi
   aana chahiye.

## Render par ek cheez aur check kar lena (1 minute)

Render Dashboard → tumhari service → **Settings → Start Command**.
Agar wahan kuch aur hai to use isse badal do:

```
gunicorn app:app --timeout 120 --workers 1 --threads 8 --worker-class gthread
```

(SQLite fix ke baad ye step anivaarya nahi hai — tasks ab shared file
me hain, workers kitne bhi hon chalega. Lekin 1 worker + zyada threads
is app ke liye sahi rehta hai, kyunki download hui files bhi ek hi
machine par rehti hain.)
