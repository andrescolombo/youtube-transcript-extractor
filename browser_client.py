"""
Cliente HTTP con huella TLS/HTTP2 de Chrome (curl_cffi).
Permite evitar el bloqueo 429 de YouTube en el endpoint timedtext al emular
el fingerprint criptográfico que usa el navegador real.

Uso: pasar la sesión devuelta por create_browser_session() como http_client
a YouTubeTranscriptApi, o usarla para GET al timedtext en el fallback yt-dlp.
"""

from __future__ import annotations

import os
from typing import Any

# Compatibilidad: si curl_cffi no está instalado, create_browser_session devuelve None
_CURL_CFFI_AVAILABLE = False
try:
    from curl_cffi.requests import Session as CurlSession
    from curl_cffi.requests import Response as CurlResponse
    from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError
    _CURL_CFFI_AVAILABLE = True
except ImportError:
    CurlSession = None  # type: ignore
    CurlResponse = None  # type: ignore
    CurlHTTPError = None  # type: ignore

# youtube-transcript-api espera requests.HTTPError en raise_for_status()
try:
    from requests.exceptions import HTTPError as RequestsHTTPError
except ImportError:
    RequestsHTTPError = Exception  # type: ignore


# Headers que suele enviar Chrome y que YouTube puede comprobar
CHROME_LIKE_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
# Para peticiones a API/recursos (timedtext, innertube)
CHROME_LIKE_HEADERS_XHR = {
    "Accept": "text/xml, application/xml, */*",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


def _load_netscape_cookies_into_curl_session(session: Any, cookies_path: str) -> None:
    """Carga cookies en formato Netscape desde cookies_path en la sesión curl_cffi."""
    from http.cookiejar import MozillaCookieJar

    if not os.path.isfile(cookies_path):
        return
    jar = MozillaCookieJar(cookies_path)
    jar.load(ignore_discard=True, ignore_expires=True)
    for cookie in jar:
        try:
            # curl_cffi usa CookieJar; podemos añadir cookies estándar de Python
            session.cookies.jar.set_cookie(cookie)
        except Exception:
            session.cookies.set(
                cookie.name,
                cookie.value or "",
                domain=cookie.domain or "",
                path=cookie.path or "/",
                secure=bool(cookie.secure),
            )


def _wrap_response(raw: Any) -> Any:
    """
    Envuelve la respuesta de curl_cffi para que raise_for_status() lance
    requests.exceptions.HTTPError, que es lo que espera youtube-transcript-api.
    """
    if raw is None:
        return raw

    class ResponseWrapper:
        __slots__ = ("_raw",)

        def __init__(self, r: Any) -> None:
            self._raw = r

        @property
        def text(self) -> str:
            return self._raw.text

        @property
        def content(self) -> bytes:
            return self._raw.content

        def json(self, **kwargs: Any) -> Any:
            return self._raw.json(**kwargs)

        @property
        def status_code(self) -> int:
            return self._raw.status_code

        @property
        def headers(self) -> Any:
            return self._raw.headers

        @property
        def url(self) -> str:
            return getattr(self._raw, "url", "")

        def raise_for_status(self) -> None:
            try:
                self._raw.raise_for_status()
            except CurlHTTPError as e:
                if CurlHTTPError and _CURL_CFFI_AVAILABLE:
                    # Re-lanzar como requests.HTTPError para compatibilidad
                    class _FakeResponse:
                        status_code = self._raw.status_code
                        reason = getattr(self._raw, "reason", "")
                        text = getattr(self._raw, "text", "")

                    raise RequestsHTTPError(
                        str(e) or f"HTTP Error {self._raw.status_code}",
                        response=_FakeResponse(),
                    ) from e
                raise

    return ResponseWrapper(raw)


class _BrowserLikeSession:
    """
    Wrapper sobre curl_cffi.requests.Session que:
    - Usa impersonate="chrome" en cada petición (TLS + HTTP/2 como Chrome).
    - Devuelve respuestas que lanzan requests.HTTPError en raise_for_status().
    - Expone .headers y .cookies y .proxies como requests.Session.
    """

    def __init__(
        self,
        impersonate: str = "chrome",
        cookies_path: str | None = None,
        proxy_url: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        if not _CURL_CFFI_AVAILABLE or CurlSession is None:
            raise RuntimeError(
                "curl_cffi no está instalado. Ejecuta: pip install curl_cffi"
            )
        self._session = CurlSession(impersonate=impersonate)
        self._impersonate = impersonate
        self._session.headers.update(CHROME_LIKE_HEADERS)
        if extra_headers:
            self._session.headers.update(extra_headers)

        if cookies_path:
            _load_netscape_cookies_into_curl_session(self._session, cookies_path)

        if proxy_url and proxy_url.strip():
            self._session.proxies = {
                "http": proxy_url.strip(),
                "https": proxy_url.strip(),
            }

        # Atributos que youtube-transcript-api puede tocar
        self.headers = self._session.headers
        self.cookies = self._session.cookies
        self.proxies = getattr(self._session, "proxies", {})

    def get(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", 30)
        r = self._session.get(url, impersonate=self._impersonate, **kwargs)
        return _wrap_response(r)

    def post(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", 30)
        r = self._session.post(url, impersonate=self._impersonate, **kwargs)
        return _wrap_response(r)


def create_browser_session(
    cookies_path: str | None = None,
    proxy_url: str | None = None,
    impersonate: str = "chrome",
) -> _BrowserLikeSession | None:
    """
    Crea una sesión HTTP que emula Chrome (TLS/JA3 + HTTP/2).
    Compatible con la interfaz que usa youtube-transcript-api (get, post, headers, cookies).
    Si curl_cffi no está instalado, devuelve None.
    """
    if not _CURL_CFFI_AVAILABLE:
        return None
    try:
        return _BrowserLikeSession(
            impersonate=impersonate,
            cookies_path=cookies_path or "",
            proxy_url=proxy_url,
        )
    except Exception:
        return None


def is_browser_client_available() -> bool:
    """Indica si curl_cffi está disponible para usar el cliente tipo navegador."""
    return _CURL_CFFI_AVAILABLE
