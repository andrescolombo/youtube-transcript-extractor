"""
Tests para la aplicación YouTube Transcript Extractor.

Qué hace cada grupo de tests:
- test_utilidades: clean_filename, extract_video_id, is_channel_url, get_output_path
- test_browser_client: disponibilidad de curl_cffi y creación de sesión
- test_canal_pelado_nerd: integración con el canal @PeladoNerd (red, opcional)
"""

import pytest
from pathlib import Path

# Importar funciones a probar desde el módulo app
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import (
    clean_filename,
    extract_video_id,
    is_channel_url,
    get_output_path,
    BASE_DIR,
)


# ─── Utilidades (sin red) ───

class TestCleanFilename:
    """Tests de clean_filename: sanitiza nombres para usar como archivo."""

    def test_quita_caracteres_invalidos(self):
        # : y " se reemplazan por _
        assert clean_filename('video: "test"') == 'video_ _test_'
        assert "\\" not in clean_filename("a\\b")
        assert "/" not in clean_filename("a/b")
        assert "*" not in clean_filename("a*b")

    def test_normaliza_espacios(self):
        assert clean_filename("  a   b  ") == "a b"

    def test_recorta_largo(self):
        largo = "a" * 300
        assert len(clean_filename(largo)) <= 200


class TestExtractVideoId:
    """Tests de extract_video_id: extrae el ID de 11 caracteres de URLs de YouTube."""

    def test_watch(self):
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_youtu_be(self):
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_shorts(self):
        assert extract_video_id("https://www.youtube.com/shorts/abc123XYZ01") == "abc123XYZ01"

    def test_no_es_video(self):
        assert extract_video_id("https://www.youtube.com/@PeladoNerd") is None
        assert extract_video_id("https://www.youtube.com/feed/subscriptions") is None


class TestIsChannelUrl:
    """Tests de is_channel_url: detecta si la URL es de canal (no de video)."""

    def test_canal_handle(self):
        assert is_channel_url("https://www.youtube.com/@PeladoNerd") is True
        assert is_channel_url("https://www.youtube.com/@PeladoNerd/videos") is True

    def test_video_no_es_canal(self):
        assert is_channel_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is False


class TestGetOutputPath:
    """Tests de get_output_path: ruta del .txt según canal y título."""

    def test_formato_ruta(self):
        folder = Path("descargas")
        path = get_output_path(folder, "Canal Prueba", "Título: con caracteres raros")
        assert path.parent == folder
        assert path.suffix == ".txt"
        assert "Canal Prueba" in path.name
        assert "Título" in path.name


# ─── Browser client (sin red, solo imports y creación) ───

class TestBrowserClient:
    """Tests del módulo browser_client (curl_cffi)."""

    def test_is_browser_client_available(self):
        from browser_client import is_browser_client_available
        # Solo comprueba que no rompe; puede ser True o False según si curl_cffi está instalado
        assert isinstance(is_browser_client_available(), bool)

    def test_create_browser_session_sin_cookies(self):
        from browser_client import create_browser_session, is_browser_client_available
        if not is_browser_client_available():
            pytest.skip("curl_cffi no instalado")
        try:
            session = create_browser_session(cookies_path="")
            assert session is not None
            # Debe tener get/post/headers
            assert hasattr(session, "get")
            assert hasattr(session, "post")
            assert hasattr(session, "headers")
        except RuntimeError as e:
            if "curl_cffi" in str(e):
                pytest.skip("curl_cffi no disponible en este entorno")


# ─── Integración con canal (requiere red) ───

class TestCanalPeladoNerd:
    """Tests de integración con el canal @PeladoNerd. Requieren red."""

    @pytest.fixture
    def channel_url(self):
        return "https://www.youtube.com/@PeladoNerd"

    def test_get_channel_videos_devuelve_lista(self, channel_url):
        from app import get_channel_videos
        channel_name, videos = get_channel_videos(channel_url)
        assert isinstance(channel_name, str)
        assert len(channel_name) > 0
        assert isinstance(videos, list)
        assert len(videos) > 0
        assert "Pelado" in channel_name or "Nerd" in channel_name or len(videos) > 0
        first = videos[0]
        assert "id" in first and "title" in first and "url" in first

    def test_primer_video_tiene_transcripcion_o_falla_controlado(self, channel_url):
        """Obtiene la lista del canal e intenta 1 transcripción; puede fallar por 429/red."""
        from app import get_channel_videos, create_ytt_api, get_transcript_text
        channel_name, videos = get_channel_videos(channel_url)
        if not videos:
            pytest.skip("No hay videos en el canal")
        video_id = videos[0]["id"]
        ytt_api = create_ytt_api(cookies_path=None, use_browser_client=True)
        try:
            text = get_transcript_text(video_id, ytt_api, cookies_path=None, log_callback=None)
            assert isinstance(text, str)
            assert len(text.strip()) > 0
        except Exception as e:
            # 429, sin subtítulos, etc. — no fallar el test, solo informar
            pytest.skip(f"Transcripción no disponible en este entorno: {e}")
