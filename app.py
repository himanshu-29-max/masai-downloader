import os
import re
import shutil
import zipfile
from urllib.parse import urlparse
from flask import Flask, render_template, request, send_file, jsonify
import yt_dlp

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, 'downloads')
COOKIE_FILE = os.path.join(BASE_DIR, 'cookies.txt')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def clean_ansi(text):
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

@app.route('/')
def home():
    return render_template('index.html')

# ==================== UNIVERSAL / M3U8 ROUTE ====================
@app.route('/download-universal', methods=['POST'])
def download_universal():
    data = request.get_json()
    video_url = data.get('url', '').strip()

    if not video_url:
        return jsonify({'error': 'Pehle URL provide karein!'}), 400

    parsed_url = urlparse(video_url)
    domain = parsed_url.netloc.lower()

    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title).100s.%(ext)s'),
        'merge_output_format': 'mp4',
        'socket_timeout': 30,
        'retries': 15,
        'fragment_retries': 15,
        'quiet': False,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    }

    if 'masaischool.com' in domain:
        if os.path.exists(COOKIE_FILE):
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
                return jsonify({'status': 'success', 'filename': os.path.basename(final_file), 'is_playlist': False})
            return jsonify({'error': 'File process nahi ho paayi.'}), 500
    except Exception as e:
        return jsonify({'error': clean_ansi(str(e))}), 500

# ==================== YOUTUBE & PLAYLIST ROUTE ====================
@app.route('/download-youtube', methods=['POST'])
def download_youtube():
    data = request.get_json()
    video_url = data.get('url', '').strip()

    if not video_url:
        return jsonify({'error': 'YouTube URL provide karein!'}), 400

    try:
        # Pre-check: Kya yeh link playlist hai?
        check_opts = {'extract_flat': True, 'quiet': True}
        with yt_dlp.YoutubeDL(check_opts) as ydl:
            info_flat = ydl.extract_info(video_url, download=False)
            is_playlist = 'entries' in info_flat

        if is_playlist:
            playlist_title = re.sub(r'[\\/*?:"<>|]', "", info_flat.get('title', 'YouTube_Playlist'))
            playlist_dir = os.path.join(DOWNLOAD_DIR, playlist_title)
            os.makedirs(playlist_dir, exist_ok=True)

            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': os.path.join(playlist_dir, '%(autonumber)02d - %(title)s.%(ext)s'),
                'merge_output_format': 'mp4',
                'retries': 10,
                'quiet': False
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])

            # Poori playlist ko zip bana kar ready karein
            zip_filename = f"{playlist_title}.zip"
            zip_path = os.path.join(DOWNLOAD_DIR, zip_filename)
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(playlist_dir):
                    for file in files:
                        zipf.write(os.path.join(root, file), file)

            shutil.rmtree(playlist_dir, ignore_errors=True)
            return jsonify({'status': 'success', 'filename': zip_filename, 'is_playlist': True})

        else:
            # Single YouTube Video
            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
                'merge_output_format': 'mp4',
                'retries': 10,
                'noplaylist': True
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                filename = ydl.prepare_filename(info)
                final_file = os.path.splitext(filename)[0] + '.mp4'
                if not os.path.exists(final_file) and os.path.exists(filename):
                    final_file = filename

                return jsonify({'status': 'success', 'filename': os.path.basename(final_file), 'is_playlist': False})

    except Exception as e:
        return jsonify({'error': clean_ansi(str(e))}), 500

@app.route('/get-file/<path:filename>')
def get_file(filename):
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return "File not found", 404

if __name__ == '__main__':
    app.run(debug=True, port=5000)