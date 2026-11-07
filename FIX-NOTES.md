# Masai Downloader — Fix v1.3 (07 Nov 2026)
# v1.1 (ffmpeg) + v1.2 (Task not found) + v1.3 (YouTube extraction) — teeno ek saath.
# Purana koi bhi ZIP lagaya ho ya na lagaya ho — seedha yahi lagao, baaki chhodo.

====================================================================
BUG 3 (v1.3, naya): "Failed to extract any player response"
====================================================================

## Pehle khushi ki baat

Tumhare naye screenshot me **"Task not found" gayab hai** — matlab
v1.2 ka SQLite fix site par lag gaya aur kaam kar raha hai. Ab site
YouTube tak pahunchti hai aur YouTube ka **asli jawab** dikhati hai.
Teen bugs me se do khatm; ye teesra aur aakhri wala hai.

## Ye error kya hai

```
ERROR: [youtube] lZx_xitlhyI: Failed to extract any player response
```

Matlab: YouTube ne Render ke server ko video ki jaankari hi nahi di.
Do badi wajahen hoti hain:

1. **Server IP par bot-rukawat** — YouTube datacenter (Render) ki IP ko
   bina login ke aksar jawab nahi deta. Ilaaj: tumhare YouTube account
   ki cookies server par lagana.
2. **Video khud uplabdh nahi** — private / delete / sirf khaas account
   ke liye. Is case me bhi, agar video tumhare account se khulta hai,
   to ilaaj wahi cookies hain; agar kahin nahi khulta, to koi bhi
   downloader use nahi la sakta.

**Meri jaanch (jo maine khud karke dekhi):**
- Screenshot wale video ko maine **9 alag-alag client tarike se** try
  kiya — har baar "This video is unavailable" ya koi jawab nahi.
  YouTube ki oEmbed jaanch me bhi ye video **404 (nahi mila)** aaya.
- Unhi tariko se **dusre aam videos bilkul sahi chale** — ek mashhoor
  video ki saari qualities (144p se 2160p tak) aayi aur download bhi
  pura hua.
- Matlab: app ka kaam ab sahi hai; ye khaas video bina account-access
  ke kahin se nahi khul raha.

## v1.3 me kya sudhara

1. **Client fallback:** ab ek tarika fail ho to app apne-aap dusra,
   phir teesra tarika try karti hai (pehle ek hi tarika tha).
2. **Cookies ho to pehle web clients:** cookies lagi hon to extraction
   pehle un clients se hoti hai jinke saath cookies sabse achhi chalti
   hain — private/account-only videos ke liye yahi sahi rasta hai.
3. **Saaf bhasha me error:** ab kachchi English ki jagah site seedha
   wajah aur ilaaj batayegi, jaise — "Ye video uplabdh nahi hai —
   private/delete ho sakta hai… cookies lagane se hoga" ya "YouTube ne
   server ko bot samjha — cookies lagao". Masai wale 403 par bhi ab
   "cookies expire ho gayi hain" wala saaf sandesh aayega.

## Live test (v1.3, 4 workers ke saath)

- Mashhoor public video → qualities aayi (2160p tak), **9 second me** ✅
- Chhota video download → merge → **asli MP4 mili** ✅
- Screenshot wala video → koi Task-not-found nahi; saaf Hinglish me
  "video uplabdh nahi — private/delete/account-only" wala jawab ✅

====================================================================
TUMHE KYA KARNA HAI
====================================================================

### Step 1: v1.3 lagao
ZIP se **`app.py`** overwrite karo (is baar sirf app.py badla hai;
index.html aur Procfile v1.2 wale hi hain — agar v1.2 nahi lagaya tha
to teeno overwrite karna). Push → Render deploy.

### Step 2: Ye video apne browser me kholo (sabse zaroori jaanch)
Link: https://youtu.be/lZx_xitlhyI
- **Browser me bhi nahi chalta** → video gaya (private/delete).
  Koi fix kaam nahi karega; link galat ya video hata diya gaya hai.
- **Sirf login hone par chalta hai** → Step 3 (cookies) hi ilaaj hai.
- **Bina login (incognito) bhi chalta hai** → video public hai; phir
  rukawat sirf Render ki IP ki hai — ilaaj phir bhi Step 3 hi hai.

### Step 3: YouTube cookies Render me lagao (bot-rukawat ka ilaaj)
1. Chrome me woh wala YouTube account kholo jisse video chalta hai.
2. "Get cookies.txt LOCALLY" extension se youtube.com ki cookies
   **Netscape format** me export karo → file ka naam
   `youtube_cookies.txt` rakho.
   (Dhyaan: ye file tumhare account ka login hai — kisi ko bhejna mat,
   GitHub par to bilkul push mat karna; ye repo ki .gitignore me pehle
   se band hai.)
3. Render Dashboard → tumhari service → **Environment → Secret Files**
   → Add: Filename = `youtube_cookies.txt`, content me puri file ka
   text paste karo → Save (service restart hogi).
4. `/health` kholo → `youtube_cookie_present: true` aana chahiye.
5. Site par video dobara try karo.

### Step 4: Ek aur setting pakki kar lo
Render → Settings:
- **Build Command** = `./render-build.sh`
  (yeh har deploy par yt-dlp ko sabse naye version par le jata hai —
  purana yt-dlp bhi "player response" wali galti deta hai. `/health`
  me `yt_dlp_version` dikhta hai, wahi se pakka hota hai.)
- **Start Command** =
  `gunicorn app:app --timeout 120 --workers 1 --threads 8 --worker-class gthread`

====================================================================
BUG 2 (v1.2): "Task not found" — HAL HO GAYA (site par lag chuka)
====================================================================
Tasks memory (`TASKS = {}`) me the; Render ke alag-alag workers ko
task dikhta hi nahi tha. Maine reproduce kiya tha: 4 workers par 48 me
se 46 polls 404. Fix: tasks SQLite file (`downloads/tasks.db`) me —
saare workers ke liye ek hi jagah. Live test: 48/48 pass. Tumhare naye
screenshot me ye error gayab hai, yahi iski sabse badi pushti hai.

====================================================================
BUG 1 (v1.1): Render par ffmpeg nahi mil raha tha — HAL
====================================================================
imageio-ffmpeg ki binary ka naam `ffmpeg` nahi hota
(`ffmpeg-linux-x86_64-v9.0.2` jaisa hota hai), isliye yt-dlp ko Render
par ffmpeg milta hi nahi tha aur har merge "ffmpeg is not installed"
par fail tha. Fix: har yt-dlp call me `ffmpeg_location` seedha pass +
binary ko `ffmpeg` naam se link karke PATH me. Saath me `/health`
page aur env-var se cookies dene ka support.
