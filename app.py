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

# ==================== PERSISTENT TASK STORAGE (SQLite) ====================
# Multi-worker gunicorn setups (e.g. Render) run separate processes that don't
# share in-memory dictionaries. SQLite provides shared, ACID-compliant storage
# accessible by all workers and threads without extra services.
DB_PATH = os.path.join(DOWNLOAD_DIR, 'tasks.db')

def init_db():
    try:
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT,
                    data TEXT,
                    updated_at REAL
                )
            ''')
    except Exception as e:
        print("DB init error:", e)

init_db()

def set_task(task_id, status, extra_data=None):
    try:
        data_json = json.dumps(extra_data) if extra_data else '{}'
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            conn.execute('''
                INSERT INTO tasks (task_id, status, data, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,
                    data=excluded.data,
                    updated_at=excluded.updated_at
            ''', (task_id, status, data_json, time.time()))
    except Exception as e:
        print(f"Error set_task {task_id}:", e)

def get_task(task_id):
    try:
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            cur = conn.cursor()
            cur.execute('SELECT status, data FROM tasks WHERE task_id = ?', (task_id,))
            row = cur.fetchone()
            if row:
                status, data_json = row
                res = {'status': status}
                if data_json:
                    try:
                        res.update(json.loads(data_json))
                    except Exception:
                        pass
                return res
    except Exception as e:
        print(f"Error get_task {task_id}:", e)
    return None

# ==================== COOKIE VALIDATION & SETUP ====================
def is_valid_netscape_cookie(filepath):
    if not filepath or not os.path.exists(filepath):
        return False
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for _ in range(10):
                line = f.readline()
                if not line:
                    break
                if '# Netscape' in line or '\t' in line:
                    return True
    except Exception:
        pass
    return False

def get_valid_cookie(secret_path, local_path, writable_path):
    target = None
    if os.path.exists(secret_path):
        try:
            shutil.copyfile(secret_path, writable_path)
            target = writable_path
        except Exception:
            target = secret_path
    elif os.path.exists(local_path):
        target = local_path

    if target and is_valid_netscape_cookie(target):
        return target
    return None

# Masai School authentication cookie (for students.masaischool.com streams only)
RENDER_SECRET_COOKIE = '/etc/secrets/cookies.txt'
LOCAL_COOKIE = os.path.join(BASE_DIR, 'cookies.txt')
WRITABLE_COOKIE = '/tmp/cookies.txt'
MASAI_COOKIE = get_valid_cookie(RENDER_SECRET_COOKIE, LOCAL_COOKIE, WRITABLE_COOKIE)

# FFmpeg setup
ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
ffmpeg_dir = os.path.dirname(ffmpeg_exe)
os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

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

def get_yt_opts():
    """Uses visionos+android player clients that bypass YouTube bot-check
    without requiring signed-in cookies. Confirmed to work on server IPs and locally (48+ formats).
    Note: yt-dlp skips visionos and android if a cookie file is passed, which causes
    'Requested format is not available' errors. Therefore, no cookiefile is passed for YouTube.
    """
    opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'nocheckcertificate': True,
        'extractor_args': {
            'youtube': {
                # visionos is the primary client (48 formats, no cookies needed).
                # android is the fallback (5 formats, works across yt-dlp versions).
                # web/web_creator/ios intentionally excluded — require sign-in on server IPs.
                'player_client': ['visionos', 'android'],
            }
        },
        'format_sort': ['res', 'ext:mp4:m4a', 'size', 'br', 'asr'],
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }
    return opts

@app.route('/')
def home():
    return render_template('index.html')

# ==================== UNIVERSAL / M3U8 DOWNLOAD ====================
def run_universal_download(task_id, video_url):
    try:
        parsed_url = urlparse(video_url)
        domain = parsed_url.netloc.lower()

        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': os.path.join(DOWNLOAD_DIR, f"{task_id}_%(title).100s.%(ext)s"),
            'merge_output_format': 'mp4',
            'socket_timeout': 30,
            'retries': 15,
            'fragment_retries': 15,
            'quiet': False
        }

        if 'masaischool.com' in domain:
            if MASAI_COOKIE and is_valid_netscape_cookie(MASAI_COOKIE):
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
                set_task(task_id, 'done', {'filename': os.path.basename(final_file)})
            else:
                set_task(task_id, 'error', {'error': 'Video merge process failed.'})
    except Exception as e:
        set_task(task_id, 'error', {'error': clean_ansi(str(e))})

@app.route('/download-universal', methods=['POST'])
def download_universal():
    data = request.get_json()
    raw_url = data.get('url', '').strip()

    if not raw_url:
        return jsonify({'error': 'URL provide karein!'}), 400

    video_url = fix_m3u8_url(raw_url)
    task_id = str(uuid.uuid4())[:8]
    set_task(task_id, 'processing')

    t = threading.Thread(target=run_universal_download, args=(task_id, video_url))
    t.daemon = True
    t.start()

    return jsonify({'status': 'started', 'task_id': task_id})

@app.route('/task-status/<task_id>')
def task_status(task_id):
    task = get_task(task_id)
    if not task:
        # Avoid premature 404 which can abort frontend polling on multi-worker delays
        return jsonify({'status': 'processing', 'retry': True}), 200
    return jsonify(task)

# ==================== YOUTUBE ROUTES ====================
def run_fetch_youtube_info(task_id, raw_url):
    try:
        if 'playlist?list=' in raw_url:
            ydl_opts = get_yt_opts()
            ydl_opts['extract_flat'] = True
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(raw_url, download=False)
                set_task(task_id, 'done', {
                    'is_playlist': True,
                    'title': info.get('title', 'YouTube Playlist'),
                    'count': len(list(info.get('entries', [])))
                })
            return

        vid = extract_video_id(raw_url)
        clean_url = f"https://www.youtube.com/watch?v={vid}" if vid else raw_url

        with yt_dlp.YoutubeDL(get_yt_opts()) as full_ydl:
            full_info = full_ydl.extract_info(clean_url, download=False)
            available_heights = set()
            for f in full_info.get('formats', []):
                h = f.get('height')
                if h and f.get('vcodec') != 'none':
                    try:
                        available_heights.add(int(h))
                    except (ValueError, TypeError):
                        pass

            sorted_heights = sorted(list(available_heights), reverse=True)
            if not sorted_heights:
                sorted_heights = [1080, 720, 480, 360]

            thumbnail = full_info.get('thumbnail') or (f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg' if vid else '')

            set_task(task_id, 'done', {
                'is_playlist': False,
                'title': full_info.get('title', 'YouTube Video'),
                'thumbnail': thumbnail,
                'resolutions': sorted_heights
            })
    except Exception as e:
        set_task(task_id, 'error', {'error': clean_ansi(str(e))})

@app.route('/fetch-youtube-info', methods=['POST'])
def fetch_youtube_info():
    data = request.get_json()
    raw_url = data.get('url', '').strip()

    if not raw_url:
        return jsonify({'error': 'URL provide karein!'}), 400

    task_id = str(uuid.uuid4())[:8]
    set_task(task_id, 'processing')

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

        ydl_opts = get_yt_opts()
        ydl_opts.update({'retries': 10, 'quiet': False})

        if quality == 'mp3':
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
            target_ext = 'mp3'
        elif quality == 'best':
            ydl_opts.update({
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'merge_output_format': 'mp4'
            })
            target_ext = 'mp4'
        else:
            ydl_opts.update({
                'format': f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best',
                'merge_output_format': 'mp4'
            })
            target_ext = 'mp4'

        if is_playlist:
            with yt_dlp.YoutubeDL(dict(get_yt_opts(), extract_flat=True)) as ydl:
                info_flat = ydl.extract_info(video_url, download=False)
                playlist_title = re.sub(r'[\\/*?:"<>|]', "", info_flat.get('title', 'YouTube_Playlist'))

            playlist_dir = os.path.join(DOWNLOAD_DIR, f"{task_id}_{playlist_title}")
            os.makedirs(playlist_dir, exist_ok=True)
            ydl_opts['outtmpl'] = os.path.join(playlist_dir, '%(autonumber)02d - %(title)s.%(ext)s')

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])

            zip_filename = f"{task_id}_{playlist_title}.zip"
            zip_path = os.path.join(DOWNLOAD_DIR, zip_filename)
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(playlist_dir):
                    for file in files:
                        zipf.write(os.path.join(root, file), file)

            set_task(task_id, 'done', {'filename': zip_filename})

        else:
            ydl_opts['outtmpl'] = os.path.join(DOWNLOAD_DIR, f"{task_id}_%(title)s.%(ext)s")
            ydl_opts['noplaylist'] = True

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                filename = ydl.prepare_filename(info)
                final_file = os.path.splitext(filename)[0] + f'.{target_ext}'

                if not os.path.exists(final_file) and os.path.exists(filename):
                    final_file = filename

                if not os.path.exists(final_file):
                    candidates = [
                        os.path.join(DOWNLOAD_DIR, f)
                        for f in os.listdir(DOWNLOAD_DIR)
                        if f.startswith(f"{task_id}_") and not f.endswith(('.temp', '.part', '.ytdl', '.db'))
                    ]
                    if candidates:
                        final_file = candidates[0]

                if os.path.exists(final_file):
                    set_task(task_id, 'done', {'filename': os.path.basename(final_file)})
                else:
                    set_task(task_id, 'error', {'error': 'Video merge process failed.'})
    except Exception as e:
        set_task(task_id, 'error', {'error': clean_ansi(str(e))})
    finally:
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
    set_task(task_id, 'processing')

    t = threading.Thread(target=run_youtube_download, args=(task_id, raw_url, quality, is_playlist))
    t.daemon = True
    t.start()

    return jsonify({'status': 'started', 'task_id': task_id})

@app.route('/get-file/<path:filename>')
def get_file(filename):
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
    app.run(debug=debug_mode, port=int(os.environ.get('PORT', 5000)), threaded=True)
