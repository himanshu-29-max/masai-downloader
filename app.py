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

# User's actual Downloads folder subfolder for desktop app mode
USER_DOWNLOADS_DIR = os.path.join(os.path.expanduser('~'), 'Downloads', 'Masai Downloader')
try:
    os.makedirs(USER_DOWNLOADS_DIR, exist_ok=True)
except Exception:
    pass

# ==================== COOKIE SETUP & LOOSE MATCHING ====================
def _cookie_from_env(env_name, writable_path):
    """Env var me Netscape cookie content diya ho to use file me likh do."""
    content = os.environ.get(env_name, '').strip()
    if not content or ('# Netscape' not in content and '\t' not in content):
        return None
    try:
        with open(writable_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return writable_path
    except Exception:
        return None

def _scan_cookie_file(directories, canonical, keywords):
    """Pehle exact canonical naam, phir keywords wala koi bhi .txt."""
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
    """Cookie file ko writable jagah par copy karo taaki read-only issues na aayein."""
    if not found_path:
        return None
    try:
        shutil.copyfile(found_path, writable_path)
        return writable_path
    except Exception:
        return found_path

def _cookie_info(path, check_domain=None):
    """Cookie metadata (size & line count) batata hai - sensitive data kabhi nahi."""
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

# Cookie Setup (Masai School)
WRITABLE_COOKIE = os.path.join(DOWNLOAD_DIR, 'masai_cookies.txt')
MASAI_COOKIE = _cookie_from_env('MASAI_COOKIES', WRITABLE_COOKIE)
if MASAI_COOKIE is None:
    _found_masai = _scan_cookie_file(['/etc/secrets', BASE_DIR], 'cookies.txt', ['masai', 'cookies'])
    MASAI_COOKIE = _adopt_cookie(_found_masai, WRITABLE_COOKIE) if _found_masai else None

# Cookie Setup (YouTube - optional login/bypass)
WRITABLE_YT_COOKIE = os.path.join(DOWNLOAD_DIR, 'yt_cookies.txt')
YOUTUBE_COOKIE = _cookie_from_env('YOUTUBE_COOKIES', WRITABLE_YT_COOKIE)
if YOUTUBE_COOKIE is None:
    _found_yt = _scan_cookie_file(['/etc/secrets', BASE_DIR], 'youtube_cookies.txt',
                                  ['youtube', 'yt_cookie', 'yt-cookie', 'ytcookies'])
    YOUTUBE_COOKIE = _adopt_cookie(_found_yt, WRITABLE_YT_COOKIE) if _found_yt else None

# Cookie Setup (Social - Instagram, Facebook, etc.)
WRITABLE_SOCIAL_COOKIE = os.path.join(DOWNLOAD_DIR, 'social_cookies.txt')
SOCIAL_COOKIE = _cookie_from_env('SOCIAL_COOKIES', WRITABLE_SOCIAL_COOKIE)
if SOCIAL_COOKIE is None:
    _found_social = _scan_cookie_file(['/etc/secrets', BASE_DIR], 'social-cookies.txt',
                                      ['social', 'insta', 'facebook', 'fb'])
    SOCIAL_COOKIE = _adopt_cookie(_found_social, WRITABLE_SOCIAL_COOKIE) if _found_social else None

# Environment configuration
POT_BASE_URL = os.environ.get('POT_BASE_URL', '').strip().rstrip('/')
PROXY_URL = os.environ.get('PROXY_URL', '').strip()

# ==================== FFMPEG EMBEDDED SETUP ====================
FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
ffmpeg_exe = FFMPEG_EXE

FFMPEG_BIN_DIR = os.path.join(BASE_DIR, 'ffmpeg-bin')
try:
    os.makedirs(FFMPEG_BIN_DIR, exist_ok=True)
    _target_name = 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'
    _ffmpeg_named = os.path.join(FFMPEG_BIN_DIR, _target_name)
    if not os.path.exists(_ffmpeg_named) and os.path.exists(FFMPEG_EXE):
        try:
            shutil.copyfile(FFMPEG_EXE, _ffmpeg_named)
        except Exception:
            pass
    try:
        os.chmod(_ffmpeg_named, 0o755)
    except Exception:
        pass
    os.environ["PATH"] = FFMPEG_BIN_DIR + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

# ==================== SQLITE TASK STORE ====================
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
    """SQLite-backed Task Store with dict-like interface."""

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
                # 2 ghante se purane completed tasks delete karein
                conn.execute('DELETE FROM tasks WHERE updated_at < ?',
                             (time.time() - 7200,))
        except Exception as e:
            print(f"Task store write error ({task_id}):", e)

    def get(self, task_id, default=None):
        try:
            with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                cur = conn.execute('SELECT data FROM tasks WHERE task_id = ?', (task_id,))
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
    return re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', str(text))

def _save_to_user_downloads(src_file):
    """Copies the finished file to ~/Downloads/Masai Downloader without prompting."""
    try:
        os.makedirs(USER_DOWNLOADS_DIR, exist_ok=True)
        basename = os.path.basename(src_file)
        dest_file = os.path.join(USER_DOWNLOADS_DIR, basename)
        shutil.copy2(src_file, dest_file)
        return dest_file
    except Exception as e:
        print(f"Error copying to USER_DOWNLOADS_DIR: {e}")
        return src_file

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
    """Constructs yt-dlp options for YouTube extraction."""
    if player_client is None:
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

    if POT_BASE_URL:
        opts['extractor_args']['youtubepot-bgutilhttp'] = {
            'base_url': [POT_BASE_URL]
        }

    if PROXY_URL:
        opts['proxy'] = PROXY_URL

    return opts

# ==================== YOUTUBE CLIENT ATTEMPT PLAN ====================
_WEB_SET = ['default', 'web', 'web_safari', 'mweb']
_TV_SET = ['tv_embedded', 'tv', 'android_vr', 'default', '-android_sdkless']
_MOB_SET = ['visionos', 'android']
_EMB_SET = ['web_embedded', 'tv_embedded']

def _attempt_plan():
    has_cookies = bool(YOUTUBE_COOKIE and os.path.exists(YOUTUBE_COOKIE))
    plan = []

    # 1) web+login
    if has_cookies:
        plan.append((_WEB_SET, True, 'web+login'))
    # 2) web+token-only
    if POT_BASE_URL:
        plan.append((_WEB_SET, False, 'web+token-only'))
    # 3) ios+login
    if has_cookies:
        plan.append((['ios'], True, 'ios+login'))
    # 4) web_creator+login
    if has_cookies:
        plan.append((['web_creator'], True, 'web_creator+login'))
    # 5) android+login
    if has_cookies:
        plan.append((['android'], True, 'android+login'))
    # 6) tv (no cookies)
    plan.append((_TV_SET, False, 'tv (no cookies)'))
    # 7) tv+login
    if has_cookies:
        plan.append((_TV_SET, True, 'tv+login'))
    # 8) visionos
    plan.append((['visionos'], False, 'visionos'))
    # 9) visionos+login
    if has_cookies:
        plan.append((['visionos'], True, 'visionos+login'))
    # 10) embedded+login
    if has_cookies:
        plan.append((_EMB_SET, True, 'embedded+login'))
    # 11) embedded
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

_FALLBACK_HINTS = ('player response', 'sign in to confirm', 'not a bot',
                   'video unavailable', 'this video is unavailable',
                   'requested format is not available', 'http error 403',
                   'login required', 'private video', 'age')

def _should_fallback(err):
    msg = str(err).lower()
    return any(h in msg for h in _FALLBACK_HINTS)

def extract_with_fallback(url, download=False, extra_opts=None):
    """Executes YouTube download or extraction trying each attempt in order."""
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
        err = Exception('Sabhi koshishein fail ho gayi: ' + ' || '.join(attempts))
        err.attempts = attempts
        raise err
    raise Exception('YouTube se koi response nahi mil saka.')

def fetch_richest_youtube_info(clean_url):
    """Evaluates ALL attempts and keeps the one with the richest format list."""
    best_info = None
    best_score = (-1, -1)
    best_heights = []
    attempts = []

    for clients, use_cookies, label in _attempt_plan():
        opts = get_yt_opts(clients, use_cookies=use_cookies)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(clean_url, download=False)
                if not info:
                    continue
                formats = info.get('formats', [])
                heights = set()
                for f in formats:
                    h = f.get('height')
                    if h and f.get('vcodec') != 'none':
                        heights.add(int(h))
                max_h = max(heights) if heights else 0
                num_h = len(heights)
                score = (max_h, num_h)
                if score > best_score:
                    best_score = score
                    best_info = info
                    best_heights = sorted(list(heights), reverse=True)
        except Exception as e:
            attempts.append(f'{label}: {clean_ansi(str(e))[:140]}')

    if best_info is not None:
        return best_info, best_heights

    if attempts:
        err = Exception('Sabhi koshishein fail ho gayi: ' + ' || '.join(attempts))
        err.attempts = attempts
        raise err
    raise Exception('YouTube se koi formats nahi mil sake.')

def friendly_error(e):
    """Transforms raw yt-dlp error tracebacks into helpful Hinglish messages."""
    raw = clean_ansi(str(e))
    low = raw.lower()
    if any(k in low for k in ('login', 'logged-in', 'checkpoint', 'sign in', 'credentials', 'authenticate')):
        return ('Is video/reel ko download karne ke liye login ki zaroorat hai. '
                'Ilaaj: Apne browser se us site ki cookies Netscape format me export karein, '
                'file ka naam "social-cookies.txt" (ya YouTube ke liye "youtube_cookies.txt") '
                'rakhein aur app folder me daal dein. '
                f'(Asli details: {raw[:300]})')
    if 'requested format is not available' in low:
        return ('Server ne video formats restrict kar diye. '
                f'(Asli details: {raw[:400]})')
    if 'failed to extract any player response' in low or 'not a bot' in low:
        return ('Server ko bot samajh kar block kiya gaya. '
                f'(Asli details: {raw[:300]})')
    if 'this video is unavailable' in low or 'video unavailable' in low or 'private video' in low:
        return ('Ye video unavailable ya private hai, ya delete ho chuka hai. '
                f'(Asli details: {raw[:300]})')
    if 'http error 403' in low or '403 forbidden' in low:
        return ('Server access deny hua (HTTP 403). Cookies expire ho sakti hain. '
                f'(Asli details: {raw[:300]})')
    return raw

# ==================== FLASK ROUTES ====================

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/health')
def health():
    """Diagnostic health check endpoint."""
    import shutil as _sh
    yt_info = _cookie_info(YOUTUBE_COOKIE, check_domain='youtube.com')
    masai_info = _cookie_info(MASAI_COOKIE, check_domain='masaischool.com')
    social_info = _cookie_info(SOCIAL_COOKIE)
    return jsonify({
        'ok': True,
        'build': 'v3.0-desktop',
        'pot_configured': bool(POT_BASE_URL),
        'ffmpeg_exe': FFMPEG_EXE,
        'ffmpeg_exists': os.path.exists(FFMPEG_EXE),
        'ffmpeg_on_path': bool(_sh.which('ffmpeg')),
        'yt_dlp_version': yt_dlp.version.__version__,
        'masai_cookie_present': bool(MASAI_COOKIE and os.path.exists(MASAI_COOKIE)),
        'masai_cookie_size_bytes': masai_info['size_bytes'],
        'youtube_cookie_present': bool(YOUTUBE_COOKIE and os.path.exists(YOUTUBE_COOKIE)),
        'youtube_cookie_size_bytes': yt_info['size_bytes'],
        'social_cookie_present': bool(SOCIAL_COOKIE and os.path.exists(SOCIAL_COOKIE)),
        'social_cookie_size_bytes': social_info['size_bytes'],
        'task_store': 'sqlite',
        'task_db_exists': os.path.exists(DB_PATH),
        'downloads_folder': USER_DOWNLOADS_DIR
    })

@app.route('/open-download-folder', methods=['POST'])
def open_download_folder():
    """Opens the local ~/Downloads/Masai Downloader folder in Windows Explorer."""
    try:
        os.makedirs(USER_DOWNLOADS_DIR, exist_ok=True)
        if os.name == 'nt':
            os.startfile(USER_DOWNLOADS_DIR)
        else:
            import subprocess
            subprocess.Popen(['xdg-open', USER_DOWNLOADS_DIR])
        return jsonify({'ok': True, 'folder': USER_DOWNLOADS_DIR})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

# ==================== SOCIAL (INSTA, FB, TIKTOK, TWITTER, ETC.) ====================

def get_social_opts(extra_opts=None):
    """Generic yt-dlp options for social media platforms (Insta, FB, etc.)."""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'nocheckcertificate': True,
        'ffmpeg_location': FFMPEG_EXE,
        'retries': 10,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Sec-Fetch-Mode': 'navigate'
        }
    }
    if SOCIAL_COOKIE and os.path.exists(SOCIAL_COOKIE):
        opts['cookiefile'] = SOCIAL_COOKIE
    if PROXY_URL:
        opts['proxy'] = PROXY_URL
    if extra_opts:
        opts.update(extra_opts)
    return opts

def run_fetch_social_info(task_id, raw_url):
    try:
        opts = get_social_opts({'extract_flat': 'in_playlist'})
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(raw_url, download=False)
            if not info:
                raise Exception("Media info extract nahi ho saki.")

            is_playlist = 'entries' in info or info.get('_type') == 'playlist'
            title = info.get('title') or info.get('description') or 'Social Media Video'
            if len(title) > 80:
                title = title[:77] + '...'

            thumb = info.get('thumbnail')
            extractor = info.get('extractor_key') or info.get('extractor') or 'Social Media'

            if is_playlist:
                entries = list(info.get('entries', []))
                TASKS[task_id] = {
                    'status': 'done',
                    'is_playlist': True,
                    'title': title,
                    'count': len(entries),
                    'extractor': extractor,
                    'thumbnail': thumb
                }
                return

            formats = info.get('formats', [])
            available_heights = set()
            for f in formats:
                h = f.get('height')
                if h and f.get('vcodec') != 'none':
                    available_heights.add(int(h))

            sorted_heights = sorted(list(available_heights), reverse=True)

            TASKS[task_id] = {
                'status': 'done',
                'is_playlist': False,
                'title': title,
                'thumbnail': thumb,
                'extractor': extractor,
                'resolutions': sorted_heights
            }
    except Exception as e:
        TASKS[task_id] = {'status': 'error', 'error': friendly_error(e)}

@app.route('/fetch-social-info', methods=['POST'])
def fetch_social_info():
    data = request.get_json() or {}
    raw_url = data.get('url', '').strip()

    if not raw_url:
        return jsonify({'error': 'Kripya link provide karein!'}), 400

    task_id = str(uuid.uuid4())[:8]
    TASKS[task_id] = {'status': 'processing'}

    t = threading.Thread(target=run_fetch_social_info, args=(task_id, raw_url))
    t.daemon = True
    t.start()

    return jsonify({'status': 'started', 'task_id': task_id})

def run_social_download(task_id, raw_url, quality, is_playlist):
    work_dir = None
    try:
        fmt_opts = {
            'ffmpeg_location': FFMPEG_EXE,
            'quiet': False
        }

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

        opts = get_social_opts(fmt_opts)

        if is_playlist:
            # Download collection into subfolder and zip
            with yt_dlp.YoutubeDL(dict(opts, extract_flat=True)) as ydl_flat:
                info_flat = ydl_flat.extract_info(raw_url, download=False)
                clean_title = re.sub(r'[\\/*?:"<>|]', "", info_flat.get('title', 'Social_Collection'))

            work_dir = os.path.join(DOWNLOAD_DIR, f"{task_id}_{clean_title}")
            os.makedirs(work_dir, exist_ok=True)
            opts['outtmpl'] = os.path.join(work_dir, '%(autonumber)02d - %(title).80s.%(ext)s')

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([raw_url])

            zip_filename = f"{clean_title}.zip"
            zip_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_{zip_filename}")
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(work_dir):
                    for file in files:
                        zipf.write(os.path.join(root, file), file)

            # Copy to user's ~/Downloads/Masai Downloader
            final_saved = _save_to_user_downloads(zip_path)

            TASKS[task_id] = {
                'status': 'done',
                'filename': os.path.basename(final_saved),
                'saved_path': final_saved,
                'folder': USER_DOWNLOADS_DIR
            }
        else:
            opts['outtmpl'] = os.path.join(DOWNLOAD_DIR, f"{task_id}_%(title).100s.%(ext)s")
            opts['noplaylist'] = True

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(raw_url, download=True)
                filename = ydl.prepare_filename(info)
                final_file = os.path.splitext(filename)[0] + f'.{target_ext}'

                if not os.path.exists(final_file) and os.path.exists(filename):
                    final_file = filename

                if os.path.exists(final_file):
                    final_saved = _save_to_user_downloads(final_file)
                    TASKS[task_id] = {
                        'status': 'done',
                        'filename': os.path.basename(final_saved),
                        'saved_path': final_saved,
                        'folder': USER_DOWNLOADS_DIR
                    }
                else:
                    TASKS[task_id] = {'status': 'error', 'error': 'Media merge process fail ho gaya.'}
    except Exception as e:
        TASKS[task_id] = {'status': 'error', 'error': friendly_error(e)}
    finally:
        if work_dir and os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

@app.route('/download-social', methods=['POST'])
def download_social():
    data = request.get_json() or {}
    raw_url = data.get('url', '').strip()
    quality = data.get('quality', 'best')
    is_playlist = data.get('is_playlist', False)

    if not raw_url:
        return jsonify({'error': 'Kripya link provide karein!'}), 400

    task_id = str(uuid.uuid4())[:8]
    TASKS[task_id] = {'status': 'processing'}

    t = threading.Thread(target=run_social_download, args=(task_id, raw_url, quality, is_playlist))
    t.daemon = True
    t.start()

    return jsonify({'status': 'started', 'task_id': task_id})

# ==================== DIRECT / M3U8 DOWNLOAD ====================
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

        if PROXY_URL:
            ydl_opts['proxy'] = PROXY_URL

        if 'masaischool.com' in domain:
            if MASAI_COOKIE and os.path.exists(MASAI_COOKIE):
                ydl_opts['cookiefile'] = MASAI_COOKIE
            ydl_opts['http_headers'] = {
                'Referer': 'https://students.masaischool.com/',
                'Origin': 'https://students.masaischool.com',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
            }
        else:
            ydl_opts['http_headers'] = {
                'Referer': f"{parsed_url.scheme}://{parsed_url.netloc}/",
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
            }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            filename = ydl.prepare_filename(info)
            final_file = os.path.splitext(filename)[0] + '.mp4'
            if not os.path.exists(final_file) and os.path.exists(filename):
                final_file = filename

            if os.path.exists(final_file):
                final_saved = _save_to_user_downloads(final_file)
                TASKS[task_id] = {
                    'status': 'done',
                    'filename': os.path.basename(final_saved),
                    'saved_path': final_saved,
                    'folder': USER_DOWNLOADS_DIR
                }
            else:
                TASKS[task_id] = {'status': 'error', 'error': 'Video merge process fail ho gaya.'}
    except Exception as e:
        err_text = clean_ansi(str(e))
        if 'masaischool.com' in video_url and ('403' in err_text or 'Forbidden' in err_text):
            err_text = ('Masai ne lecture download rok diya (403 Forbidden). '
                        'cookies.txt expire ho gayi hai ya folder me nahi hai. '
                        'Apne browser se students.masaischool.com ki cookies.txt app folder me daalein.')
        TASKS[task_id] = {'status': 'error', 'error': err_text}

@app.route('/download-universal', methods=['POST'])
def download_universal():
    data = request.get_json() or {}
    raw_url = data.get('url', '').strip()

    if not raw_url:
        return jsonify({'error': 'Kripya stream URL provide karein!'}), 400

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

# ==================== YOUTUBE QUALITY FETCH & DOWNLOAD ====================

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
            TASKS[task_id] = {'status': 'error', 'error': 'Invalid YouTube URL! Sahi video link enter karein.'}
            return

        clean_url = f"https://www.youtube.com/watch?v={vid}"
        best_info, sorted_heights = fetch_richest_youtube_info(clean_url)

        TASKS[task_id] = {
            'status': 'done',
            'is_playlist': False,
            'title': best_info.get('title', 'YouTube Video'),
            'thumbnail': best_info.get('thumbnail', f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'),
            'resolutions': sorted_heights
        }
    except Exception as e:
        TASKS[task_id] = {'status': 'error', 'error': friendly_error(e),
                          'attempts': getattr(e, 'attempts', None)}

@app.route('/fetch-youtube-info', methods=['POST'])
def fetch_youtube_info():
    data = request.get_json() or {}
    raw_url = data.get('url', '').strip()

    if not raw_url:
        return jsonify({'error': 'Kripya YouTube URL provide karein!'}), 400

    task_id = str(uuid.uuid4())[:8]
    TASKS[task_id] = {'status': 'processing'}

    t = threading.Thread(target=run_fetch_youtube_info, args=(task_id, raw_url))
    t.daemon = True
    t.start()

    return jsonify({'status': 'started', 'task_id': task_id})

def run_youtube_download(task_id, raw_url, quality, is_playlist):
    playlist_dir = None
    try:
        if not is_playlist:
            vid = extract_video_id(raw_url)
            video_url = f"https://www.youtube.com/watch?v={vid}" if vid else raw_url
        else:
            video_url = raw_url

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

            playlist_dir = os.path.join(DOWNLOAD_DIR, f"{task_id}_{playlist_title}")
            os.makedirs(playlist_dir, exist_ok=True)
            fmt_opts['outtmpl'] = os.path.join(playlist_dir, '%(autonumber)02d - %(title)s.%(ext)s')

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

            zip_filename = f"{playlist_title}.zip"
            zip_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_{zip_filename}")
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(playlist_dir):
                    for file in files:
                        zipf.write(os.path.join(root, file), file)

            final_saved = _save_to_user_downloads(zip_path)

            TASKS[task_id] = {
                'status': 'done',
                'filename': os.path.basename(final_saved),
                'saved_path': final_saved,
                'folder': USER_DOWNLOADS_DIR
            }

        else:
            fmt_opts['outtmpl'] = os.path.join(DOWNLOAD_DIR, f"{task_id}_%(title)s.%(ext)s")
            fmt_opts['noplaylist'] = True

            info, filename = extract_with_fallback(video_url, download=True, extra_opts=fmt_opts)
            final_file = os.path.splitext(filename)[0] + f'.{target_ext}'

            if not os.path.exists(final_file) and os.path.exists(filename):
                final_file = filename

            if os.path.exists(final_file):
                final_saved = _save_to_user_downloads(final_file)
                TASKS[task_id] = {
                    'status': 'done',
                    'filename': os.path.basename(final_saved),
                    'saved_path': final_saved,
                    'folder': USER_DOWNLOADS_DIR
                }
            else:
                TASKS[task_id] = {'status': 'error', 'error': 'Video merge process fail ho gaya.'}
    except Exception as e:
        TASKS[task_id] = {'status': 'error', 'error': friendly_error(e),
                          'attempts': getattr(e, 'attempts', None)}
    finally:
        if playlist_dir and os.path.exists(playlist_dir):
            shutil.rmtree(playlist_dir, ignore_errors=True)

@app.route('/download-youtube', methods=['POST'])
def download_youtube():
    data = request.get_json() or {}
    raw_url = data.get('url', '').strip()
    quality = data.get('quality', 'best')
    is_playlist = data.get('is_playlist', False)

    if not raw_url:
        return jsonify({'error': 'Kripya YouTube URL provide karein!'}), 400

    task_id = str(uuid.uuid4())[:8]
    TASKS[task_id] = {'status': 'processing'}

    t = threading.Thread(target=run_youtube_download, args=(task_id, raw_url, quality, is_playlist))
    t.daemon = True
    t.start()

    return jsonify({'status': 'started', 'task_id': task_id})

@app.route('/get-file/<path:filename>')
def get_file(filename):
    """Fallback file serving route if accessed outside desktop app mode."""
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
    debug_mode = os.environ.get('FLASK_DEBUG') == '1'
    port = int(os.environ.get('PORT', 5000))
    app.run(host='127.0.0.1', port=port, debug=debug_mode, threaded=True)
