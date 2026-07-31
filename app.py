# ============================================================
#   VIDEO DOWNLOADER - Flask Backend
#   Requirements: pip install flask flask-cors yt-dlp
#   Also install FFmpeg: https://ffmpeg.org/download.html
#   Run: python app.py
# ============================================================

from flask import Flask, request, jsonify, send_file, after_this_request
from flask_cors import CORS
import yt_dlp
import os
import tempfile
import uuid
import threading
import time

app = Flask(__name__)
CORS(app)  # Allow frontend to communicate with backend

DOWNLOAD_DIR = tempfile.mkdtemp()  # Temp folder for downloaded files
# Path to cookies file (placed in the same folder as app.py)
COOKIE_PATH = os.path.join(os.path.dirname(__file__), 'cookies.txt')

# ─────────────────────────────────────────────
#  Auto-cleanup: delete files older than 5 min
# ─────────────────────────────────────────────
def cleanup_old_files():
    while True:
        now = time.time()
        for f in os.listdir(DOWNLOAD_DIR):
            fp = os.path.join(DOWNLOAD_DIR, f)
            try:
                if os.path.isfile(fp) and (now - os.path.getmtime(fp)) > 300:
                    os.remove(fp)
            except Exception:
                pass
        time.sleep(60)

threading.Thread(target=cleanup_old_files, daemon=True).start()


# ─────────────────────────────────────────────
#  ROUTE: Fetch video info (title, thumbnail, formats)
# ─────────────────────────────────────────────
@app.route('/api/info', methods=['POST'])
def get_info():
    data = request.get_json()
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'error': 'No URL provided.'}), 400

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'android', 'mweb']
            }
        }
    }

    # If cookies.txt exists, use it
    if os.path.exists(COOKIE_PATH):
        ydl_opts['cookiefile'] = COOKIE_PATH

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # Collect unique video qualities
        formats = []
        seen_heights = set()

        if 'formats' in info:
            for f in reversed(info['formats']):
                height = f.get('height')
                vcodec = f.get('vcodec', 'none')
                if vcodec != 'none' and height and height not in seen_heights:
                    seen_heights.add(height)
                    formats.append({
                        'format_id': f['format_id'],
                        'quality': f"{height}p",
                        'ext': f.get('ext', 'mp4'),
                        'filesize': f.get('filesize') or f.get('filesize_approx') or 0
                    })

        # Sort highest quality first
        formats.sort(key=lambda x: int(x['quality'].replace('p', '')), reverse=True)

        # Fallback if no specific formats found
        if not formats:
            formats = [{'format_id': 'best', 'quality': 'Best Available', 'ext': 'mp4', 'filesize': 0}]

        # Add audio-only option
        formats.append({'format_id': 'bestaudio', 'quality': '🎵 Audio Only (MP3)', 'ext': 'mp3', 'filesize': 0})

        return jsonify({
            'title': info.get('title', 'Unknown Title'),
            'thumbnail': info.get('thumbnail', ''),
            'duration': info.get('duration', 0),
            'uploader': info.get('uploader') or info.get('channel', 'Unknown'),
            'view_count': info.get('view_count', 0),
            'platform': info.get('extractor_key', 'Unknown'),
            'formats': formats
        })

    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': f'Could not fetch video info: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500


# ─────────────────────────────────────────────
#  ROUTE: Download video and stream to browser
# ─────────────────────────────────────────────
@app.route('/api/download', methods=['POST'])
def download_video():
    data = request.get_json()
    url = data.get('url', '').strip()
    format_id = data.get('format_id', 'best')
    quality = data.get('quality', 'best')

    if not url:
        return jsonify({'error': 'No URL provided.'}), 400

    file_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    # Determine format selection
    is_audio_only = format_id == 'bestaudio'

    if is_audio_only:
        fmt = 'bestaudio/best'
        postprocessors = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    else:
        if format_id and format_id != 'best':
            fmt = f"{format_id}+bestaudio/best"
        else:
            fmt = 'bestvideo+bestaudio/best'
        postprocessors = [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }]

    ydl_opts = {
        'format': fmt,
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'merge_output_format': 'mp4' if not is_audio_only else None,
        'postprocessors': postprocessors,
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'android', 'mweb']
            }
        }
    }

    # If cookies.txt exists, use it
    if os.path.exists(COOKIE_PATH):
        ydl_opts['cookiefile'] = COOKIE_PATH

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'video')

        # Find the downloaded file by its UUID prefix
        downloaded_file = None
        for fname in os.listdir(DOWNLOAD_DIR):
            if fname.startswith(file_id):
                downloaded_file = os.path.join(DOWNLOAD_DIR, fname)
                file_ext = fname.rsplit('.', 1)[-1]
                break

        if not downloaded_file or not os.path.exists(downloaded_file):
            return jsonify({'error': 'Download failed — file not found.'}), 500

        # Sanitize filename
        safe_title = "".join(
            c for c in title if c.isalnum() or c in (' ', '-', '_', '.')
        ).strip()
        safe_title = safe_title[:80] or "video"
        download_name = f"{safe_title}.{file_ext}"

        mime = 'audio/mpeg' if file_ext == 'mp3' else 'video/mp4'

        @after_this_request
        def remove_file(response):
            def delete_later():
                time.sleep(5)
                try:
                    os.remove(downloaded_file)
                except Exception:
                    pass
            threading.Thread(target=delete_later, daemon=True).start()
            return response

        return send_file(
            downloaded_file,
            as_attachment=True,
            download_name=download_name,
            mimetype=mime
        )

    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': f'Download error: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500


# ─────────────────────────────────────────────
#  ROUTE: Health check
# ─────────────────────────────────────────────
@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'message': 'Video Downloader API is running!'})


if __name__ == '__main__':
    print("🚀 Video Downloader Backend running at http://localhost:5000")
    print("📂 Temp download directory:", DOWNLOAD_DIR)
    app.run(debug=True, host='0.0.0.0', port=5000)
