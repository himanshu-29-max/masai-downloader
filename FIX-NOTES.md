# Masai Downloader — Fix v1.1 (07 Nov 2026)

## Asli problem (repo clone karke, Render jaisa environment bana ke reproduce kiya)

Purana code ffmpeg ko is tarah setup karta tha:

```python
ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
ffmpeg_dir = os.path.dirname(ffmpeg_exe)
os.environ["PATH"] = ffmpeg_dir + ...   # sirf directory PATH me daali
```

Lekin imageio-ffmpeg ki binary ka naam `ffmpeg` hota hi nahi — woh hota hai
`ffmpeg-linux-x86_64-v9.0.2` jaisa kuch. yt-dlp PATH me exact naam `ffmpeg`
dhundhta hai, isliye **Render par (jahan system ffmpeg hota hi nahi) use
ffmpeg kabhi milta hi nahi tha.**

Reproduce kiya hua exact error:

```
ERROR: You have requested merging of multiple formats but ffmpeg is not
installed. Aborting due to --abort-on-error
```

Iska asar:
- **YouTube tab:** Best / har quality ka download video+audio alag-alag
  formats me aata hai, merge ke bina file banti hi nahi → har download fail.
  MP3 conversion bhi ffmpeg se hota hai → woh bhi fail.
- **Universal/M3U8 tab:** Jin streams me video+audio alag formats hain,
  wahan bhi merge fail. Single-format HLS kabhi-kabhi bach jata tha.
- Local Windows par system ffmpeg installed hone se ye bug chhup jata tha —
  isiliye "mere PC par chalta hai, site par nahi" wala scene bana.

Player clients (tv_embedded / tv / android_vr) check kiye — woh theek hain,
unse 144p se 2160p tak saare formats aate hain. Cookies wala hissa bhi
theek tha. Gadbad sirf ffmpeg ki thi.

## Fix (sirf `app.py` badla hai)

1. **Har yt-dlp call me `'ffmpeg_location': FFMPEG_EXE` seedha pass kiya**
   (`get_yt_opts()` me — jo YouTube info/download/playlist sab use karte
   hain — aur universal downloader ke opts me). Ab yt-dlp PATH par nirbhar
   nahi hai.
2. **Belt-and-braces:** startup par binary ko `ffmpeg-bin/ffmpeg` naam se
   symlink (fail ho to copy) karke woh folder PATH me sabse aage rakha,
   taaki post-processors ko bhi `ffmpeg` naam se mil jaye.
3. **Naya `/health` page:** deploy ke baad browser me `your-site.onrender.com/health`
   kholo — dikhayega `ffmpeg_exists: true`, `ffmpeg_on_path`,
   yt-dlp version, aur cookies lagi hain ya nahi (sirf true/false,
   cookie ka content kabhi nahi).
4. **Cookies env-var support wapas:** Render secret file ke alawa ab
   `MASAI_COOKIES` / `YOUTUBE_COOKIES` environment variables me Netscape
   cookie content diya ho to woh bhi chalega (pichhle ek commit me ye
   support galti se hat gaya tha).
5. `.gitignore` me `ffmpeg-bin/` joda (ye folder runtime par khud banta hai).

Baaki sab same hai: `requirements.txt`, `Procfile`, `render-build.sh`,
`templates/index.html` — kuch nahi badla.

## Live test (jo maine karke dekha)

Render jaisa mahol banaya (PATH se system ffmpeg hata diya), phir poori
Flask app chalayi:

- `/health` → `ffmpeg_exists: true`, `ffmpeg_on_path: .../ffmpeg-bin/ffmpeg` ✅
- YouTube "Me at the zoo" → info fetch ✅, Best download → merge hua →
  `GET /get-file` se **522 KB ki asli MP4** mili ✅
- Universal M3U8 sample (JW Platform) → video+audio alag formats aaye →
  **[Merger] chala** → merged MP4, **7.9 MB file download** hui ✅
  (yehi test purane code par "ffmpeg is not installed" par fail hota hai)

**Ek sachchai:** Masai ke asal lecture URL par test nahi ho paya, kyunki
uske liye tumhari Masai cookies chahiye jo mere paas nahi hain (aur honi
bhi nahi chahiye). Masai wala flow code-wise universal wala hi hai, jo
upar pass ho chuka hai. Agar Masai download phir bhi atke, to wajah
cookies hongi — neeche wala check karna.

## Tumhe kya karna hai

1. ZIP extract karo. Sirf **`app.py`** (aur chaaho to `.gitignore`)
   apne project folder me overwrite karo. Baaki files same hain.
2. GitHub par push karo:
   ```
   git add app.py .gitignore
   git commit -m "fix: ffmpeg_location pass karo - Render par merge fail ho raha tha"
   git push
   ```
3. Render auto-deploy ho jayega (2-4 min). Deploy ke baad pehle
   **`/health`** kholo: `ffmpeg_exists: true` aana chahiye.
4. Phir ek chhota YouTube video aur ek Masai lecture try karo.

## Agar phir bhi atke to

Site jo laal error dikhaye, uska screenshot bhejo. Do sambhavna bachegi:
- **"Sign in to confirm you're not a bot"** (YouTube) → Render ki IP par
  bot-check; iske liye `youtube_cookies.txt` secret file / `YOUTUBE_COOKIES`
  env var me fresh logged-in cookies lagani hongi.
- **Masai par 403 / cookie error** → `cookies.txt` secret file ki cookies
  expire ho gayi hain; browser se fresh cookies export karke Render me
  secret file update karo. `/health` me `masai_cookie_present` pehle
  confirm kar lena — `false` aaye to secret file lagi hi nahi hai.
