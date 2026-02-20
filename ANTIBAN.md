# Evitar bloqueos 429 (timedtext) en YouTube

## Qué pasa

YouTube devuelve **HTTP 429 (Too Many Requests)** en el endpoint de subtítulos (`timedtext`) cuando detecta tráfico que **no** parece un navegador real, aunque uses cookies válidas. Eso suele deberse a:

1. **Huella TLS (JA3/JA3S)**  
   `requests` (y por tanto `youtube-transcript-api`/`yt-dlp` por defecto) usan OpenSSL. El handshake TLS que generan es distinto al de Chrome. YouTube (y otros) pueden usar ese fingerprint para marcar el tráfico como bot.

2. **HTTP/2 y orden de cabeceras**  
   Chrome usa HTTP/2 por defecto y un orden/estilo de cabeceras concreto. Un cliente que use HTTP/1.1 o cabeceras distintas se distingue fácilmente.

3. **Cabeceras que faltan**  
   El navegador envía `Sec-CH-UA`, `Sec-Fetch-*`, `Referer`, etc. Si el script no las envía o las envía mal, el patrón de petición no se parece al de una pestaña real.

Por eso **en el navegador** (misma IP y cookies) los subtítulos cargan y **en el script** empiezan a salir 429.

## Solución aplicada: cliente tipo Chrome (curl_cffi)

La app usa **curl_cffi** cuando está instalado (`pip install curl_cffi`). Eso te da:

- **Mismo fingerprint TLS que Chrome** (vía curl-impersonate): mismo JA3/JA3S que el navegador.
- **HTTP/2** y cabeceras alineadas con Chrome.
- Misma interfaz que `requests` (`.get()`, `.post()`, cookies, etc.), así que se integra con `youtube-transcript-api` y con el fallback por `yt-dlp` sin cambiar la lógica de negocio.

### Dónde se usa

1. **YouTube Transcript API**  
   La sesión que se pasa como `http_client` a `YouTubeTranscriptApi` es, si hay `curl_cffi`, una sesión creada con `create_browser_session()` en `browser_client.py`. Con eso, tanto la petición a la página del video como la al API innertube y **la petición al `timedtext`** van con huella de Chrome.

2. **Fallback yt-dlp**  
   Cuando la API falla y se usa yt-dlp para obtener la URL del subtítulo (json3), la **descarga de esa URL** se hace también con la sesión tipo Chrome (y `Referer: https://www.youtube.com/`), no con `requests` a pelo.

### Cómo activarlo

- Instalar: `pip install curl_cffi`
- En la app: en el sidebar, dejar marcado **"Usar cliente tipo navegador (curl_cffi)"** (por defecto activado si el paquete está instalado).

Si `curl_cffi` no está instalado, la app sigue funcionando con `requests` y tus cookies; solo es más fácil que aparezcan 429 en descargas masivas.

### Opcional: Playwright/Selenium

Si aun con curl_cffi sigues teniendo bloqueos, la opción más pesada pero más fiel es usar un navegador real (Playwright o Selenium) en modo headless y hacer que **ese** navegador cargue la página del video (o la URL del timedtext) y te devuelva el texto. Ahí la huella es la del navegador real. La arquitectura actual no incluye Playwright; si quieres ir por ahí, el flujo sería: para cada video, abrir la URL en el navegador, esperar a que carguen los subtítulos (o inyectar JS para pedir el timedtext) y leer el DOM o la respuesta. Es más lento y consume más recursos, pero es la opción “navegador real” que comentas.

## Resumen

- **Problema:** 429 en timedtext por detección de bot (TLS/cabeceras/HTTP2).
- **Solución en esta app:** usar **curl_cffi** como cliente HTTP (sesión tipo Chrome) para la API de transcripciones y para la descarga del timedtext en el fallback yt-dlp.
- **Qué hacer:** `pip install curl_cffi` y tener activada la opción “Usar cliente tipo navegador” en el sidebar.
