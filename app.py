import os
import re
import shutil
import zipfile
from urllib.parse import urlparse
from flask import Flask, render_template, request, send_file, jsonify, after_this_request
import yt_dlp
import imageio_ffmpeg

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, 'downloads')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Cookie Setup (Sirf Masai ke liye use karenge)
RENDER_SECRET_COOKIE = '/etc/secrets/cookies.txt'
LOCAL_COOKIE = os.path.join(BASE_DIR, 'cookies.txt')
WRITABLE_COOKIE = '/tmp/cookies.txt'

COOKIE_FILE = None
if os.path.exists(RENDER_SECRET_COOKIE):
    try:
        shutil.copyfile(RENDER_SECRET_COOKIE, WRITABLE_COOKIE)
        COOKIE_FILE = WRITABLE_COOKIE
    except Exception:
        COOKIE_FILE = RENDER_SECRET_COOKIE
elif os.path.exists(LOCAL_COOKIE):
    COOKIE_FILE = LOCAL_COOKIE

# FFmpeg binary path
ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
ffmpeg_dir = os.path.dirname(ffmpeg_exe)
os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

def clean_ansi(text):
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

def extract_video_id(url):
    """Har tarah ke URL se clean 11-char Video ID extract karta hai"""
    patterns = [
        r'youtube\.com/live/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'v=([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def get_yt_opts():
    """TV and Android Embedded clients bypass cloud IP restrictions completely"""
    return {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'extractor_args': {
            'youtube': {
                'player_client': ['tv', 'tv_embedded', 'android'],
                'player_skip': ['webpage', 'configs', 'js']
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (SmartHub; SMART-TV; U; Linux/SmartTV) AppleWebKit/538.1+ (KHTML, like Gecko) TV Safari/538.1+'
        }
    }

@app.route('/')
def home():
    return render_template('index.html')

# ==================== UNIVERSAL / M3U8 ROUTE ====================
@app.route('/download-universal', methods=['POST'])
def download_universal():
    data = request.get_json()
    video_url = data.get('url', '').strip()

    if not video_url:
        return jsonify({'error': 'URL provide karein!'}), 400

    parsed_url = urlparse(video_url)
    domain = parsed_url.netloc.lower()

    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title).100s.%(ext)s'),
        'merge_output_format': 'mp4',
        'socket_timeout': 30,
        'retries': 15,
        'fragment_retries': 15,
        'quiet': False
    }

    if 'masaischool.com' in domain:
        if COOKIE_FILE and os.path.exists(COOKIE_FILE):
            ydl_opts['cookiefile'] = COOKIE_FILE
        ydl_opts['http_headers'] = {
            'Referer': 'https://students.masaischool.com/',
            'Origin': 'https://students.masaischool.com'
        }
    else:
        ydl_opts['http_headers'] = {
            'Referer': f"{parsed_url.scheme}://{parsed_url.netloc}/"
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            filename = ydl.prepare_filename(info)
            final_file = os.path.splitext(filename)[0] + '.mp4'
            if not os.path.exists(final_file) and os.path.exists(filename):
                final_file = filename

            if os.path.exists(final_file):
                return jsonify({'status': 'success', 'filename': os.path.basename(final_file)})
            return jsonify({'error': 'File process nahi ho paayi.'}), 500
    except Exception as e:
        return jsonify({'error': clean_ansi(str(e))}), 500

# ==================== STEP 1: FETCH YOUTUBE DETAILS ====================
@app.route('/fetch-youtube-info', methods=['POST'])
def fetch_youtube_info():
    data = request.get_json()
    raw_url = data.get('url', '').strip()

    if not raw_url:
        return jsonify({'error': 'URL provide karein!'}), 400

    # Check agar Playlist hai
    if 'playlist?list=' in raw_url:
        ydl_opts = get_yt_opts()
        ydl_opts['extract_flat'] = True
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(raw_url, download=False)
                return jsonify({
                    'is_playlist': True,
                    'title': info.get('title', 'YouTube Playlist'),
                    'count': len(list(info.get('entries', [])))
                })
        except Exception as e:
            return jsonify({'error': clean_ansi(str(e))}), 500

    # Video ID extract karein
    vid = extract_video_id(raw_url)
    if not vid:
        return jsonify({'error': 'Invalid YouTube URL! Please check the link.'}), 400

    clean_url = f"https://www.youtube.com/watch?v={vid}"
    ydl_opts = get_yt_opts()

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as full_ydl:
            full_info = full_ydl.extract_info(clean_url, download=False)
            available_heights = set()
            for f in full_info.get('formats', []):
                h = f.get('height')
                if h and f.get('vcodec') != 'none':
                    available_heights.add(h)

            sorted_heights = sorted(list(available_heights), reverse=True)

            return jsonify({
                'is_playlist': False,
                'title': full_info.get('title', 'YouTube Video'),
                'thumbnail': full_info.get('thumbnail', f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'),
                'resolutions': sorted_heights
            })

    except Exception as e:
        return jsonify({'error': clean_ansi(str(e))}), 500

# ==================== STEP 2: DOWNLOAD SELECTED FORMAT ====================
@app.route('/download-youtube', methods=['POST'])
def download_youtube():
    data = request.get_json()
    raw_url = data.get('url', '').strip()
    quality = data.get('quality', 'best')
    is_playlist = data.get('is_playlist', False)

    if not raw_url:
        return jsonify({'error': 'URL provide karein!'}), 400

    if not is_playlist:
        vid = extract_video_id(raw_url)
        video_url = f"https://www.youtube.com/watch?v={vid}" if vid else raw_url
    else:
        video_url = raw_url

    try:
        ydl_opts = get_yt_opts()
        ydl_opts.update({
            'retries': 10,
            'quiet': False
        })

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

            playlist_dir = os.path.join(DOWNLOAD_DIR, playlist_title)
            os.makedirs(playlist_dir, exist_ok=True)
            ydl_opts['outtmpl'] = os.path.join(playlist_dir, '%(autonumber)02d - %(title)s.%(ext)s')

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])

            zip_filename = f"{playlist_title}.zip"
            zip_path = os.path.join(DOWNLOAD_DIR, zip_filename)
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(playlist_dir):
                    for file in files:
                        zipf.write(os.path.join(root, file), file)

            shutil.rmtree(playlist_dir, ignore_errors=True)
            return jsonify({'status': 'success', 'filename': zip_filename})

        else:
            ydl_opts['outtmpl'] = os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s')
            ydl_opts['noplaylist'] = True

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                filename = ydl.prepare_filename(info)
                final_file = os.path.splitext(filename)[0] + f'.{target_ext}'

                if not os.path.exists(final_file) and os.path.exists(filename):
                    final_file = filename

                return jsonify({'status': 'success', 'filename': os.path.basename(final_file)})

    except Exception as e:
        return jsonify({'error': clean_ansi(str(e))}), 500

# ==================== FILE DELIVERY & AUTO CLEANUP ====================
@app.route('/get-file/<path:filename>')
def get_file(filename):
    file_path = os.path.join(DOWNLOAD_DIR, filename)
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
    app.run(debug=True, port=5000)