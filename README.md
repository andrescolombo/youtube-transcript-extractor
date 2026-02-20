# YouTube Transcript Extractor

![YouTube Transcript Extractor](https://img.shields.io/badge/Python-3.12-blue.svg)
![Streamlit](https://img.shields.io/badge/Streamlit-App-FF4B4B.svg)
![Status](https://img.shields.io/badge/Status-Active-brightgreen.svg)

A professional, local web application built with Streamlit and Python to bulk-download YouTube transcripts from individual videos or entire channels. Specially designed to bypass YouTube's strict anti-bot measures (HTTP 429 Too Many Requests) using advanced network impersonation techniques.

## Features

- **Bulk Downloading:** Paste a channel URL (`@channel` or `/channel/...`) to automatically list and download all available transcripts.
- **Advanced Anti-Ban System:**
  - **TLS/HTTP2 Impersonation:** Utilizes `curl_cffi` to spoof a genuine Google Chrome network footprint, preventing the `youtube.com/api/timedtext` endpoint from flagging the requests as a bot.
  - **Cookie Authentication:** Supports passing a `cookies.txt` file (Netscape format) to download transcripts using your real, authenticated YouTube session.
  - **Multi-layer Fallback:** Tries `youtube-transcript-api` first. If blocked, retries with exponential backoff. If it fails again, falls back to raw subtitle extraction via `yt-dlp`.
  - **Configurable Delays:** Set random minimum/maximum sleep times between downloads and schedule periodic long pauses.
- **Resilience:** Automatically skips videos that have already been downloaded, allowing you to stop and resume massive channel downloads seamlessly.
- **Clean Output:** Transcripts are saved as clean `.txt` files (text only, no timestamps) organized in `descargas/Channel_Name/`.

---

## 🚀 Installation & Setup

### Prerequisites
- **Python 3.10+** (Developed and tested on Python 3.12)
- Git (optional, for cloning)

### 1. Clone the repository
```bash
git clone https://github.com/andrescolombo/youtube-transcript-extractor.git
cd youtube-transcript-extractor
```

### 2. Install dependencies
It is highly recommended to use a virtual environment.
```bash
pip install -r requirements.txt
```
*(Ensure `curl_cffi` is installed for the TLS impersonation feature to work).*

### 3. Run the application
```bash
streamlit run app.py
```
The app will automatically open in your default web browser at `http://localhost:8501`.

---

## 🛠️ How to Use

1. **Get your YouTube Cookies (Optional but Recommended for bulk):**
   - Install the "Get cookies.txt LOCALLY" extension in your browser.
   - Go to YouTube, ensure you are logged in, and export your cookies.
   - Save the file as `cookies.txt` in the root folder of this project.

2. **Start the App:**
   - Run `streamlit run app.py`.
   - On the left sidebar, ensure the path to your `cookies.txt` is correct and the `curl_cffi` impersonation checkbox is enabled.
   
3. **Extract:**
   - Find a YouTube channel (e.g., `https://www.youtube.com/@Oskar1up`) or a single video link.
   - Paste it into the main input field.
   - Click **▶️ Iniciar** (Start).
   
4. **Monitor:**
   - Watch the live progress bar and the console log. Transcripts will appear instantly inside the `descargas/` folder.

---

## Technical Details: The "IP Block" Problem

When extracting dozens of videos rapidly, YouTube terminates the connection to its subtitle endpoint (`/api/timedtext`) with an `HTTP 429` error, even if proper headers or cookies are used. This happens because the standard Python `requests` library leaves a cryptographic TLS fingerprint (JA3) that YouTube's Anti-Bot system easily identifies.

This application solves this by using **`curl-impersonate`** (via the `curl_cffi` library). By negotiating the TLS handshake exactly like a real Chrome browser, your automated requests become indistinguishable from manual browsing, allowing continuous API usage without triggers.

---

## Dependencies
- `streamlit` - Web UI framework
- `youtube-transcript-api` - Core transcript fetching
- `yt-dlp` - Channel URL extraction and fallback logic
- `curl_cffi` - Browser impersonation (Anti-ban)

## License
MIT License. Feel free to fork and modify.
