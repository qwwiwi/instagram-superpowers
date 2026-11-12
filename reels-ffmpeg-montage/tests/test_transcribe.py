"""Groq client with a mocked HTTP layer: success, 401, 413, 429, and the key never leaks."""

from __future__ import annotations

import io
import json
import logging
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

import transcribe
from common import MontageError
from transcribe import (
    GroqClient,
    GroqError,
    encode_multipart,
    load_api_key,
    redact,
    words_from_response,
)

SECRET = "gsk_TESTKEY_do_not_leak_0123456789"

GOOD_ANSWER = {
    "text": "привет мир",
    "language": "russian",
    "duration": 3.0,
    "words": [
        {"word": "привет", "start": 0.5, "end": 1.0},
        {"word": "мир", "start": 1.1, "end": 1.5},
        {"word": "призрак", "start": 40.0, "end": 40.5},  # invented beyond the end
    ],
}


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class Recorder:
    """Stands in for urllib.request.urlopen and remembers the request."""

    def __init__(self, answer: bytes | Exception) -> None:
        self.answer = answer
        self.request: Any = None
        self.timeout: float | None = None

    def __call__(self, request: Any, timeout: float | None = None) -> FakeResponse:
        self.request, self.timeout = request, timeout
        if isinstance(self.answer, Exception):
            raise self.answer
        return FakeResponse(self.answer)


def http_error(status: int, body: str, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError("https://api.example/x", status, "err", msg, io.BytesIO(body.encode("utf-8")))


@pytest.fixture()
def audio(tmp_path: Path) -> Path:
    path = tmp_path / "audio.flac"
    path.write_bytes(b"fLaC" + b"\x00" * 64)
    return path


def test_success_sends_the_documented_fields_and_returns_the_answer(audio: Path) -> None:
    opener = Recorder(json.dumps(GOOD_ANSWER).encode("utf-8"))
    data = GroqClient(SECRET, opener).transcribe(audio, "audio/flac", language="ru")
    assert data["words"][0]["word"] == "привет"
    request = opener.request
    assert request.full_url == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert request.get_method() == "POST"
    body = request.data.decode("latin-1")
    for expected in ('name="model"', "whisper-large-v3", 'name="response_format"', "verbose_json",
                     'name="timestamp_granularities[]"', "\r\n\r\nword\r\n", 'name="language"', "\r\n\r\nru\r\n",
                     'name="file"; filename="audio.flac"', "Content-Type: audio/flac"):
        assert expected in body, expected
    assert request.get_header("Authorization") == f"Bearer {SECRET}"
    assert opener.timeout and opener.timeout > 0


def test_language_is_optional(audio: Path) -> None:
    opener = Recorder(json.dumps(GOOD_ANSWER).encode("utf-8"))
    GroqClient(SECRET, opener).transcribe(audio, "audio/flac")
    assert 'name="language"' not in opener.request.data.decode("latin-1")


def test_words_from_response_drops_invented_words() -> None:
    wf, dropped = words_from_response(GOOD_ANSWER, media_duration=3.0)
    assert [w.text for w in wf.words] == ["привет", "мир"]
    assert dropped == 1 and wf.duration == 3.0 and wf.language == "russian"


def test_answer_without_words_is_an_error() -> None:
    with pytest.raises(GroqError, match="word timestamps"):
        words_from_response({"text": "x"}, 3.0)


@pytest.mark.parametrize("status,needle", [(401, "401"), (413, "too large"), (429, "rate limit")])
def test_http_errors_have_clear_messages(audio: Path, status: int, needle: str) -> None:
    opener = Recorder(http_error(status, json.dumps({"error": {"message": f"server says {status}"}})))
    with pytest.raises(GroqError) as info:
        GroqClient(SECRET, opener).transcribe(audio, "audio/flac")
    assert needle in str(info.value)
    assert SECRET not in str(info.value)


def test_429_reports_retry_after(audio: Path) -> None:
    opener = Recorder(http_error(429, "{}", {"Retry-After": "17"}))
    with pytest.raises(GroqError, match="Retry after 17 s"):
        GroqClient(SECRET, opener).transcribe(audio, "audio/flac")


def test_401_message_tells_what_to_check(audio: Path) -> None:
    opener = Recorder(http_error(401, "{}"))
    with pytest.raises(GroqError, match="GROQ_API_KEY"):
        GroqClient(SECRET, opener).transcribe(audio, "audio/flac")


def test_other_status_shows_the_server_message(audio: Path) -> None:
    opener = Recorder(http_error(500, json.dumps({"error": {"message": "boom"}})))
    with pytest.raises(GroqError, match="500.*boom"):
        GroqClient(SECRET, opener).transcribe(audio, "audio/flac")


def test_key_echoed_by_the_server_is_redacted(audio: Path) -> None:
    opener = Recorder(http_error(400, json.dumps({"error": {"message": f"bad header Bearer {SECRET}"}})))
    with pytest.raises(GroqError) as info:
        GroqClient(SECRET, opener).transcribe(audio, "audio/flac")
    assert SECRET not in str(info.value) and "<key>" in str(info.value)
    assert SECRET not in repr(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_network_failure_and_bad_json_are_clear(audio: Path) -> None:
    with pytest.raises(GroqError, match="Cannot reach Groq"):
        GroqClient(SECRET, Recorder(urllib.error.URLError(f"dns failure for {SECRET}"))).transcribe(audio, "audio/flac")
    with pytest.raises(GroqError, match="not JSON"):
        GroqClient(SECRET, Recorder(b"<html>")).transcribe(audio, "audio/flac")


def test_key_never_appears_in_logs_or_outputs(audio: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    out = tmp_path / "words.json"
    opener = Recorder(json.dumps(GOOD_ANSWER).encode("utf-8"))

    class Info:
        duration = 3.0

    import transcribe as t

    orig_probe, orig_extract = t.probe, t.extract_audio
    t.probe = lambda path: Info()  # type: ignore[assignment]
    t.extract_audio = lambda video, workdir, max_bytes: (audio, "audio/flac")  # type: ignore[assignment]
    try:
        args = t.build_parser().parse_args([str(video), "--out", str(out), "--language", "ru", "-v"])
        with caplog.at_level(logging.DEBUG):
            code = t.run(args, opener=opener, environ={"GROQ_API_KEY": SECRET})
    finally:
        t.probe, t.extract_audio = orig_probe, orig_extract
    assert code == 0
    saved = out.read_text(encoding="utf-8")
    assert SECRET not in saved
    assert SECRET not in caplog.text
    captured = capsys.readouterr()
    assert SECRET not in captured.out and SECRET not in captured.err
    data = json.loads(saved)
    assert [w["text"] for w in data["words"]] == ["привет", "мир"]
    assert data["source"].endswith("v.mp4")


def test_key_from_environment_wins_over_the_file(tmp_path: Path) -> None:
    key_file = tmp_path / "env"
    key_file.write_text('export GROQ_API_KEY="from_file"\n', encoding="utf-8")
    assert load_api_key({"GROQ_API_KEY": " from_env "}, key_file) == "from_env"
    assert load_api_key({}, key_file) == "from_file"


@pytest.mark.parametrize("line,expected", [
    ('export GROQ_API_KEY="abc"', "abc"),
    ("GROQ_API_KEY=abc", "abc"),
    ("export GROQ_API_KEY='abc'  ", "abc"),
    ("# GROQ_API_KEY=commented\nexport GROQ_API_KEY=real", "real"),
])
def test_env_file_formats(tmp_path: Path, line: str, expected: str) -> None:
    key_file = tmp_path / "env"
    key_file.write_text(line + "\n", encoding="utf-8")
    assert load_api_key({}, key_file) == expected


def test_missing_key_explains_where_to_put_it(tmp_path: Path) -> None:
    with pytest.raises(MontageError) as info:
        load_api_key({}, tmp_path / "missing")
    text = str(info.value)
    assert "GROQ_API_KEY" in text and "~/.secrets/groq/env" in text


def test_redact() -> None:
    assert redact(f"a {SECRET} b", SECRET) == "a <key> b"
    assert redact("plain", "") == "plain"


def test_multipart_body_layout() -> None:
    body, content_type = encode_multipart([("a", "1")], "file", "x.flac", b"DATA", "audio/flac")
    boundary = content_type.split("boundary=")[1]
    assert body.startswith(f"--{boundary}\r\n".encode()) and body.endswith(f"--{boundary}--\r\n".encode())
    assert b"DATA" in body


def test_module_has_no_print_of_the_key() -> None:
    source = Path(transcribe.__file__).read_text(encoding="utf-8")
    assert "print(key" not in source and "LOG.info(key" not in source
