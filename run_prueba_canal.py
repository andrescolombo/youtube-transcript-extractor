"""
Script de prueba: descarga las primeras N transcripciones del canal indicado.
Uso: python run_prueba_canal.py [URL_canal] [N]
Por defecto: https://www.youtube.com/@PeladoNerd y 2 videos.
"""

import sys
import io
from pathlib import Path

# En Windows, la consola puede no soportar UTF-8; evitar fallos al imprimir emojis
if sys.stdout.encoding and "utf" not in sys.stdout.encoding.lower():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import (
    get_channel_videos,
    create_ytt_api,
    get_transcript_text,
    save_transcript,
    get_output_path,
    clean_filename,
    BASE_DIR,
    is_browser_client_available,
)

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.youtube.com/@PeladoNerd"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    print("Cliente tipo Chrome (curl_cffi):", "sí" if is_browser_client_available() else "no")
    print("Obteniendo lista del canal...")
    channel_name, videos = get_channel_videos(url)
    print(f"Canal: {channel_name}. Videos: {len(videos)}")
    if not videos:
        print("No hay videos.")
        return

    folder = BASE_DIR / clean_filename(channel_name)
    ytt_api = create_ytt_api(cookies_path=None, use_browser_client=True)

    for i, video in enumerate(videos[:n], 1):
        video_id = video["id"]
        title = video["title"]
        video_url = video["url"]
        if get_output_path(folder, channel_name, title).exists():
            print(f"  [{i}] Ya existe: {title[:50]}...")
            continue
        print(f"  [{i}] Descargando: {title[:50]}...")
        try:
            text = get_transcript_text(video_id, ytt_api, cookies_path=None, log_callback=print)
            path = save_transcript(folder, channel_name, title, video_url, text)
            print(f"      OK -> {path.name}")
        except Exception as e:
            print(f"      Error: {e}")

    print("Listo.")

if __name__ == "__main__":
    main()
