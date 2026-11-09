import os
import re
import uuid
import shutil
import zipfile
import threading
import sqlite3
import json
import time
from urllib.parse import urlparse
from flask import Flask, render_template, request, send_file, jsonify, after_this_request
import yt_dlp
import imageio_ffmpeg

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, 'downloads')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def _cookie_from_env(env_name, writable_path):
    """Render par secret file lagana kabhi-kabhi fail hota hai; env var me
    Netscape cookie content diya ho to use writable file me likh do."""
    content = os.environ.get(env_name, '').strip()
    if not content or '# Netscape' not in content and '\t' not in content:
        return None
    try:
        with open(writable_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return writable_path
    except Exception:
        return None

# ==================== v1.4: COOKIE FILE KI DHOONDH (loose matching) ====================
# Pichli versions sirf exact jagah '/etc/secrets/youtube_cookies.txt'
# dekhti thi. Filename me zara si bhi galti (capital letter, extra .txt,
# dash ki jagah underscore) ho to file Render par hote hue bhi app ko
# milti hi nahi thi - aur bina cookies ke YouTube Render ki IP ko jawab
# nahi deta. Ab /etc/secrets aur app folder dono me naam ke kareeb-
# kareeb saare variants dhoondhe jate hain.
def _scan_cookie_file(directories, canonical, keywords):
    """Pehle exact canonical naam, phir keywords wala koi bhi .txt.
    Sirf pehla match lautata hai (path), na mile to None."""
    for d in directories:
        if not d or not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except Exception:
            continue
        for n in names:
            if n == canonical:
                return os.path.join(d, n)
        for n in names:
            low = n.lower()
            if low.endswith('.txt') and any(k in low for k in keywords):
                return os.path.join(d, n)
    return None

def _adopt_cookie(found_path, writable_path):
    """Mile hue cookie file ko writable jagah par copy karo (yt-dlp
    cookies ko update karke wapas likhta hai; /etc/secrets read-only
    hota hai). Copy fail ho to mili hui file hi istemal karo."""
    if not found_path:
        return None
    try:
        shutil.copyfile(found_path, writable_path)
        return writable_path
    except Exception:
        return found_path

def _cookie_info(path, check_domain=None):
    """Sirf ginti/naap batata hai - cookie ka content kabhi nahi.
    /health se pata chalta hai: file mili? kitni badi hai? usme sahi
    domain ki cookies hain bhi ya nahi (galat site se export hui file
    me youtube.com ki cookies hi nahi hoti)."""
    info = {'path': path, 'size_bytes': 0, 'lines': 0}
    if check_domain:
        info['has_domain_cookies'] = False
    if not path or not os.path.exists(path):
        return info
    try:
        info['size_bytes'] = os.path.getsize(path)
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            for i, line in enumerate(f):
                if i >= 5000:
                    break
                info['lines'] += 1
                if check_domain and check_domain in line:
                    info['has_domain_cookies'] = True
    except Exception:
        pass
    return info

# Cookie Setup (Masai ke liye)
WRITABLE_COOKIE = '/tmp/cookies.txt'
MASAI_COOKIE = _cookie_from_env('MASAI_COOKIES', WRITABLE_COOKIE)
if MASAI_COOKIE is None:
    _found = _scan_cookie_file(['/etc/secrets', BASE_DIR], 'cookies.txt', ['masai'])
    MASAI_COOKIE = _adopt_cookie(_found, WRITABLE_COOKIE)

# Cookie Setup (YouTube ke liye - bot-check bypass)
WRITABLE_YT_COOKIE = '/tmp/youtube_cookies.txt'
YOUTUBE_COOKIE = _cookie_from_env('YOUTUBE_COOKIES', WRITABLE_YT_COOKIE)
if YOUTUBE_COOKIE is None:
    _found = _scan_cookie_file(['/etc/secrets', BASE_DIR], 'youtube_cookies.txt',
                               ['youtube', 'yt_cookie', 'yt-cookie', 'ytcookies'])
    YOUTUBE_COOKIE = _adopt_cookie(_found, WRITABLE_YT_COOKIE)

# ==================== v1.5: PO TOKEN PROVIDER ====================
# YouTube ab server (datacenter) IP se aayi web request par cookies ke
# saath-saath ek PO (Proof-of-Origin) Token bhi mangta hai - ek tarah ka
# digital stamp jo sabit karta hai ki request asli browser-jaisi hai.
# Bina token ke jawab hamesha ek hi aata hai: "Sign in to confirm you're
# not a bot", chahe cookies ekdum taazi aur sahi hi kyun na hon (Singapore
# service par 08 Nov ko yahi hua - taazi 33-line cookies ke baad bhi).
# Token ek alag chhoti service (bgutil-ytdlp-pot-provider) banati hai;
# uska URL Render par POT_BASE_URL env var me diya jata hai. Na diya ho
# to app pehle ki tarah (bina token ke) chalti rehti hai.
POT_BASE_URL = os.environ.get('POT_BASE_URL', '').strip().rstrip('/')

# ==================== FFmpeg setup (v1.1 FIX - ASLI BUG) ====================
# PURANA CODE SIRF DIRECTORY KO PATH ME DAALTA THA. Lekin imageio-ffmpeg ki
# binary ka naam 'ffmpeg' NAHI hota - woh kuch aisa hota hai:
#   ffmpeg-linux-x86_64-v7.0.2  /  ffmpeg-win-x86_64-v7.0.2.exe
# yt-dlp PATH me exact naam 'ffmpeg' dhundhta hai, isliye Render par (jahan
# system ffmpeg hota hi nahi) use ffmpeg MILTA HI NAHI THA aur har merge
# fail hota tha:
#   "ERROR: You have requested merging of multiple formats but ffmpeg is
#    not installed. Aborting due to --abort-on-error"
# Matlab: YouTube ka har Best/quality download aur MP3 conversion fail,
# aur kuch Masai/HLS downloads bhi. Local Windows par system ffmpeg hone
# se ye bug chhup jata tha.
#
# FIX (do hisson me):
# 1) Har yt-dlp call me 'ffmpeg_location': FFMPEG_EXE seedha pass karo
#    (get_yt_opts + universal opts me added hai) - yt-dlp ko PATH par
#    nirbhar hi nahi rehna padta.
# 2) Belt-and-braces: ek folder me binary ko 'ffmpeg' naam se symlink/copy
#    karke PATH me sabse aage rakho, taaki post-processors ko bhi mil jaye.
FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
ffmpeg_exe = FFMPEG_EXE  # purana naam, koi aur jagah use ho to na toote

FFMPEG_BIN_DIR = os.path.join(BASE_DIR, 'ffmpeg-bin')
try:
    os.makedirs(FFMPEG_BIN_DIR, exist_ok=True)
    _ffmpeg_named = os.path.join(
        FFMPEG_BIN_DIR, 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
    if not os.path.exists(_ffmpeg_named):
        try:
            os.symlink(FFMPEG_EXE, _ffmpeg_named)
        except Exception:
            shutil.copyfile(FFMPEG_EXE, _ffmpeg_named)
    try:
        os.chmod(_ffmpeg_named, 0o755)
    except Exception:
        pass
    os.environ["PATH"] = FFMPEG_BIN_DIR + os.pathsep + os.environ.get("PATH", "")
except Exception:
    # Symlink/copy fail ho jaye (Windows permissions aadi) to bhi
    # ffmpeg_location wala fix akela kaafi hai - app ko crash mat karo.
    pass

# ==================== TASK STORE (v1.2 FIX - "Task not found") ====================
# PURANI GALTI: TASKS = {} ek in-memory dictionary thi. Render par gunicorn
# ke MULTIPLE worker processes chalte hain (dashboard ka Start Command
# Procfile wala --workers 1 use hi nahi karta) aur har worker ki apni alag
# memory hoti hai. Download request jis worker ke paas gayi, task usi ki
# memory me bana; browser ki agli polling request dusre worker ke paas
# gayi -> uske paas task tha hi nahi -> "Task not found". Reproduce karke
# dekha: 4 workers par 48 polls me se 46 baar 404 aaya.
# (Ye SQLite wala fix repo ke ek purane commit 93aec17 me tha bhi, lekin
# baad ke 'fix bugs' commit d62fbf6 ne use galti se hata diya tha.)
#
# FIX: Tasks ab SQLite file (downloads/tasks.db) me rehte hain - saare
# workers ek hi file padhte/likhte hain, aur worker restart ho jaye to
# bhi task gayab nahi hota. Neeche wala class bilkul dict jaisa behave
# karta hai, isliye baaki poore code me TASKS[...] wali ek bhi line
# badalni nahi padi.
DB_PATH = os.path.join(DOWNLOAD_DIR, 'tasks.db')

def _init_task_db():
    try:
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    data TEXT,
                    updated_at REAL
                )
            ''')
    except Exception as e:
        print("Task DB init error:", e)

_init_task_db()

class _TaskStore:
    """Dict-jaisa interface, peeche SQLite. Har operation fresh connection
    kholta hai - threads aur processes dono ke liye safe hai."""

    def __setitem__(self, task_id, value):
        try:
            with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                conn.execute('PRAGMA journal_mode=WAL;')
                conn.execute('''
                    INSERT INTO tasks (task_id, data, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        data=excluded.data, updated_at=excluded.updated_at
                ''', (task_id, json.dumps(value), time.time()))
                # 2 ghante se purane tasks saaf karte raho, file badhti na rahe
                conn.execute('DELETE FROM tasks WHERE updated_at < ?',
                             (time.time() - 7200,))
        except Exception as e:
            print(f"Task store write error ({task_id}):", e)

    def get(self, task_id, default=None):
        try:
            with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                cur = conn.execute(
                    'SELECT data FROM tasks WHERE task_id = ?', (task_id,))
                row = cur.fetchone()
                if row:
                    return json.loads(row[0])
        except Exception as e:
            print(f"Task store read error ({task_id}):", e)
        return default

    def __getitem__(self, task_id):
        value = self.get(task_id)
        if value is None:
            raise KeyError(task_id)
        return value

    def __contains__(self, task_id):
        return self.get(task_id) is not None

TASKS = _TaskStore()

def clean_ansi(text):
    return re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', text)

def fix_m3u8_url(url):
    clean = url.strip()
    if 'masaischool.com' in clean and not clean.endswith('.m3u8'):
        if not clean.endswith('/'):
            clean += '/'
        clean += 'master.m3u8'
    return clean

def extract_video_id(url):
    patterns = [
        r'youtube\.com/live/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'v=([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})'
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None

def get_yt_opts(player_client=None, use_cookies=True):
    """YouTube ke liye yt-dlp options. player_client diya ho to wahi set
    use hota hai, warna default combo. use_cookies=False par cookiefile
    nahi bheja jata (v1.6: kuch koshishein bina login ke bhi hoti hain)."""
    if player_client is None:
        # YouTube's 2025-2026 SABR rollout strips playable formats
        # from the plain 'web' clients unless a PO (Proof-of-Origin)
        # token is supplied - which this app doesn't generate. 'tv',
        # 'tv_embedded' and 'android_vr' are, for now, less affected
        # and often still return real formats without one.
        # 'android_sdkless' stays excluded (yt-dlp #15712, HTTP 403
        # on MP4 formats). This list is a moving target - YouTube
        # changes which clients work every few weeks.
        player_client = ['tv_embedded', 'tv', 'android_vr', 'default', '-android_sdkless']
    opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'nocheckcertificate': True,
        'ffmpeg_location': FFMPEG_EXE,
        'extractor_args': {
            'youtube': {
                'player_client': player_client
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Sec-Fetch-Mode': 'navigate'
        }
    }
    if use_cookies and YOUTUBE_COOKIE and os.path.exists(YOUTUBE_COOKIE):
        opts['cookiefile'] = YOUTUBE_COOKIE
    # v1.5: PO Token provider laga ho to uska pata yt-dlp ke plugin ko do.
    # Bina iske web clients par YouTube "Sign in to confirm you're not a
    # bot" par atka rehta hai - cookies akeli ab kaafi nahi hain.
    if POT_BASE_URL:
        opts['extractor_args']['youtubepot-bgutilhttp'] = {
            'base_url': [POT_BASE_URL]
        }
    return opts

# ==================== v1.7: CLIENT + LOGIN JODI FALLBACK ====================
# Render ki IP par YouTube aksar pehle client set ko jawab hi nahi deta
# ("Failed to extract any player response") jabki dusra set chal jata hai,
# isliye hamesha kai client sets ek ke baad ek try hote hain.
# v1.6 ka saboot (2026-11-09, live test): POT + cookies lagne ke baad bot
# wali deewaar ("Sign in to confirm you're not a bot") to gir gayi, lekin
# LOGIN wale session ke saath HAR client set par formats bina asli URL ke
# aaye - "Requested format is not available". YouTube apne naye SABR
# system me kuch login sessions ke formats rok deta hai. Wahi video bina
# login ke, sirf POT token ke saath, saaf 1080p formats deta hai (local
# pariksha me pakka hua). Isliye ab har client set do roop me try hota
# hai - login ke saath aur bina login ke - taaki ek band rasta dusre ko
# na rok sake. Koi ek jodi chal jaye, wahi kaafi hai.
_WEB_SET = ['default', 'web', 'web_safari', 'mweb']
_TV_SET = ['tv_embedded', 'tv', 'android_vr', 'default', '-android_sdkless']
_MOB_SET = ['visionos', 'android']
_EMB_SET = ['web_embedded', 'tv_embedded']

def _attempt_plan():
    """(clients, use_cookies, label) ki koshish-suchi - sabse zyada
    ummeed wali jodi pehle."""
    has_cookies = bool(YOUTUBE_COOKIE and os.path.exists(YOUTUBE_COOKIE))
    plan = []
    if POT_BASE_URL or has_cookies:
        if has_cookies:
            plan.append((_WEB_SET, True, 'web+login'))
        if POT_BASE_URL:
            plan.append((_WEB_SET, False, 'web+token'))
    if has_cookies:
        # v1.7: teen login-wale roop jo abhi tak try nahi hue the. Ye
        # teeno formats aksar dusre clients se alag raste par dete hain:
        # ios (HLS playlist wala rasta), web_creator (YouTube Studio ka
        # apna client) aur android (login ke saath poori list deta hai).
        plan.append((['ios'], True, 'ios+login'))
        plan.append((['web_creator'], True, 'creator+login'))
        plan.append((['android'], True, 'android+login'))
    plan.append((_TV_SET, False, 'tv'))
    if has_cookies:
        plan.append((_TV_SET, True, 'tv+login'))
    plan.append((_MOB_SET, False, 'visionos'))
    if has_cookies:
        plan.append((_MOB_SET, True, 'visionos+login'))
    if has_cookies:
        plan.append((_EMB_SET, True, 'embedded+login'))
    plan.append((_EMB_SET, False, 'embedded'))
    seen = set()
    out = []
    for clients, use_cookies, label in plan:
        key = (tuple(clients), use_cookies)
        if key in seen:
            continue
        seen.add(key)
        out.append((clients, use_cookies, label))
    return out

# In shabdon wale error par dusra client set try karna faydemand hai.
# Baaki errors (jaise ffmpeg merge fail) par dobara try sirf samay kharab karega.
_FALLBACK_HINTS = ('player response', 'sign in to confirm', 'not a bot',
                   'video unavailable', 'this video is unavailable',
                   'requested format is not available', 'http error 403',
                   'login required', 'private video', 'age')

def _should_fallback(err):
    msg = str(err).lower()
    return any(h in msg for h in _FALLBACK_HINTS)

def extract_with_fallback(url, download=False, extra_opts=None):
    """Har (client set, login hai ya nahi) jodi ek-ek karke try karta hai.
    Safal ho to (info, filename) lautata hai; sab fail ho jayein to har
    koshish ki galti ka saar uthata hai, taaki asli wajah dikhe.
    Download ke case me bhi yahi istemal hota hai - merge jaisi gair-
    extraction galtiyon par fallback nahi hota."""
    attempts = []
    for clients, use_cookies, label in _attempt_plan():
        opts = get_yt_opts(clients, use_cookies=use_cookies)
        if extra_opts:
            opts.update(extra_opts)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=download)
                filename = ydl.prepare_filename(info) if download else None
                return info, filename
        except Exception as e:
            attempts.append(f'{label}: {clean_ansi(str(e))[:140]}')
            if download and not _should_fallback(e):
                raise
            continue
    if attempts:
        err = Exception('Sab koshishein fail: ' + ' || '.join(attempts))
        # Har koshish ki galti alag se bhi rakho - task status me bina
        # kate dikhegi, taaki agli jaanch me koi koshish andekhi na rahe.
        err.attempts = attempts
        raise err
    raise Exception('YouTube se koi jawab nahi aaya.')

def friendly_error(e):
    """yt-dlp ki kachchi English error ko aisi bhasha me badlo jisse asli
    wajah aur ilaaj turant samajh aaye. Asli error aakhir me rehta hai."""
    raw = clean_ansi(str(e))
    low = raw.lower()
    if 'requested format is not available' in low:
        return ('YouTube ne video tak pahunch to de di, lekin download wale formats rok diye — '
                'ye uske naye SABR system ki wajah se hota hai: kuch login sessions par formats '
                'band ho jate hain. App ne login ke saath aur bina login ke kai clients try kiye '
                'hain. Phir bhi ye aaye to: (1) token service ka /ping kholkar dekho ki woh chal '
                'rahi hai ya nahi, (2) thodi der baad dobara try karo — YouTube ki ye rok aksar '
                'kuch ghanton me khud hat jati hai. '
                f'(Asli error: {raw[:1800]})')
    if 'failed to extract any player response' in low or \
       'sign in to confirm' in low or 'not a bot' in low:
        if POT_BASE_URL:
            ilaaj = ('Token service (POT) lagi hui hai, phir bhi YouTube ne rok diya — '
                     'ho sakta hai token service so rahi ho ya band ho (uska /ping '
                     'kholkar dekho), ya cookies expire ho gayi hon (taazi cookies '
                     'lagao). Thodi der baad dobara try karna bhi kaam kar sakta hai.')
        else:
            ilaaj = ('Ilaaj: Render me apne YouTube login ki cookies lagao '
                     '(Secret File: youtube_cookies.txt) aur PO Token service '
                     '(POT_BASE_URL) jodo — aaj ke YouTube par server se download '
                     'ke liye dono chahiye.')
        return ('YouTube ne is server (Render) ki request ko bot samajh kar rok diya. '
                'Ye aksar server ki IP ki wajah se hota hai. '
                f'{ilaaj} '
                f'(Asli error: {raw[:200]})')
    if 'this video is unavailable' in low or 'video unavailable' in low or \
       'private video' in low or 'login required' in low:
        return ('Ye video YouTube par is samay uplabdh nahi hai — woh private ho sakta hai, '
                'delete ho sakta hai, ya sirf kisi khaas account ke liye khulta ho. '
                'Agar video tumhare apne YouTube account se browser me chalta hai, to usi '
                'account ki cookies Render me lagane se hi download hoga '
                '(Secret File: youtube_cookies.txt). '
                f'(Asli error: {raw[:200]})')
    if 'http error 403' in low or '403 forbidden' in low:
        return ('Server ko video tak pahunchne se rok diya gaya (403). Cookies expire '
                'ho gayi hongi ya lagi hi nahi hain — fresh cookies lagakar dobara try karo. '
                f'(Asli error: {raw[:200]})')
    return raw

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/health')
def health():
    """Deploy ke baad browser me /health kholo - bina logs khode pata chal
    jayega ki ffmpeg mil raha hai ya nahi aur cookies lagi hain ya nahi.
    Sirf True/False batata hai, cookie ka content kabhi nahi."""
    import shutil as _sh
    # Secret Files ke sirf NAAM (content kabhi nahi) - isse ek nazar me
    # pata chal jata hai ki file Render par kis naam se lagi hai.
    try:
        secret_files = sorted(os.listdir('/etc/secrets'))
    except Exception:
        secret_files = []
    yt_info = _cookie_info(YOUTUBE_COOKIE, check_domain='youtube.com')
    return jsonify({
        'ok': True,
        'build': 'v1.7',
        'pot_configured': bool(POT_BASE_URL),
        'ffmpeg_exe': FFMPEG_EXE,
        'ffmpeg_exists': os.path.exists(FFMPEG_EXE),
        'ffmpeg_on_path': _sh.which('ffmpeg'),
        'yt_dlp_version': yt_dlp.version.__version__,
        'masai_cookie_present': bool(MASAI_COOKIE and os.path.exists(MASAI_COOKIE)),
        'masai_cookie_size_bytes': _cookie_info(MASAI_COOKIE)['size_bytes'],
        'youtube_cookie_present': bool(YOUTUBE_COOKIE and os.path.exists(YOUTUBE_COOKIE)),
        'youtube_cookie_size_bytes': yt_info['size_bytes'],
        'youtube_cookie_lines': yt_info['lines'],
        'youtube_cookie_has_yt_cookies': yt_info.get('has_domain_cookies'),
        'secret_files': secret_files,
        'task_store': 'sqlite',
        'task_db_exists': os.path.exists(DB_PATH),
    })

# ==================== UNIVERSAL / M3U8 DOWNLOAD ====================
def run_universal_download(task_id, video_url):
    try:
        parsed_url = urlparse(video_url)
        domain = parsed_url.netloc.lower()

        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': os.path.join(DOWNLOAD_DIR, f"{task_id}_%(title).100s.%(ext)s"),
            'merge_output_format': 'mp4',
            'ffmpeg_location': FFMPEG_EXE,
            'socket_timeout': 30,
            'retries': 15,
            'fragment_retries': 15,
            'quiet': False
        }

        if 'masaischool.com' in domain:
            if MASAI_COOKIE and os.path.exists(MASAI_COOKIE):
                ydl_opts['cookiefile'] = MASAI_COOKIE
            ydl_opts['http_headers'] = {
                'Referer': 'https://students.masaischool.com/',
                'Origin': 'https://students.masaischool.com'
            }
        else:
            ydl_opts['http_headers'] = {
                'Referer': f"{parsed_url.scheme}://{parsed_url.netloc}/"
            }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            filename = ydl.prepare_filename(info)
            final_file = os.path.splitext(filename)[0] + '.mp4'
            if not os.path.exists(final_file) and os.path.exists(filename):
                final_file = filename

            if os.path.exists(final_file):
                TASKS[task_id] = {'status': 'done', 'filename': os.path.basename(final_file)}
            else:
                TASKS[task_id] = {'status': 'error', 'error': 'Video merge process failed.'}
    except Exception as e:
        err_text = clean_ansi(str(e))
        # Masai ke streams par 403 ka matlab lagbhag hamesha: cookies.txt
        # (Render Secret File) expire ho gayi ya lagi hi nahi hai.
        if 'masaischool.com' in video_url and ('403' in err_text or 'Forbidden' in err_text):
            err_text = ('Masai ne download rok diya (403) — lagbhag pakka hai ki Render me '
                        'lagi cookies.txt expire ho gayi hai ya lagi hi nahi hai. Fresh '
                        'cookies lagao: apne browser se students.masaischool.com ki cookies '
                        'export karke Render ki Secret File (cookies.txt) update karo. '
                        f'(Asli error: {err_text[:200]})')
        TASKS[task_id] = {'status': 'error', 'error': err_text}

@app.route('/download-universal', methods=['POST'])
def download_universal():
    data = request.get_json()
    raw_url = data.get('url', '').strip()

    if not raw_url:
        return jsonify({'error': 'URL provide karein!'}), 400

    video_url = fix_m3u8_url(raw_url)
    task_id = str(uuid.uuid4())[:8]
    TASKS[task_id] = {'status': 'processing'}

    t = threading.Thread(target=run_universal_download, args=(task_id, video_url))
    t.daemon = True
    t.start()

    return jsonify({'status': 'started', 'task_id': task_id})

@app.route('/task-status/<task_id>')
def task_status(task_id):
    task = TASKS.get(task_id)
    if not task:
        return jsonify({'status': 'error', 'error': 'Task not found'}), 404
    return jsonify(task)

# ==================== YOUTUBE ROUTES ====================
# Pulling full format info involves YouTube's bot-check + cookies + JS
# challenge solving, which can take longer than gunicorn's/Render's request
# timeout. Run it in the background like the download routes so a slow
# lookup doesn't come back as a dead connection (which shows up in the
# browser as a JSON-parse failure, not a real error).
def run_fetch_youtube_info(task_id, raw_url):
    try:
        if 'playlist?list=' in raw_url:
            info, _ = extract_with_fallback(raw_url, download=False,
                                            extra_opts={'extract_flat': True})
            TASKS[task_id] = {
                'status': 'done',
                'is_playlist': True,
                'title': info.get('title', 'YouTube Playlist'),
                'count': len(list(info.get('entries', [])))
            }
            return

        vid = extract_video_id(raw_url)
        if not vid:
            TASKS[task_id] = {'status': 'error', 'error': 'Invalid YouTube URL!'}
            return

        clean_url = f"https://www.youtube.com/watch?v={vid}"
        full_info, _ = extract_with_fallback(clean_url, download=False)
        available_heights = set()
        for f in full_info.get('formats', []):
            h = f.get('height')
            if h and f.get('vcodec') != 'none':
                available_heights.add(h)

        sorted_heights = sorted(list(available_heights), reverse=True)

        TASKS[task_id] = {
            'status': 'done',
            'is_playlist': False,
            'title': full_info.get('title', 'YouTube Video'),
            'thumbnail': full_info.get('thumbnail', f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'),
            'resolutions': sorted_heights
        }
    except Exception as e:
        TASKS[task_id] = {'status': 'error', 'error': friendly_error(e),
                          'attempts': getattr(e, 'attempts', None)}

@app.route('/fetch-youtube-info', methods=['POST'])
def fetch_youtube_info():
    data = request.get_json()
    raw_url = data.get('url', '').strip()

    if not raw_url:
        return jsonify({'error': 'URL provide karein!'}), 400

    task_id = str(uuid.uuid4())[:8]
    TASKS[task_id] = {'status': 'processing'}

    t = threading.Thread(target=run_fetch_youtube_info, args=(task_id, raw_url))
    t.daemon = True
    t.start()

    return jsonify({'status': 'started', 'task_id': task_id})

# Runs on a background thread so a slow/long video doesn't hit Render's
# ~100s request timeout - the same fix already applied to the universal
# downloader, now applied here too since YouTube downloads were still
# synchronous and would time out on anything but very short videos.
def run_youtube_download(task_id, raw_url, quality, is_playlist):
    playlist_dir = None
    try:
        if not is_playlist:
            vid = extract_video_id(raw_url)
            video_url = f"https://www.youtube.com/watch?v={vid}" if vid else raw_url
        else:
            video_url = raw_url

        # Quality ke hisaab se format/postprocessor - client set se alag
        # rakha hai taaki fallback me har set ke saath yahi istemal ho.
        fmt_opts = {'retries': 10, 'quiet': False}
        if quality == 'mp3':
            fmt_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
            target_ext = 'mp3'
        elif quality == 'best':
            fmt_opts.update({
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'merge_output_format': 'mp4'
            })
            target_ext = 'mp4'
        else:
            fmt_opts.update({
                'format': f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best',
                'merge_output_format': 'mp4'
            })
            target_ext = 'mp4'

        if is_playlist:
            info_flat, _ = extract_with_fallback(video_url, download=False,
                                                 extra_opts={'extract_flat': True})
            playlist_title = re.sub(r'[\\/*?:"<>|]', "", info_flat.get('title', 'YouTube_Playlist'))

            # Prefix with task_id so two people downloading playlists with
            # the same title at the same time don't collide on disk.
            playlist_dir = os.path.join(DOWNLOAD_DIR, f"{task_id}_{playlist_title}")
            os.makedirs(playlist_dir, exist_ok=True)
            fmt_opts['outtmpl'] = os.path.join(playlist_dir, '%(autonumber)02d - %(title)s.%(ext)s')

            # Har client set ek baar try karo; aadha-adhura download agle
            # set se pehle saaf kar do taaki ZIP me kachra na jaye.
            last_err = None
            downloaded = False
            for clients, use_cookies, _label in _attempt_plan():
                dl_opts = get_yt_opts(clients, use_cookies=use_cookies)
                dl_opts.update(fmt_opts)
                try:
                    with yt_dlp.YoutubeDL(dl_opts) as ydl:
                        ydl.download([video_url])
                    downloaded = True
                    break
                except Exception as e:
                    last_err = e
                    if not _should_fallback(e):
                        raise
                    for f in os.listdir(playlist_dir):
                        try:
                            os.remove(os.path.join(playlist_dir, f))
                        except Exception:
                            pass
            if not downloaded:
                raise last_err if last_err else Exception('Playlist download fail ho gaya.')

            zip_filename = f"{task_id}_{playlist_title}.zip"
            zip_path = os.path.join(DOWNLOAD_DIR, zip_filename)
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(playlist_dir):
                    for file in files:
                        zipf.write(os.path.join(root, file), file)

            TASKS[task_id] = {'status': 'done', 'filename': zip_filename}

        else:
            fmt_opts['outtmpl'] = os.path.join(DOWNLOAD_DIR, f"{task_id}_%(title)s.%(ext)s")
            fmt_opts['noplaylist'] = True

            info, filename = extract_with_fallback(video_url, download=True,
                                                   extra_opts=fmt_opts)
            final_file = os.path.splitext(filename)[0] + f'.{target_ext}'

            if not os.path.exists(final_file) and os.path.exists(filename):
                final_file = filename

            if os.path.exists(final_file):
                TASKS[task_id] = {'status': 'done', 'filename': os.path.basename(final_file)}
            else:
                TASKS[task_id] = {'status': 'error', 'error': 'Video merge process failed.'}
    except Exception as e:
        TASKS[task_id] = {'status': 'error', 'error': friendly_error(e),
                          'attempts': getattr(e, 'attempts', None)}
    finally:
        # Always clean up the per-playlist working folder, even if the
        # download failed partway through - previously a failed playlist
        # download left partial files behind on disk forever.
        if playlist_dir and os.path.exists(playlist_dir):
            shutil.rmtree(playlist_dir, ignore_errors=True)

@app.route('/download-youtube', methods=['POST'])
def download_youtube():
    data = request.get_json()
    raw_url = data.get('url', '').strip()
    quality = data.get('quality', 'best')
    is_playlist = data.get('is_playlist', False)

    if not raw_url:
        return jsonify({'error': 'URL provide karein!'}), 400

    task_id = str(uuid.uuid4())[:8]
    TASKS[task_id] = {'status': 'processing'}

    t = threading.Thread(target=run_youtube_download, args=(task_id, raw_url, quality, is_playlist))
    t.daemon = True
    t.start()

    return jsonify({'status': 'started', 'task_id': task_id})

@app.route('/get-file/<path:filename>')
def get_file(filename):
    # Strip any directory components so a filename like "../../etc/passwd"
    # can't be used to escape DOWNLOAD_DIR (path traversal).
    safe_name = os.path.basename(filename)
    file_path = os.path.realpath(os.path.join(DOWNLOAD_DIR, safe_name))
    if not file_path.startswith(os.path.realpath(DOWNLOAD_DIR) + os.sep):
        return "Invalid filename", 400
    if os.path.exists(file_path):
        @after_this_request
        def remove_file(response):
            try:
                os.remove(file_path)
            except Exception:
                pass
            return response
        return send_file(file_path, as_attachment=True)
    return "File not found", 404

if __name__ == '__main__':
    # Never run with the Werkzeug debugger enabled in production - it lets
    # anyone who can reach an error page execute arbitrary code. Only turn
    # it on locally by explicitly setting FLASK_DEBUG=1.
    debug_mode = os.environ.get('FLASK_DEBUG') == '1'
    app.run(debug=debug_mode, port=int(os.environ.get('PORT', 5000)), threaded=True)