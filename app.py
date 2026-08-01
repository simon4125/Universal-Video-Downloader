# ============================================================
#   VIDEO DOWNLOADER - Render Optimized (Audio + Video Fixed)
#   Requirements: pip install flask flask-cors yt-dlp
# ============================================================

from flask import Flask, request, jsonify, send_file, after_this_request
from flask_cors import CORS
import yt_dlp
import os
import imageio_ffmpeg
import tempfile
import uuid
import threading
import time

app = Flask(__name__)
CORS(app)

DOWNLOAD_DIR = tempfile.mkdtemp()
COOKIE_PATH = os.path.join(os.path.dirname(__file__), 'cookies.txt')


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


def get_base_ydl_opts():
    opts = {
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'geo_bypass_country': 'US',
        'nocheckcertificate': True,
        'ffmpeg_location': ffmpeg_path,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        },
        'extractor_args': {
            'youtube': {
                'player_client': ['tvhtml5', 'android_creator', 'ios', 'mweb']
            }
        }
    }

    if os.path.exists(COOKIE_PATH):
        opts['cookiefile'] = COOKIE_PATH

    return opts


# ─────────────────────────────────────────────
#  ROUTE: Fetch video info
# ─────────────────────────────────────────────
@app.route('/api/info', methods=['POST'])
def get_info():
    data = request.get_json() or {}
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'error': 'No URL provided.'}), 400

    ydl_opts = get_base_ydl_opts()
    ydl_opts['skip_download'] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = []
        seen_qualities = set()

        if 'formats' in info:
            for f in reversed(info['formats']):
                height = f.get('height')
                vcodec = f.get('vcodec', 'none')
                
                if height and vcodec != 'none':
                    q_str = f"{height}p"
                    if q_str not in seen_qualities:
                        seen_qualities.add(q_str)
                        # এটি অডিও ও ভিডিও একত্রে নামানোর জন্য নিরাপদ ফরম্যাট স্ট্রিম সেটআপ
                        fmt_selector = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/b[height<={height}]/best"
                        formats.append({
                            'format_id': fmt_selector,
                            'quality': q_str,
                            'ext': 'mp4',
                            'filesize': f.get('filesize') or f.get('filesize_approx') or 0
                        })

        if not formats:
            formats = [{
                'format_id': 'bestvideo+bestaudio/best/b',
                'quality': 'Best Quality (Video+Audio)',
                'ext': 'mp4',
                'filesize': 0
            }]

        formats.append({
            'format_id': 'bestaudio/best',
            'quality': '🎵 Audio Only (MP3)',
            'ext': 'mp3',
            'filesize': 0
        })

        return jsonify({
            'title': info.get('title', 'Unknown Title'),
            'thumbnail': info.get('thumbnail', ''),
            'duration': info.get('duration', 0),
            'uploader': info.get('uploader') or info.get('channel', 'Unknown'),
            'view_count': info.get('view_count', 0),
            'platform': info.get('extractor_key', 'Unknown'),
            'formats': formats
        })

    except Exception as e:
        return jsonify({'error': f'Could not fetch video info: {str(e)}'}), 400


# ─────────────────────────────────────────────
#  ROUTE: Download video
# ─────────────────────────────────────────────
@app.route('/api/download', methods=['POST'])
def download_video():
    data = request.get_json() or {}
    url = data.get('url', '').strip()
    format_id = data.get('format_id', 'bestvideo+bestaudio/best/b')

    if not url:
        return jsonify({'error': 'No URL provided.'}), 400

    file_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    is_audio_only = 'bestaudio' in format_id

    ydl_opts = get_base_ydl_opts()

    if is_audio_only:
        ydl_opts.update({
            'format': 'bestaudio/best',
            'outtmpl': output_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        ydl_opts.update({
            'format': format_id,
            'outtmpl': output_template,
            'merge_output_format': 'mp4',
            # FFmpeg না থাকলেও অন্তত ভিডিও+অডিও একসাথে থাকা সিঙ্গেল ফাইল নিবে
            'format_sort': ['hasvid', 'hasaud', 'res', 'ext'],
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'video')

        downloaded_file = None
        for fname in os.listdir(DOWNLOAD_DIR):
            if fname.startswith(file_id):
                downloaded_file = os.path.join(DOWNLOAD_DIR, fname)
                break

        if not downloaded_file or not os.path.exists(downloaded_file):
            return jsonify({'error': 'Download failed — file not found.'}), 500

        file_ext = downloaded_file.rsplit('.', 1)[-1]
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()[:80] or "video"
        download_name = f"{safe_title}.{file_ext}"
        mime = 'audio/mpeg' if file_ext == 'mp3' else 'video/mp4'

        @after_this_request
        def remove_file(response):
            def delete_later():
                time.sleep(15)
                try:
                    if os.path.exists(downloaded_file):
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

    except Exception as e:
        return jsonify({'error': f'Download error: {str(e)}'}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)