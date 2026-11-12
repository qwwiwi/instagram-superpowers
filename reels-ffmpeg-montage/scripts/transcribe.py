#!/usr/bin/env python3
"""Speech to text with word timestamps: the Groq API or a local Whisper.

Usage:
    transcribe.py video.mp4 [--language ru] [--out words.json] [--engine auto|groq|local]

Engines:
    groq   ffmpeg extracts mono 16 kHz audio (FLAC, and MP3 if FLAC is too big), the audio
           goes to the Groq OpenAI-compatible endpoint with model ``whisper-large-v3``.
    local  ffmpeg extracts mono 16 kHz WAV, the optional package ``faster-whisper`` recognises
           it on this computer (no key, the audio never leaves the machine). The first run
           downloads the model weights (``--model``, default ``large-v3-turbo``).
    auto   (default) groq when a Groq key is found, otherwise local when ``faster-whisper``
           is installed, otherwise stop with a message that names both ways.

Both engines write the same ``words.json``.

The key comes from the environment variable ``GROQ_API_KEY`` or from the file
``~/.secrets/groq/env``. It is never printed, logged or written to an output file.

File size limit (groq only): an upload above ``--max-upload-mb`` (default 25) is refused by
the service with HTTP 413. The script first writes FLAC; if that is over the
limit it re-encodes to 32 kbit/s mono MP3 (about 4 KB per second); if even that
is over the limit it stops with a clear message. It does not split a take into
chunks: a reel is short.
"""

from __future__ import annotations

import argparse
import http.client
import importlib.util
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

# Local engine (optional package faster-whisper).
ENGINES = ("auto", "groq", "local")
LOCAL_PACKAGE = "faster_whisper"
DEFAULT_LOCAL_MODEL = "large-v3-turbo"
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE_TYPE = "int8"
LOCAL_BEAM_SIZE = 5
LOCAL_INSTALL = "python3 -m venv .venv && .venv/bin/python -m pip install faster-whisper"
# Rough download sizes of the model weights, for the log of the first run.
LOCAL_MODEL_SIZES = {
    "large-v3-turbo": "about 1.6 GB",
    "turbo": "about 1.6 GB",
    "large-v3": "about 3 GB",
    "medium": "about 1.5 GB",
    "small": "about 0.5 GB",
    "base": "about 0.15 GB",
    "tiny": "about 0.08 GB",
}

Opener = Callable[..., Any]
# Callable with the signature of ``faster_whisper.WhisperModel`` (replaced in tests).
ModelFactory = Callable[..., Any]
# Callable with the signature of ``importlib.util.find_spec`` (replaced in tests).
SpecFinder = Callable[[str], Any]


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


def clean_key(raw: str) -> str:
    """Normalise a key and check that it can travel in an HTTP header.

    Surrounding whitespace and one pair of quotes are removed. What is left must be
    printable ASCII without any whitespace: a line break or a space inside the key
    means a broken copy, and an HTTP library would quote the whole key in its error.

    Args:
        raw: The value as found in the environment or the env file.

    Returns:
        The key.

    Raises:
        MontageError: The key has an inner space, line break, control or non-ASCII character.
            The message never contains the key.
    """
    value = raw.strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    if not value or any(not ch.isascii() or not ch.isprintable() or ch.isspace() for ch in value):
        raise MontageError(
            f"{KEY_ENV} is not a valid key: it must be one line of plain characters with no spaces, line breaks "
            "or non-ASCII letters (the value is not shown). Copy the key from the Groq console again."
        )
    return value


def find_raw_key(environ: Mapping[str, str] | None = None, key_file: Path | None = None) -> str | None:
    """Return the Groq key as found, not yet checked: the environment first, then the env file.

    Args:
        environ: Environment to read (defaults to ``os.environ``).
        key_file: Env file to read (defaults to ``~/.secrets/groq/env``).

    Returns:
        The raw value, or None when there is no key. The caller must never print it.
    """
    env = os.environ if environ is None else environ
    value = (env.get(KEY_ENV) or "").strip()
    if value:
        return value
    return _read_env_file((key_file or KEY_FILE).expanduser())


def load_api_key(environ: Mapping[str, str] | None = None, key_file: Path | None = None) -> str:
    """Find the Groq key: the environment first, then the env file.

    Args:
        environ: Environment to read (defaults to ``os.environ``).
        key_file: Env file to read (defaults to ``~/.secrets/groq/env``).

    Returns:
        The key. The caller must never print it.

    Raises:
        MontageError: No key was found, or the key is malformed.
    """
    raw = find_raw_key(environ, key_file)
    if raw:
        return clean_key(raw)
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
        self._key = clean_key(key)
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
            try:
                error_body = exc.read() if exc.fp else b""
            except (http.client.HTTPException, OSError):
                error_body = b""
            detail = _error_text(error_body, self._key)
            retry = exc.headers.get("Retry-After") if exc.headers else None
            raise GroqError(explain_http_error(exc.code, detail, retry)) from None
        except urllib.error.URLError as exc:
            raise GroqError(f"Cannot reach Groq: {redact(str(exc.reason), self._key)}") from None
        except TimeoutError:
            raise GroqError("Groq did not answer in time; try again") from None
        except (http.client.HTTPException, OSError):
            # A cut-off or reset answer (IncompleteRead, connection reset): nothing to show but the advice.
            raise GroqError("The answer from Groq was cut off or the connection broke; try again "
                            "(if it keeps happening, check the network and the size of the audio)") from None
        except ValueError:
            # http.client puts the rejected header value into this message, and the value holds the key.
            raise GroqError(f"The request was refused before it was sent; check that {KEY_ENV} is a single line "
                            "with no spaces") from None
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


# --- engine choice ----------------------------------------------------------------------------------------


def local_engine_available(find_spec: SpecFinder | None = None) -> bool:
    """Tell whether ``faster-whisper`` can be imported, without importing it.

    Args:
        find_spec: Replacement of ``importlib.util.find_spec`` (tests).
    """
    finder = find_spec or importlib.util.find_spec
    try:
        return finder(LOCAL_PACKAGE) is not None
    except (ImportError, ValueError):
        return False


def local_missing_message() -> str:
    """Message for a missing ``faster-whisper``: how to install it into a venv."""
    return (
        f"The local engine needs the Python package faster-whisper, and this Python ({sys.executable}) "
        f"does not have it. Install it into a virtual environment: `{LOCAL_INSTALL}` "
        "and run this script with `.venv/bin/python`."
    )


def no_engine_message() -> str:
    """Message for ``--engine auto`` when there is neither a Groq key nor the local engine."""
    return (
        "No speech recognition engine is available. Choose one of two ways. "
        f"1) Groq (fast, needs a key, the audio is sent to Groq): set the environment variable {KEY_ENV}, "
        f"or put the line `export {KEY_ENV}=\"<your key>\"` into {KEY_FILE} (chmod 600); "
        "the key is created in the Groq console. "
        "2) Local Whisper (no key, the audio stays on this computer, slower): install it into a virtual "
        f"environment: `{LOCAL_INSTALL}` and run this script with `.venv/bin/python`."
    )


def choose_engine(requested: str, environ: Mapping[str, str] | None = None, key_file: Path | None = None,
                  find_spec: SpecFinder | None = None) -> tuple[str, str | None]:
    """Resolve ``--engine`` to the engine that will run.

    Args:
        requested: ``auto``, ``groq`` or ``local``.
        environ: Environment to read the key from (defaults to ``os.environ``).
        key_file: Env file with the key (defaults to ``~/.secrets/groq/env``).
        find_spec: Replacement of ``importlib.util.find_spec`` (tests).

    Returns:
        ``(engine, key)``: ``("groq", key)`` or ``("local", None)``.

    Raises:
        MontageError: The requested engine cannot run; the message says how to fix it.
    """
    if requested == "groq":
        return "groq", load_api_key(environ, key_file)
    if requested == "local":
        if not local_engine_available(find_spec):
            raise MontageError(local_missing_message())
        return "local", None
    if requested != "auto":
        raise MontageError(f"Unknown engine {requested!r}; use one of: {', '.join(ENGINES)}")
    raw = find_raw_key(environ, key_file)
    if raw:
        return "groq", clean_key(raw)
    if local_engine_available(find_spec):
        LOG.info("no Groq key found: using the local engine (faster-whisper)")
        return "local", None
    raise MontageError(no_engine_message())


# --- local engine -----------------------------------------------------------------------------------------


def extract_local_audio(video: Path, workdir: Path) -> Path:
    """Extract mono 16 kHz WAV for the local engine (no size limit, nothing is uploaded).

    Raises:
        MontageError: The video has no audio stream.
    """
    if not probe(video).has_audio:
        raise MontageError(f"{video} has no audio stream")
    wav = workdir / "audio.wav"
    run_ffmpeg(["-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
               "audio extraction")
    return wav


def _default_model_factory() -> ModelFactory:
    """Import ``faster_whisper.WhisperModel`` only when the local engine really runs."""
    try:
        from faster_whisper import WhisperModel  # noqa: PLC0415 - optional dependency, imported lazily
    except ImportError as exc:
        raise MontageError(f"{local_missing_message()} (import failed: {exc})") from None
    return WhisperModel


def transcribe_local(audio: Path, language: str | None, model_name: str = DEFAULT_LOCAL_MODEL,
                     device: str = DEFAULT_DEVICE, compute_type: str = DEFAULT_COMPUTE_TYPE,
                     model_dir: Path | None = None,
                     model_factory: ModelFactory | None = None) -> tuple[list[dict[str, Any]], str]:
    """Recognise speech on this computer with faster-whisper.

    Args:
        audio: Mono 16 kHz audio file.
        language: ISO-639-1 code (``ru``), or None to auto-detect.
        model_name: Whisper model name or a path to a converted model.
        device: ``cpu``, ``cuda`` or ``auto``.
        compute_type: Quantisation such as ``int8``, ``float16``, ``int8_float16``.
        model_dir: Where to download the weights (``download_root``); None keeps the Hugging Face cache.
        model_factory: Replacement of ``faster_whisper.WhisperModel`` (tests).

    Returns:
        ``(raw words in the shape parse_words reads, language)``.

    Raises:
        MontageError: The model cannot be loaded or the recognition fails.
    """
    factory = model_factory or _default_model_factory()
    size = LOCAL_MODEL_SIZES.get(model_name, "size depends on the model")
    where = str(model_dir) if model_dir else "the Hugging Face cache (HF_HOME, by default ~/.cache/huggingface)"
    LOG.info("local model %s on %s (%s); the first run downloads the weights (%s) into %s, later runs reuse them",
             model_name, device, compute_type, size, where)
    try:
        model = factory(model_name, device=device, compute_type=compute_type,
                        download_root=str(model_dir) if model_dir else None)
    except Exception as exc:  # noqa: BLE001 - the library raises many types (network, ctranslate2, value)
        raise MontageError(
            f"Cannot load the local model {model_name!r}: {exc}. The first run downloads it from Hugging Face: "
            "check the network, the model name (--model), --device and --compute-type."
        ) from None
    raw: list[dict[str, Any]] = []
    try:
        segments, info = model.transcribe(str(audio), language=language, word_timestamps=True,
                                          beam_size=LOCAL_BEAM_SIZE)
        # Segments come from a generator: recognition happens while iterating.
        for segment in segments:
            for word in getattr(segment, "words", None) or []:
                text = str(word.word).strip()
                if not text:
                    continue
                raw.append({"word": text, "start": float(word.start), "end": float(word.end)})
    except Exception as exc:  # noqa: BLE001 - see above
        raise MontageError(f"Local recognition failed: {exc}") from None
    detected = getattr(info, "language", None)
    return raw, language or (detected if isinstance(detected, str) else "")


def words_from_local(raw: list[dict[str, Any]], language: str, media_duration: float) -> tuple[WordsFile, int]:
    """Turn the local engine's words into a words file, the same way as the Groq answer.

    Returns:
        ``(words file, number of dropped words)``.
    """
    clean, dropped = sanitize_words(parse_words(raw), media_duration)
    return WordsFile(words=clean, duration=media_duration, language=language), dropped


# --- CLI ---------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Command line of ``transcribe.py``."""
    parser = argparse.ArgumentParser(
        description="Word-level transcript of a video through the Groq API or a local Whisper (faster-whisper)")
    parser.add_argument("video", type=Path, help="input video")
    parser.add_argument("--language", default=None, help="spoken language, ISO-639-1 code such as ru (default: auto-detect)")
    parser.add_argument("--out", type=Path, default=Path("words.json"), help="output file (default ./words.json)")
    parser.add_argument("--engine", choices=ENGINES, default="auto",
                        help="groq: Groq API, needs a key, the audio is sent to Groq; local: faster-whisper on this "
                             "computer, no key; auto (default): groq when a key is found, else local when "
                             "faster-whisper is installed")
    parser.add_argument("--max-upload-mb", type=float, default=DEFAULT_MAX_UPLOAD_MB,
                        help=f"groq only: upload size limit in MB (default {DEFAULT_MAX_UPLOAD_MB})")
    local = parser.add_argument_group("local engine (faster-whisper)")
    local.add_argument("--model", default=DEFAULT_LOCAL_MODEL,
                       help=f"Whisper model name or path (default {DEFAULT_LOCAL_MODEL}; small is lighter and less accurate)")
    local.add_argument("--device", default=DEFAULT_DEVICE, help=f"cpu, cuda or auto (default {DEFAULT_DEVICE})")
    local.add_argument("--compute-type", default=DEFAULT_COMPUTE_TYPE,
                       help=f"quantisation such as int8, float16, int8_float16 (default {DEFAULT_COMPUTE_TYPE})")
    local.add_argument("--model-dir", type=Path, default=None,
                       help="folder for the model weights (default: the Hugging Face cache, set by HF_HOME)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def run(args: argparse.Namespace, opener: Opener | None = None, environ: Mapping[str, str] | None = None,
        key_file: Path | None = None, model_factory: ModelFactory | None = None,
        find_spec: SpecFinder | None = None) -> int:
    """Transcribe a video with the chosen engine and write ``words.json``.

    Args:
        args: Parsed command line.
        opener: Replacement of ``urllib.request.urlopen`` for the Groq engine (tests).
        environ: Environment to read the Groq key from (defaults to ``os.environ``).
        key_file: Env file with the Groq key (defaults to ``~/.secrets/groq/env``).
        model_factory: Replacement of ``faster_whisper.WhisperModel`` for the local engine (tests).
        find_spec: Replacement of ``importlib.util.find_spec`` used to detect faster-whisper (tests).

    Returns:
        The exit code.

    Raises:
        MontageError: Bad input, no engine available, or the recognition failed.
    """
    video: Path = args.video.expanduser().resolve()
    if not video.is_file():
        raise MontageError(f"File not found: {video}")
    engine, key = choose_engine(args.engine, environ, key_file, find_spec)
    info = probe(video)
    with tempfile.TemporaryDirectory(prefix="reels-audio-") as tmp:
        if engine == "groq":
            LOG.info("engine: groq (%s), the audio is sent to Groq", MODEL)
            audio, mime = extract_audio(video, Path(tmp), int(args.max_upload_mb * 1_000_000))
            data = GroqClient(key or "", opener).transcribe(audio, mime, args.language)
            wf, dropped = words_from_response(data, info.duration)
        else:
            LOG.info("engine: local (faster-whisper), the audio stays on this computer")
            wav = extract_local_audio(video, Path(tmp))
            model_dir = args.model_dir.expanduser() if args.model_dir else None
            raw, language = transcribe_local(wav, args.language, model_name=args.model, device=args.device,
                                             compute_type=args.compute_type, model_dir=model_dir,
                                             model_factory=model_factory)
            wf, dropped = words_from_local(raw, language, info.duration)
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
