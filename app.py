"""
YouTube Transcript Extractor
=============================
Aplicación web local con Streamlit para descargar transcripciones masivas
de YouTube, ya sea de videos individuales o canales completos.

Incluye sistema anti-ban multicapa:
  1. Sesión HTTP con huella TLS de Chrome (curl_cffi) + cookies
  2. Delays configurables entre descargas
  3. Pausas largas periódicas
  4. Reintento con backoff + fallback a yt-dlp
  5. Soporte de proxy

Librerías:
  - streamlit              → interfaz web
  - youtube-transcript-api → obtención de transcripciones
  - yt-dlp                 → extracción de listas de canales y metadata
  - curl_cffi (opcional)   → emulación de huella TLS Chrome
"""

import os
import re
import json
import time
import random
import logging
import datetime
import tempfile
from pathlib import Path

import streamlit as st
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    CouldNotRetrieveTranscript,
    IpBlocked,
    RequestBlocked,
)
from youtube_transcript_api.proxies import GenericProxyConfig

try:
    from browser_client import create_browser_session, is_browser_client_available
except ImportError:
    create_browser_session = None  # type: ignore
    is_browser_client_available = lambda: False  # type: ignore

# ─────────────────────────────────────────────
# Configuración global
# ─────────────────────────────────────────────
BASE_DIR = Path("descargas")
LOOSE_VIDEOS_FOLDER = "Videos Sueltos"
ERROR_LOG_FILE = BASE_DIR / "errores.log"

PREFERRED_LANGUAGES = ["es", "en"]

# Valores por defecto para anti-ban
DEFAULT_DELAY_MIN = 3.0
DEFAULT_DELAY_MAX = 7.0
DEFAULT_LONG_PAUSE_EVERY = 10
DEFAULT_LONG_PAUSE_MIN = 15.0
DEFAULT_LONG_PAUSE_MAX = 30.0

# Reintentos en caso de IpBlocked
MAX_RETRIES_IP_BLOCK = 1
BASE_BACKOFF_SECONDS = 10.0


# ─────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────
def clean_filename(name: str) -> str:
    """Elimina caracteres no válidos para nombres de archivo."""
    cleaned = re.sub(r'[<>:"/\\|?*]', "", name)
    cleaned = cleaned.strip(". ")
    return cleaned[:200] if cleaned else "sin_titulo"


def extract_video_id(url: str) -> str | None:
    """Extrae el ID de un video de YouTube a partir de su URL."""
    patterns = [
        r"(?:v=|/v/|youtu\.be/|/embed/)([a-zA-Z0-9_-]{11})",
        r"^([a-zA-Z0-9_-]{11})$",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def is_channel_url(url: str) -> bool:
    """Determina si la URL es de un canal (no un video individual)."""
    channel_patterns = [
        r"youtube\.com/@",
        r"youtube\.com/channel/",
        r"youtube\.com/c/",
        r"youtube\.com/user/",
    ]
    return any(re.search(p, url) for p in channel_patterns)


def log_error(video_url: str, reason: str):
    """Registra un error en errores.log."""
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}]  URL: {video_url}  |  Error: {reason}\n")


# ─────────────────────────────────────────────
# Sesión HTTP y transcripciones
# ─────────────────────────────────────────────
def create_ytt_api(
    proxy_url: str | None = None,
    cookies_path: str | None = None,
    use_browser_client: bool = True,
) -> YouTubeTranscriptApi:
    """
    Crea una instancia de YouTubeTranscriptApi.
    Si curl_cffi está disponible, usa un cliente con huella TLS de Chrome.
    Si no, usa requests.Session con cookies cargadas.
    """
    from http.cookiejar import MozillaCookieJar
    from requests import Session

    http_client = None

    # Intentar crear sesión con huella TLS de Chrome
    if use_browser_client and is_browser_client_available() and create_browser_session:
        try:
            http_client = create_browser_session(
                cookies_path=cookies_path or "",
                proxy_url=proxy_url,
                impersonate="chrome",
            )
        except Exception:
            http_client = None

    # Fallback a requests.Session estándar
    if http_client is None:
        http_client = Session()
        http_client.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
        })
        if cookies_path and os.path.isfile(cookies_path):
            jar = MozillaCookieJar(cookies_path)
            jar.load(ignore_discard=True, ignore_expires=True)
            http_client.cookies = jar

    http_client.headers.setdefault("Accept-Language", "en-US,en;q=0.9,es;q=0.8")

    if proxy_url and proxy_url.strip():
        proxy_config = GenericProxyConfig(
            http_url=proxy_url.strip(),
            https_url=proxy_url.strip(),
        )
        return YouTubeTranscriptApi(proxy_config=proxy_config, http_client=http_client)
    return YouTubeTranscriptApi(http_client=http_client)


def get_transcript_via_api(
    video_id: str,
    ytt_api: YouTubeTranscriptApi,
) -> str:
    """Obtiene transcripción usando youtube-transcript-api."""
    transcript_list = ytt_api.list(video_id)

    transcript = None
    for lang in PREFERRED_LANGUAGES:
        try:
            transcript = transcript_list.find_manually_created_transcript([lang])
            break
        except NoTranscriptFound:
            continue

    if transcript is None:
        for lang in PREFERRED_LANGUAGES:
            try:
                transcript = transcript_list.find_generated_transcript([lang])
                break
            except NoTranscriptFound:
                continue

    if transcript is None:
        available = list(transcript_list)
        if not available:
            raise CouldNotRetrieveTranscript(video_id)
        transcript = available[0]

    segments = transcript.fetch()
    lines = [snippet.text for snippet in segments]
    return "\n".join(lines)


def get_transcript_via_ytdlp(
    video_id: str,
    cookies_path: str | None = None,
) -> str:
    """
    Fallback: usa yt-dlp extract_info para obtener URLs de subtítulos,
    luego los descarga con sesión autenticada.
    """
    from http.cookiejar import MozillaCookieJar
    from requests import Session

    ydl_opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": PREFERRED_LANGUAGES,
        "ignoreerrors": True,
    }
    if cookies_path and os.path.isfile(cookies_path):
        ydl_opts["cookiefile"] = cookies_path

    url = f"https://www.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    subs = info.get("subtitles", {})
    auto_subs = info.get("automatic_captions", {})

    sub_url = None
    for lang in PREFERRED_LANGUAGES:
        for source in [subs, auto_subs]:
            if lang in source:
                for fmt in source[lang]:
                    if fmt.get("ext") == "json3":
                        sub_url = fmt["url"]
                        break
                if sub_url:
                    break
        if sub_url:
            break

    if not sub_url:
        raise Exception("No se encontraron subtítulos disponibles via yt-dlp.")

    # Descargar con sesión autenticada (preferir curl_cffi si disponible)
    dl_session = None
    if is_browser_client_available() and create_browser_session:
        try:
            dl_session = create_browser_session(cookies_path=cookies_path or "")
        except Exception:
            dl_session = None

    if dl_session is None:
        dl_session = Session()
        dl_session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.youtube.com/",
        })
        if cookies_path and os.path.isfile(cookies_path):
            jar = MozillaCookieJar(cookies_path)
            jar.load(ignore_discard=True, ignore_expires=True)
            dl_session.cookies = jar

    resp = dl_session.get(sub_url, timeout=30, headers={"Referer": "https://www.youtube.com/"})
    if resp.status_code != 200:
        raise Exception(f"Error descargando subtítulos: HTTP {resp.status_code}")

    data = resp.json()
    events = data.get("events", [])
    lines = []
    for ev in events:
        for seg in ev.get("segs", []):
            text = seg.get("utf8", "").strip()
            if text and text != "\n":
                lines.append(text)

    if not lines:
        raise Exception("Los subtítulos descargados están vacíos.")

    return "\n".join(lines)


def get_transcript_text(
    video_id: str,
    ytt_api: YouTubeTranscriptApi,
    cookies_path: str | None = None,
    log_callback=None,
) -> str:
    """
    Obtiene la transcripción con sistema multicapa:
    1) youtube-transcript-api (rápido)
    2) Reintento con backoff si IpBlocked
    3) Fallback a yt-dlp si sigue fallando
    """
    try:
        return get_transcript_via_api(video_id, ytt_api)
    except (IpBlocked, RequestBlocked):
        if log_callback:
            log_callback(f"   🚫 IP bloqueada. Esperando {BASE_BACKOFF_SECONDS:.0f}s y reintentando...")
        time.sleep(BASE_BACKOFF_SECONDS + random.uniform(0, 5))
        try:
            return get_transcript_via_api(video_id, ytt_api)
        except (IpBlocked, RequestBlocked) as e:
            if log_callback:
                log_callback("   🔄 Reintento falló. Probando con yt-dlp + cookies...")
            try:
                return get_transcript_via_ytdlp(video_id, cookies_path)
            except Exception as ytdlp_err:
                if log_callback:
                    log_callback(f"   ❌ yt-dlp también falló: {str(ytdlp_err)[:100]}")
                raise e


def save_transcript(
    folder: Path,
    channel_name: str,
    video_title: str,
    video_url: str,
    transcript_text: str,
) -> Path:
    """Guarda la transcripción en un archivo .txt."""
    folder.mkdir(parents=True, exist_ok=True)
    safe_channel = clean_filename(channel_name)
    safe_title = clean_filename(video_title)
    filename = f"{safe_channel} - {safe_title}.txt"
    filepath = folder / filename

    content = (
        f"Título: {video_title}\n"
        f"Canal: {channel_name}\n"
        f"URL: {video_url}\n"
        f"{'─' * 50}\n\n"
        f"{transcript_text}\n"
    )
    filepath.write_text(content, encoding="utf-8")
    return filepath


def get_output_path(folder: Path, channel_name: str, video_title: str) -> Path:
    """Devuelve la ruta esperada del archivo .txt."""
    safe_channel = clean_filename(channel_name)
    safe_title = clean_filename(video_title)
    return folder / f"{safe_channel} - {safe_title}.txt"


# ─────────────────────────────────────────────
# Extracción de metadatos con yt-dlp
# ─────────────────────────────────────────────
def get_single_video_info(url: str) -> dict:
    """Extrae título y canal de un único video usando yt-dlp."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "title": info.get("title", "Sin título"),
        "channel": info.get("channel", info.get("uploader", "Desconocido")),
        "url": info.get("webpage_url", url),
    }


def get_channel_videos(url: str, status_callback=None) -> tuple[str, list[dict]]:
    """Extrae la lista de todos los videos de un canal usando yt-dlp."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
    }
    if "/videos" not in url:
        url = url.rstrip("/") + "/videos"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    channel_name = info.get("channel", info.get("uploader", "Canal Desconocido"))
    entries = info.get("entries", [])

    videos = []
    for entry in entries:
        if entry is None:
            continue
        vid_url = entry.get("url", "")
        if not vid_url:
            vid_url = f"https://www.youtube.com/watch?v={entry.get('id', '')}"
        videos.append({
            "title": entry.get("title", "Sin título"),
            "channel": channel_name,
            "url": vid_url,
        })

    if status_callback:
        status_callback(f"   📋 Canal: {channel_name} — {len(videos)} videos encontrados.")

    return channel_name, videos


# ─────────────────────────────────────────────
# Streamlit — Interfaz Web
# ─────────────────────────────────────────────
def main():
    st.set_page_config(
        page_title="YouTube Transcript Extractor",
        page_icon="📝",
        layout="wide",
    )

    # ── CSS personalizado ──
    st.markdown(
        """
        <style>
        .stApp {background-color: #0e1117;}
        .stat-card {
            background: linear-gradient(145deg, #1a1d24, #22262e);
            padding: 20px; border-radius: 12px; text-align: center;
            border: 1px solid #2d3139;
        }
        .stat-card h4 {color: #a0a5b0; font-size: 0.85rem; margin-bottom: 5px;}
        .stat-card .value {font-size: 1.8rem; font-weight: 700;}
        .stat-card .value.total {color: #60a5fa;}
        .stat-card .value.downloaded {color: #34d399;}
        .stat-card .value.skipped {color: #fbbf24;}
        .stat-card .value.errors {color: #f87171;}
        .console-box {
            background: #0d1117; border: 1px solid #30363d; border-radius: 8px;
            padding: 16px; font-family: 'Cascadia Code', 'Fira Code', monospace;
            font-size: 0.82rem; color: #c9d1d9; max-height: 450px;
            overflow-y: auto; white-space: pre-wrap; line-height: 1.6;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ── Título ──
    st.markdown(
        "<h1 style='text-align:center;'>"
        "📝 <span style='color:#ff4b4b;'>YouTube Transcript Extractor</span>"
        "</h1>"
        "<p style='text-align:center; color:#888;'>"
        "Descarga transcripciones masivas de videos individuales o canales completos"
        "</p>",
        unsafe_allow_html=True,
    )

    # ── Sidebar: configuración anti-ban ──
    with st.sidebar:
        st.header("⚙️ Configuración Anti-Ban")

        # curl_cffi
        browser_client_available = is_browser_client_available()
        use_browser_client = st.checkbox(
            "Usar cliente tipo navegador (curl_cffi)",
            value=True,
            help="Emula la huella TLS/HTTP2 de Chrome para reducir bloqueos 429.",
            disabled=not browser_client_available,
        )
        if not browser_client_available:
            st.caption("⚠️ curl_cffi no instalado. Ejecuta: pip install curl_cffi")

        st.divider()

        # Cookies
        st.subheader("🍪 Cookies (anti IP-block)")
        cookies_file = st.text_input(
            "Ruta a cookies.txt",
            value="cookies.txt",
            help=(
                "Exporta tus cookies de YouTube con 'Get cookies.txt LOCALLY' "
                "y guarda el archivo aquí."
            ),
        )

        st.divider()

        # Delays
        st.subheader("🕐 Delays entre descargas")
        delay_min = st.slider(
            "Delay mínimo (s)", 1.0, 20.0, DEFAULT_DELAY_MIN, 0.5,
            help="Segundos mínimos de espera entre cada descarga.",
        )
        delay_max = st.slider(
            "Delay máximo (s)", 2.0, 30.0, DEFAULT_DELAY_MAX, 0.5,
            help="Segundos máximos de espera entre cada descarga.",
        )
        if delay_max < delay_min:
            delay_max = delay_min

        st.subheader("☕ Pausa larga periódica")
        long_pause_every = st.number_input(
            "Pausa larga cada N videos", 5, 100, DEFAULT_LONG_PAUSE_EVERY, 1,
        )
        long_pause_min = st.slider(
            "Pausa larga mín (s)", 5.0, 60.0, DEFAULT_LONG_PAUSE_MIN, 1.0,
        )
        long_pause_max = st.slider(
            "Pausa larga máx (s)", 10.0, 120.0, DEFAULT_LONG_PAUSE_MAX, 1.0,
        )
        if long_pause_max < long_pause_min:
            long_pause_max = long_pause_min

        st.divider()

        # Proxy
        st.subheader("🌐 Proxy (opcional)")
        proxy_url = st.text_input(
            "URL del proxy",
            placeholder="http://user:pass@proxy.example.com:8080",
            help="Proxy HTTP/HTTPS/SOCKS rotatorio.",
        )

        st.divider()
        st.caption(
            "💡 Si recibes errores de IP bloqueada, exporta tus cookies o usa una VPN."
        )

    # ── Inicializar estado de sesión ──
    if "running" not in st.session_state:
        st.session_state.running = False
    if "stop_requested" not in st.session_state:
        st.session_state.stop_requested = False
    if "log_lines" not in st.session_state:
        st.session_state.log_lines = []
    if "stats" not in st.session_state:
        st.session_state.stats = {
            "total": 0, "downloaded": 0, "skipped": 0, "errors": 0,
        }

    # ── Entrada del usuario ──
    col_input, col_buttons = st.columns([3, 1])

    with col_input:
        url_input = st.text_input(
            "🔗 URL de YouTube",
            placeholder="https://www.youtube.com/@canal  o  https://www.youtube.com/watch?v=...",
        )

    with col_buttons:
        st.markdown("<br>", unsafe_allow_html=True)
        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            start_btn = st.button(
                "▶️ Iniciar", use_container_width=True, type="primary",
                disabled=st.session_state.running,
            )
        with btn_col2:
            stop_btn = st.button(
                "⏹️ Detener", use_container_width=True,
                disabled=not st.session_state.running,
            )

    if stop_btn:
        st.session_state.stop_requested = True

    # ── Tarjetas de estadísticas ──
    s = st.session_state.stats
    c1, c2, c3, c4 = st.columns(4)
    for col, label, key, css in [
        (c1, "📊 Total Videos", "total", "total"),
        (c2, "✅ Descargados", "downloaded", "downloaded"),
        (c3, "⏭️ Saltados", "skipped", "skipped"),
        (c4, "❌ Errores", "errors", "errors"),
    ]:
        col.markdown(
            f"<div class='stat-card'><h4>{label}</h4>"
            f"<div class='value {css}'>{s[key]}</div></div>",
            unsafe_allow_html=True,
        )

    # ── Consola de log ──
    console_placeholder = st.empty()

    def add_log(message: str):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        st.session_state.log_lines.append(f"[{timestamp}] {message}")
        if len(st.session_state.log_lines) > 200:
            st.session_state.log_lines = st.session_state.log_lines[-200:]

    def render_console():
        text = "\n".join(st.session_state.log_lines) if st.session_state.log_lines else "Esperando..."
        console_placeholder.markdown(
            f"<div class='console-box'>{text}</div>", unsafe_allow_html=True,
        )

    render_console()

    # ── Barra de progreso ──
    progress_bar = st.progress(0, text="")

    # ── Footer ──
    st.markdown(
        "<div style='text-align:center; color:#555; margin-top:30px; font-size:0.8rem;'>"
        "YouTube Transcript Extractor · Anti-ban integrado · Reanudación automática"
        "</div>",
        unsafe_allow_html=True,
    )

    # ── Proceso principal ──
    if start_btn and url_input.strip():
        url = url_input.strip()
        st.session_state.running = True
        st.session_state.stop_requested = False
        st.session_state.log_lines = []
        st.session_state.stats = {"total": 0, "downloaded": 0, "skipped": 0, "errors": 0}

        # Determinar cookies efectivas
        effective_cookies = cookies_file if cookies_file and os.path.isfile(cookies_file) else None

        # Crear instancia compartida
        ytt_api = create_ytt_api(
            proxy_url=proxy_url,
            cookies_path=effective_cookies,
            use_browser_client=use_browser_client,
        )

        if use_browser_client and browser_client_available:
            add_log("🔐 Cliente tipo Chrome (curl_cffi) activo.")
        if effective_cookies:
            add_log("🍪 Cookies cargadas desde archivo.")
        if proxy_url and proxy_url.strip():
            add_log("🌐 Usando proxy configurado.")
        add_log(f"⚙️  Delays: {delay_min}-{delay_max}s | Pausa larga cada {long_pause_every} videos")

        try:
            # ─── Determinar tipo de URL ───
            if is_channel_url(url):
                add_log("🔎 URL de canal detectada. Extrayendo lista de videos...")
                render_console()
                channel_name, videos = get_channel_videos(url, status_callback=add_log)
                folder = BASE_DIR / clean_filename(channel_name)
            else:
                add_log("🎬 URL de video individual detectada.")
                render_console()
                try:
                    info = get_single_video_info(url)
                    videos = [info]
                    channel_name = info["channel"]
                    folder = BASE_DIR / LOOSE_VIDEOS_FOLDER
                    add_log(f"   Canal: {channel_name} | Título: {info['title']}")
                except Exception as e:
                    add_log(f"❌ No se pudo obtener info del video: {e}")
                    log_error(url, str(e))
                    render_console()
                    st.session_state.running = False
                    st.rerun()

            total = len(videos)
            st.session_state.stats["total"] = total
            add_log(f"📦 {total} video(s) a procesar. Carpeta: {folder}")
            render_console()

            start_time = time.time()
            downloaded_count = 0

            for idx, video in enumerate(videos, 1):
                if st.session_state.stop_requested:
                    add_log("⏹️ Detenido por el usuario.")
                    render_console()
                    break

                video_title = video["title"]
                video_url = video["url"]
                video_id = extract_video_id(video_url)

                progress_bar.progress(
                    idx / total,
                    text=f"Procesando {idx}/{total}: {video_title[:60]}...",
                )

                # Verificar si ya existe
                out_path = get_output_path(folder, channel_name, video_title)
                if out_path.exists():
                    add_log(f"⏭️ [{idx}/{total}] Ya existe: {video_title[:60]}")
                    st.session_state.stats["skipped"] += 1
                    render_console()
                    continue

                add_log(f"⬇️ [{idx}/{total}] Descargando: {video_title[:60]}...")
                render_console()

                if not video_id:
                    add_log(f"   ⚠️ No se pudo extraer ID del video: {video_url}")
                    log_error(video_url, "No se pudo extraer video ID")
                    st.session_state.stats["errors"] += 1
                    render_console()
                    continue

                try:
                    transcript_text = get_transcript_text(
                        video_id, ytt_api,
                        cookies_path=effective_cookies,
                        log_callback=add_log,
                    )
                    saved_path = save_transcript(
                        folder, channel_name, video_title, video_url, transcript_text
                    )
                    st.session_state.stats["downloaded"] += 1
                    downloaded_count += 1
                    add_log(f"   ✅ Guardado: {saved_path.name}")

                except (IpBlocked, RequestBlocked) as e:
                    add_log(f"   🚫 IP BLOQUEADA — todas las capas fallaron.")
                    add_log(f"   💡 Usa una VPN, espera unas horas, o activa un proxy.")
                    log_error(video_url, f"IpBlocked: {e}")
                    st.session_state.stats["errors"] += 1
                    add_log("   ⛔ Abortando descarga para evitar más bloqueos.")
                    render_console()
                    break  # Fail-fast: no seguir intentando

                except (TranscriptsDisabled, NoTranscriptFound, CouldNotRetrieveTranscript) as e:
                    reason = type(e).__name__
                    add_log(f"   ⚠️ Sin transcripción disponible ({reason})")
                    log_error(video_url, reason)
                    st.session_state.stats["errors"] += 1

                except Exception as e:
                    error_name = type(e).__name__
                    add_log(f"   ❌ Error: {error_name}: {str(e)[:80]}")
                    log_error(video_url, f"{error_name}: {str(e)[:200]}")
                    st.session_state.stats["errors"] += 1

                render_console()

                # Anti-ban: delay entre descargas
                if idx < total and not st.session_state.stop_requested:
                    # Pausa larga periódica
                    if downloaded_count > 0 and downloaded_count % long_pause_every == 0:
                        pause = random.uniform(long_pause_min, long_pause_max)
                        add_log(f"   ☕ Pausa larga de {pause:.0f}s (cada {long_pause_every} videos)...")
                        render_console()
                        time.sleep(pause)
                    else:
                        delay = random.uniform(delay_min, delay_max)
                        time.sleep(delay)

            # ── Resumen final ──
            elapsed = time.time() - start_time
            minutes = int(elapsed // 60)
            seconds = int(elapsed % 60)
            s = st.session_state.stats
            add_log("")
            add_log(f"🏁 Proceso completado en {minutes}m {seconds}s")
            add_log(
                f"   📊 Total: {s['total']} | ✅ Descargados: {s['downloaded']} "
                f"| ⏭️ Saltados: {s['skipped']} | ❌ Errores: {s['errors']}"
            )
            render_console()
            progress_bar.progress(1.0, text="✅ Completado")

        except Exception as e:
            add_log(f"💥 Error fatal: {e}")
            log_error(url, f"FATAL: {e}")
            render_console()

        finally:
            st.session_state.running = False


if __name__ == "__main__":
    main()
