# ============================================================
#   VIDEO DOWNLOADER - Flask Backend (UPDATED & FIXED)
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


def get_base_ydl_opts():
    """ইউটিউবের অ্যান্টি-বট বাইপাস সেটিংস তৈরি করে"""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'geo_bypass_country': 'US',
        'nocheckcertificate': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Sec-Fetch-Mode': 'navigate',
        },
        'extractor_args': {
            'youtube': {
                'player_client': ['tvhtml5', 'android_creator', 'ios', 'mweb']
            },
            'generic': {
                'extractor_args': ['--no-check-certificate']
            },
            'xvideos': {
                'extractor_args': ['--no-check-certificate']
            },
            'pornhub': {
                'extractor_args': ['--no-check-certificate']
            },
            'xhamster': {
                'extractor_args': ['--no-check-certificate']
            }
        },
        'enable_file_urls': True,
        'allow_multiple_video_streams': True,
        'compat_opts': ['no-direct-merge'],
        'overwrites': True,
        'no_resume': True
    }

    if os.path.exists(COOKIE_PATH):
        opts['cookiefile'] = COOKIE_PATH

    return opts


# ─────────────────────────────────────────────
#  ROUTE: Fetch video info (title, thumbnail, formats)
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
        seen_heights = set()

        # ফরম্যাট ফিল্টারিং ফিক্স করা হয়েছে
        if 'formats' in info:
            for f in reversed(info['formats']):
                # শুধু ভিডিও স্ট্রিমগুলোর রেজ্যুলেশন চেক করবে
                height = f.get('height')
                vcodec = f.get('vcodec', 'none')
                
                if height and vcodec != 'none' and height not in seen_heights:
                    seen_heights.add(height)
                    formats.append({
                        'format_id': f.get('format_id'),
                        'quality': f"{height}p",
                        'ext': 'mp4',
                        'filesize': f.get('filesize') or f.get('filesize_approx') or 0
                    })

        # রেজ্যুলেশন অনুযায়ী ক্রমানুসারে সাজানো
        formats.sort(key=lambda x: int(x['quality'].replace('p', '')), reverse=True)

        # যদি কোনো নির্দিষ্ট রেজ্যুলেশন না পাওয়া যায়, তবে সেফ ফলব্যাক
        if not formats:
            formats = [{'format_id': 'bestvideo+bestaudio/best', 'quality': 'Best Available', 'ext': 'mp4', 'filesize': 0}]

        # অডিও ডাউনলোডের অপশন যোগ করা
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
        err_msg = str(e)
        if "not made this video available in your country" in err_msg:
            return jsonify({'error': 'এই ভিডিওটি আপনার অঞ্চলের সার্ভারে ব্লক করা (Geo-Restricted Video)।'}), 400
        elif "Sign in to confirm you’re not a bot" in err_msg:
            return jsonify({'error': 'ইউটিউব বট ডিটেক্ট করেছে। আপনার cookies.txt ফাইলটি আপডেট করুন।'}), 400
        return jsonify({'error': f'Could not fetch video info: {err_msg}'}), 400
    except Exception as e:
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500


# ─────────────────────────────────────────────
#  ROUTE: Download video and stream to browser
# ─────────────────────────────────────────────
@app.route('/api/download', methods=['POST'])
def download_video():
    data = request.get_json() or {}
    url = data.get('url', '').strip()
    format_id = data.get('format_id', 'best')

    if not url:
        return jsonify({'error': 'No URL provided.'}), 400

    file_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    is_audio_only = format_id == 'bestaudio'

    if is_audio_only:
        fmt = 'bestaudio/best'
        postprocessors = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    else:
        # Requested format is not available এররটি আটকানোর জন্য সেফ ফরম্যাট স্ট্রিং
        if format_id and format_id not in ('best', 'bestvideo+bestaudio/best'):
            fmt = f"{format_id}+bestaudio/bestvideo+bestaudio/best"
        else:
            fmt = 'bestvideo+bestaudio/best'
            
        postprocessors = [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }]

    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        'format': fmt,
        'outtmpl': output_template,
        'merge_output_format': 'mp4' if not is_audio_only else None,
        'postprocessors': postprocessors,
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'video')

        downloaded_file = None
        file_ext = 'mp3' if is_audio_only else 'mp4'
        
        for fname in os.listdir(DOWNLOAD_DIR):
            if fname.startswith(file_id):
                downloaded_file = os.path.join(DOWNLOAD_DIR, fname)
                file_ext = fname.rsplit('.', 1)[-1]
                break

        if not downloaded_file or not os.path.exists(downloaded_file):
            return jsonify({'error': 'Download failed — file not found.'}), 500

        safe_title = "".join(
            c for c in title if c.isalnum() or c in (' ', '-', '_', '.')
        ).strip()
        safe_title = safe_title[:80] or "video"
        download_name = f"{safe_title}.{file_ext}"

        mime = 'audio/mpeg' if file_ext == 'mp3' else 'video/mp4'

        @after_this_request
        def remove_file(response):
            def delete_later():
                time.sleep(10)
                try:
                    if os.path.exists(downloaded_file):
                        os.remove(downloaded_file)
                except Exception:
                    pass
            threading.Thread(target=delete_later, daemon=True).start()
            return response

        response = send_file(
            downloaded_file,
            as_attachment=True,
            download_name=download_name,
            mimetype=mime
        )
        response.headers['Content-Disposition'] = f'attachment; filename="{download_name}"'
        response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition'
        return response

    except yt_dlp.utils.DownloadError as e:
        err_msg = str(e)
        if "not made this video available in your country" in err_msg:
            return jsonify({'error': 'এই ভিডিওটি আপনার অঞ্চলের সার্ভারে ব্লক করা (Geo-Restricted Video)।'}), 400
        return jsonify({'error': f'Download error: {err_msg}'}), 400
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