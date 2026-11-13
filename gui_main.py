import os
import sys
import time
import logging
import threading
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Configure logging to app.log next to app (never console)
log_path = os.path.join(BASE_DIR, 'app.log')
logging.basicConfig(
    filename=log_path,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# Redirect stdout and stderr so yt-dlp / ffmpeg or python prints never need a console window
class _LogWriter:
    def __init__(self, level):
        self.level = level
    def write(self, message):
        msg = message.strip() if message else ""
        if msg:
            self.level(msg)
    def flush(self):
        pass

sys.stdout = _LogWriter(logging.info)
sys.stderr = _LogWriter(logging.warning)

# Import Flask application & target directories
try:
    from app import app, USER_DOWNLOADS_DIR
except Exception as e:
    logging.exception("Failed to import app: %s", e)
    raise

class JsApi:
    """Exposed to JS inside pywebview window as window.pywebview.api"""
    def open_download_folder(self):
        try:
            os.makedirs(USER_DOWNLOADS_DIR, exist_ok=True)
            if os.name == 'nt':
                os.startfile(USER_DOWNLOADS_DIR)
            else:
                import subprocess
                subprocess.Popen(['xdg-open', USER_DOWNLOADS_DIR])
            logging.info("Opened download folder: %s", USER_DOWNLOADS_DIR)
            return True
        except Exception as e:
            logging.exception("Failed to open download folder: %s", e)
            return False

def start_flask():
    try:
        logging.info("Starting Flask backend on 127.0.0.1:5000...")
        app.run(host='127.0.0.1', port=5000, threaded=True, debug=False)
    except Exception as e:
        logging.exception("Flask server error: %s", e)

def main():
    logging.info("=== Masai Downloader Desktop Session Started ===")
    
    # 1. Start Flask in daemon thread
    server_thread = threading.Thread(target=start_flask, daemon=True)
    server_thread.start()

    # 2. Wait up to 6 seconds for Flask to respond
    import urllib.request
    server_ready = False
    for _ in range(30):
        try:
            with urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=1) as resp:
                if resp.status == 200:
                    server_ready = True
                    logging.info("Flask health check succeeded.")
                    break
        except Exception:
            time.sleep(0.2)

    # 3. Launch pywebview EdgeChromium window
    try:
        import webview
        api = JsApi()
        window = webview.create_window(
            'Masai Downloader',
            'http://127.0.0.1:5000',
            width=1120,
            height=800,
            min_size=(920, 620),
            js_api=api,
            text_select=True
        )
        logging.info("Opening pywebview window...")
        webview.start(gui='edgechromium', debug=False)
        logging.info("Window closed by user. Terminating process.")
        sys.exit(0)
    except Exception as e:
        logging.exception("pywebview failed to launch; opening in default browser fallback: %s", e)
        webbrowser.open('http://127.0.0.1:5000')
        # Keep background server alive in browser fallback mode
        while True:
            time.sleep(1)

if __name__ == '__main__':
    main()
