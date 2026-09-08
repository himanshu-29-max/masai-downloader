import os
import re
import shutil
import zipfile
from urllib.parse import urlparse
from flask import Flask, render_template, request, send_file, jsonify, after_this_request
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
        'retries': 10,
        'fragment_retries': 10,
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
                return jsonify({'status': 'success', 'filename': os.path.basename(final_file)})
            return jsonify({'error': 'File process nahi ho paayi.'}), 500
    except Exception as e:
        return jsonify({'error': clean_ansi(str(e))}), 500

@app.route('/fetch-youtube-info', methods=['POST'])
def fetch_youtube_info():
    data = request.get_json()
    video_url = data.get('url', '').strip()

    if not video_url:
        return jsonify({'error': 'URL provide karein!'}), 400

    try:
        with yt_dlp.YoutubeDL({'extract_flat': True, 'quiet': True}) as ydl:
            info = ydl.extract_info(video_url, download=False)
            
            if 'entries' in info:
                return jsonify({
                    'is_playlist': True,
                    'title': info.get('title', 'YouTube Playlist'),
                    'count': len(list(info.get('entries', [])))
                })

            with yt_dlp.YoutubeDL({'quiet': True}) as full_ydl:
                full_info = full_ydl.extract_info(video_url, download=False)
                available_heights = set()
                for f in full_info.get('formats', []):
                    h = f.get('height')
                    if h and f.get('vcodec') != 'none':
                        available_heights.add(h)

                sorted_heights = sorted(list(available_heights), reverse=True)

                return jsonify({
                    'is_playlist': False,
                    'title': full_info.get('title', 'YouTube Video'),
                    'thumbnail': full_info.get('thumbnail', ''),
                    'resolutions': sorted_heights
                })
    except Exception as e:
        return jsonify({'error': clean_ansi(str(e))}), 500

@app.route('/download-youtube', methods=['POST'])
def download_youtube():
    data = request.get_json()
    video_url = data.get('url', '').strip()
    quality = data.get('quality', 'best')
    is_playlist = data.get('is_playlist', False)

    if not video_url:
        return jsonify({'error': 'URL provide karein!'}), 400

    try:
        ydl_opts = {'socket_timeout': 30, 'retries': 10}

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
            with yt_dlp.YoutubeDL({'extract_flat': True, 'quiet': True}) as ydl:
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