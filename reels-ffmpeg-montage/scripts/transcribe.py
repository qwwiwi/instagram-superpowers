#!/usr/bin/env python3
"""Speech to text with word timestamps through the Groq API.

Usage:
    transcribe.py video.mp4 [--language ru] [--out words.json]

Steps: ffmpeg extracts mono 16 kHz audio (FLAC, and MP3 if FLAC is too big),
the audio goes to the Groq OpenAI-compatible endpoint with model
``whisper-large-v3``, the answer is saved as ``words.json``.

The key comes from the environment variable ``GROQ_API_KEY`` or from the file
``~/.secrets/groq/env``. It is never printed, logged or written to an output file.

File size limit: an upload above ``--max-upload-mb`` (default 25) is refused by
the service with HTTP 413. The script first writes FLAC; if that is over the
limit it re-encodes to 32 kbit/s mono MP3 (about 4 KB per second); if even that
is over the limit it stops with a clear message. It does not split a take into
chunks: a reel is short.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from common import (
    MontageError,
    WordsFile,
    parse_words,
    probe,
    run_ffmpeg,
    sanitize_words,
    save_words,
    setup_logging,
)

LOG = logging.getLogger("reels.transcribe")

API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3"
KEY_ENV = "GROQ_API_KEY"
KEY_FILE = Path("~/.secrets/groq/env")
# Conservative size limit of one upload in megabytes (override with --max-upload-mb).
DEFAULT_MAX_UPLOAD_MB = 25
HTTP_TIMEOUT = 180.0
USER_AGENT = "reels-ffmpeg-montage/1.0"

Opener = Callable[..., Any]


class GroqError(MontageError):
    """The Groq API answered with an error or could not be reached."""


# --- key -------------------------------------------------------------------------------------------------


def _read_env_file(path: Path) -> str | None:
    """Return GROQ_API_KEY from a shell-style env file, or None."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    pattern = re.compile(rf"^\s*(?:export\s+)?{KEY_ENV}\s*=\s*(.*?)\s*$")
    value: str | None = None
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            raw = match.group(1)
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
                raw = raw[1:-1]
            value = raw.strip() or None
    return value


def load_api_key(environ: Mapping[str, str] | None = None, key_file: Path | None = None) -> str:
    """Find the Groq key: the environment first, then the env file.

    Args:
        environ: Environment to read (defaults to ``os.environ``).
        key_file: Env file to read (defaults to ``~/.secrets/groq/env``).

    Returns:
        The key. The caller must never print it.

    Raises:
        MontageError: No key was found.
    """
    env = os.environ if environ is None else environ
    value = (env.get(KEY_ENV) or "").strip()
    if value:
        return value
    path = (key_file or KEY_FILE).expanduser()
    from_file = _read_env_file(path)
    if from_file:
        return from_file
    raise MontageError(
        f"No Groq key. Set the environment variable {KEY_ENV}, or put the line "
        f"`export {KEY_ENV}=\"<your key>\"` into {KEY_FILE} (chmod 600). "
        "The key is created in the Groq console."
    )


def redact(text: str, key: str) -> str:
    """Remove the key from a text before it goes to a log or an error."""
    return text.replace(key, "<key>") if key else text


# --- audio ------------------------------------------------------------------------------------------------


def extract_audio(video: Path, workdir: Path, max_bytes: int) -> tuple[Path, str]:
    """Extract mono 16 kHz audio, compressing it when it is over the size limit.

    Args:
        video: Input video.
        workdir: Folder for the temporary audio.
        max_bytes: Largest upload accepted.

    Returns:
        ``(audio file, mime type)``.

    Raises:
        MontageError: The video has no audio, or the audio is over the limit even compressed.
    """
    info = probe(video)
    if not info.has_audio:
        raise MontageError(f"{video} has no audio stream")
    flac = workdir / "audio.flac"
    run_ffmpeg(["-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac", str(flac)], "audio extraction")
    if flac.stat().st_size <= max_bytes:
        return flac, "audio/flac"
    LOG.info("FLAC is %.1f MB, over the %.0f MB limit: re-encoding to 32 kbit/s MP3",
             flac.stat().st_size / 1e6, max_bytes / 1e6)
    mp3 = workdir / "audio.mp3"
    run_ffmpeg(["-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k", str(mp3)],
               "audio compression")
    if mp3.stat().st_size > max_bytes:
        raise MontageError(
            f"Even compressed the audio is {mp3.stat().st_size / 1e6:.1f} MB, over the {max_bytes / 1e6:.0f} MB limit. "
            "Cut the video into shorter takes, or raise --max-upload-mb if your account allows bigger files."
        )
    return mp3, "audio/mpeg"


# --- HTTP client ------------------------------------------------------------------------------------------


def encode_multipart(
    fields: list[tuple[str, str]], file_field: str, filename: str, content: bytes, mime: str
) -> tuple[bytes, str]:
    """Build a multipart/form-data body.

    Returns:
        ``(body, content type header value)``.
    """
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    parts.append(
        (f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
         f"Content-Type: {mime}\r\n\r\n").encode("utf-8") + content + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _error_text(raw: bytes, key: str) -> str:
    """Message of an API error body, with the key removed."""
    text = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
        message = data.get("error", {}).get("message") if isinstance(data, dict) else None
        if isinstance(message, str):
            text = message
    except ValueError:
        pass
    return redact(text.strip(), key)[:300]


def explain_http_error(status: int, detail: str, retry_after: str | None) -> str:
    """Human message for an HTTP error status of the transcription endpoint."""
    if status == 401:
        return ("Groq rejected the key (401). Check that GROQ_API_KEY is the key from the Groq console, "
                "has no extra spaces, and has not been revoked.")
    if status == 413:
        return ("Groq says the file is too large (413). The audio is already compressed; "
                "use a shorter take, or check the file limit of your account.")
    if status == 429:
        wait = f" Retry after {retry_after} s." if retry_after else " Wait a minute and run again."
        return f"Groq rate limit or quota reached (429).{wait}"
    return f"Groq answered HTTP {status}: {detail}"


class GroqClient:
    """Minimal client of the Groq transcription endpoint.

    Args:
        key: API key. Never logged and never put in an exception.
        opener: Callable with the signature of ``urllib.request.urlopen`` (replaced in tests).
        url: Endpoint address.
    """

    def __init__(self, key: str, opener: Opener | None = None, url: str = API_URL) -> None:
        self._key = key
        self._opener = opener or urllib.request.urlopen
        self._url = url

    def transcribe(self, audio: Path, mime: str, language: str | None = None) -> dict[str, Any]:
        """Send audio and return the decoded ``verbose_json`` answer.

        Args:
            audio: Audio file.
            mime: Its MIME type.
            language: Spoken language as an ISO-639-1 code (``ru``), or None to auto-detect.

        Raises:
            GroqError: HTTP error (401, 413, 429, other), network failure or a bad answer.
        """
        fields = [("model", MODEL), ("response_format", "verbose_json"), ("timestamp_granularities[]", "word")]
        if language:
            fields.append(("language", language))
        body, content_type = encode_multipart(fields, "file", audio.name, audio.read_bytes(), mime)
        request = urllib.request.Request(
            self._url, data=body, method="POST",
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": content_type, "User-Agent": USER_AGENT},
        )
        LOG.info("sending %.1f MB to Groq (%s)", len(body) / 1e6, MODEL)
        try:
            with self._opener(request, timeout=HTTP_TIMEOUT) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = _error_text(exc.read() if exc.fp else b"", self._key)
            retry = exc.headers.get("Retry-After") if exc.headers else None
            raise GroqError(explain_http_error(exc.code, detail, retry)) from None
        except urllib.error.URLError as exc:
            raise GroqError(f"Cannot reach Groq: {redact(str(exc.reason), self._key)}") from None
        except TimeoutError:
            raise GroqError("Groq did not answer in time; try again") from None
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise GroqError("Groq returned an answer that is not JSON") from None
        if not isinstance(data, dict):
            raise GroqError("Groq returned an unexpected answer")
        return data


def words_from_response(data: dict[str, Any], media_duration: float) -> tuple[WordsFile, int]:
    """Turn a ``verbose_json`` answer into a words file.

    Args:
        data: Decoded answer.
        media_duration: Length of the video, seconds.

    Returns:
        ``(words file, number of dropped words)``.

    Raises:
        GroqError: The answer holds no word timestamps.
    """
    raw_words = data.get("words")
    if not isinstance(raw_words, list):
        raise GroqError("The answer has no word timestamps (`words`). Is the model name right and the file speech?")
    try:
        words = parse_words(raw_words)
    except MontageError as exc:
        raise GroqError(f"Unexpected word format in the answer: {exc}") from None
    clean, dropped = sanitize_words(words, media_duration)
    language = data.get("language")
    return WordsFile(words=clean, duration=media_duration, language=language if isinstance(language, str) else ""), dropped


# --- CLI ---------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Command line of ``transcribe.py``."""
    parser = argparse.ArgumentParser(description="Word-level transcript of a video through the Groq API")
    parser.add_argument("video", type=Path, help="input video")
    parser.add_argument("--language", default=None, help="spoken language, ISO-639-1 code such as ru (default: auto-detect)")
    parser.add_argument("--out", type=Path, default=Path("words.json"), help="output file (default ./words.json)")
    parser.add_argument("--max-upload-mb", type=float, default=DEFAULT_MAX_UPLOAD_MB,
                        help=f"upload size limit in MB (default {DEFAULT_MAX_UPLOAD_MB})")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def run(args: argparse.Namespace, opener: Opener | None = None, environ: Mapping[str, str] | None = None,
        key_file: Path | None = None) -> int:
    """Transcribe a video. Returns the exit code."""
    video: Path = args.video.expanduser().resolve()
    if not video.is_file():
        raise MontageError(f"File not found: {video}")
    key = load_api_key(environ, key_file)
    info = probe(video)
    with tempfile.TemporaryDirectory(prefix="reels-audio-") as tmp:
        audio, mime = extract_audio(video, Path(tmp), int(args.max_upload_mb * 1_000_000))
        data = GroqClient(key, opener).transcribe(audio, mime, args.language)
    wf, dropped = words_from_response(data, info.duration)
    wf.source = str(video)
    if args.language and not wf.language:
        wf.language = args.language
    if not wf.words:
        raise MontageError("No words were recognised. Is there speech in the video, and is the language right?")
    save_words(args.out, wf)
    if dropped:
        LOG.info("dropped %d invented word(s) (empty or beyond the end of the video)", dropped)
    print(f"{len(wf.words)} words, {info.duration:.1f} s -> {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run."""
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        return run(args)
    except MontageError as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
