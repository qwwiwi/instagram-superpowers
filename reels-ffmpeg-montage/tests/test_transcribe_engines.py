"""Engine choice of transcribe.py: auto/groq/local, with Groq and faster-whisper both mocked (offline)."""

from __future__ import annotations

import ast
import json
import urllib.request
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import pytest

import transcribe
from common import MontageError
from conftest import CLIP_DURATION, fake_words
from transcribe import LOCAL_INSTALL, choose_engine, transcribe_local, words_from_local

# Assembled at run time so that no literal provider-prefixed key sits in the source.
SECRET = "gsk_" + "TEST" * 6 + "0123456789"
GHOST = ("призрак", 40.0, 40.5)  # invented beyond the end of the 13 s clip


def present(name: str) -> object:
    """find_spec stand-in: the package is installed."""
    return object()


def absent(name: str) -> None:
    """find_spec stand-in: the package is not installed."""
    return None


# --- fake faster-whisper ------------------------------------------------------------------------------------


@dataclass
class FakeWord:
    word: str
    start: float
    end: float


@dataclass
class FakeSegment:
    words: list[FakeWord] | None


@dataclass
class FakeInfo:
    language: str


@dataclass
class FakeModel:
    segments: list[FakeSegment]
    language: str
    calls: list[dict[str, Any]] = field(default_factory=list)
    audio_facts: list[tuple[int, int]] = field(default_factory=list)

    def transcribe(self, audio: str, **kwargs: Any) -> tuple[Iterator[FakeSegment], FakeInfo]:
        self.calls.append({"audio": audio, **kwargs})
        path = Path(audio)
        if path.suffix == ".wav" and path.is_file():
            with wave.open(str(path), "rb") as handle:
                self.audio_facts.append((handle.getnchannels(), handle.getframerate()))
        return iter(self.segments), FakeInfo(self.language)


class FakeFactory:
    """Stands in for faster_whisper.WhisperModel and remembers how it was built."""

    def __init__(self, model: FakeModel) -> None:
        self.model = model
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> FakeModel:
        self.calls.append((args, kwargs))
        return self.model


def forbidden_factory(*args: Any, **kwargs: Any) -> FakeModel:
    raise AssertionError("the local model must not be built")


def local_model(language: str = "ru") -> FakeModel:
    """Fake model that returns the synthetic take's words the way faster-whisper does (leading spaces)."""
    words = [FakeWord(f" {w.text}", w.start, w.end) for w in fake_words()]
    words.insert(3, FakeWord("  ", 3.0, 3.1))  # empty word: must be skipped
    words.append(FakeWord(f" {GHOST[0]}", GHOST[1], GHOST[2]))
    half = len(words) // 2
    return FakeModel([FakeSegment(words[:half]), FakeSegment(None), FakeSegment(words[half:])], language)


# --- fake Groq ----------------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class GroqRecorder:
    """Stands in for urllib.request.urlopen and answers with the synthetic take's words."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: Any, timeout: float | None = None) -> FakeResponse:
        self.calls += 1
        words = [{"word": w.text, "start": w.start, "end": w.end} for w in fake_words()]
        words.append({"word": GHOST[0], "start": GHOST[1], "end": GHOST[2]})
        answer = {"text": "", "language": "russian", "duration": CLIP_DURATION, "words": words}
        return FakeResponse(json.dumps(answer).encode("utf-8"))


def forbidden_opener(request: Any, timeout: float | None = None) -> FakeResponse:
    raise AssertionError("nothing may be sent to Groq")


def parse(argv: list[str]) -> Any:
    return transcribe.build_parser().parse_args(argv)


# --- engine choice (no ffmpeg needed) -----------------------------------------------------------------------


def test_auto_picks_groq_when_a_key_is_present(tmp_path: Path) -> None:
    engine, key = choose_engine("auto", {"GROQ_API_KEY": SECRET}, tmp_path / "none", present)
    assert (engine, key) == ("groq", SECRET)


def test_auto_picks_groq_from_the_key_file(tmp_path: Path) -> None:
    key_file = tmp_path / "env"
    key_file.write_text(f'export GROQ_API_KEY="{SECRET}"\n', encoding="utf-8")
    assert choose_engine("auto", {}, key_file, present) == ("groq", SECRET)


def test_auto_picks_local_without_a_key_when_the_package_is_present(tmp_path: Path) -> None:
    asked: list[str] = []

    def finder(name: str) -> object:
        asked.append(name)
        return object()

    assert choose_engine("auto", {}, tmp_path / "none", finder) == ("local", None)
    assert asked == ["faster_whisper"]


def test_auto_with_neither_names_both_ways(tmp_path: Path) -> None:
    with pytest.raises(MontageError) as info:
        choose_engine("auto", {}, tmp_path / "none", absent)
    text = str(info.value)
    assert "GROQ_API_KEY" in text and "~/.secrets/groq/env" in text
    assert LOCAL_INSTALL in text
    assert "python3 -m venv .venv && .venv/bin/python -m pip install faster-whisper" in text
    assert ".venv/bin/python" in text


def test_local_without_the_package_prints_the_install_command(tmp_path: Path) -> None:
    with pytest.raises(MontageError) as info:
        choose_engine("local", {"GROQ_API_KEY": SECRET}, tmp_path / "none", absent)
    assert LOCAL_INSTALL in str(info.value)
    assert SECRET not in str(info.value)


def test_groq_without_a_key_prints_the_key_message(tmp_path: Path) -> None:
    with pytest.raises(MontageError) as info:
        choose_engine("groq", {}, tmp_path / "none", present)
    text = str(info.value)
    assert text.startswith("No Groq key") and "~/.secrets/groq/env" in text


def test_local_does_not_need_a_key(tmp_path: Path) -> None:
    assert choose_engine("local", {}, tmp_path / "none", present) == ("local", None)


def test_find_spec_failure_counts_as_absent() -> None:
    def broken(name: str) -> object:
        raise ValueError("broken spec")

    assert transcribe.local_engine_available(broken) is False


def test_cli_engine_defaults_and_local_options() -> None:
    args = parse(["v.mp4"])
    assert args.engine == "auto" and args.model == "large-v3-turbo"
    assert args.device == "cpu" and args.compute_type == "int8" and args.model_dir is None
    args = parse(["v.mp4", "--engine", "local", "--model", "small", "--device", "cuda",
                  "--compute-type", "float16", "--model-dir", "/tmp/models"])
    assert (args.engine, args.model, args.device, args.compute_type) == ("local", "small", "cuda", "float16")
    assert args.model_dir == Path("/tmp/models")
    with pytest.raises(SystemExit):
        parse(["v.mp4", "--engine", "cloud"])


def test_faster_whisper_is_not_imported_at_module_level() -> None:
    tree = ast.parse(Path(transcribe.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import):
            assert all("faster_whisper" not in alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert "faster_whisper" not in (node.module or "")


# --- local engine on its own (no ffmpeg needed) -------------------------------------------------------------


def test_transcribe_local_strips_skips_and_passes_options(tmp_path: Path) -> None:
    model = local_model(language="en")
    factory = FakeFactory(model)
    raw, language = transcribe_local(tmp_path / "audio.wav", "ru", model_name="small", device="cpu",
                                     compute_type="int8", model_dir=tmp_path / "models", model_factory=factory)
    assert language == "ru"  # --language wins over detection
    assert all(item["word"] == item["word"].strip() and item["word"] for item in raw)
    assert len(raw) == len(fake_words()) + 1  # the empty word is gone, the ghost is still there
    assert factory.calls == [(("small",), {"device": "cpu", "compute_type": "int8",
                                          "download_root": str(tmp_path / "models")})]
    call = model.calls[0]
    assert call["language"] == "ru" and call["word_timestamps"] is True and call["beam_size"] == 5


def test_transcribe_local_detects_the_language_when_not_given(tmp_path: Path) -> None:
    model = local_model(language="ru")
    raw, language = transcribe_local(tmp_path / "a.wav", None, model_factory=FakeFactory(model))
    assert language == "ru" and model.calls[0]["language"] is None


def test_words_from_local_drops_words_beyond_the_media() -> None:
    raw = [{"word": "привет", "start": 0.5, "end": 1.0}, {"word": GHOST[0], "start": GHOST[1], "end": GHOST[2]}]
    wf, dropped = words_from_local(raw, "ru", 3.0)
    assert [w.text for w in wf.words] == ["привет"] and dropped == 1
    assert wf.duration == 3.0 and wf.language == "ru"


def test_model_load_failure_is_a_clear_error(tmp_path: Path) -> None:
    def failing(*args: Any, **kwargs: Any) -> FakeModel:
        raise RuntimeError("no network")

    with pytest.raises(MontageError, match="Cannot load the local model 'large-v3-turbo'.*--model"):
        transcribe_local(tmp_path / "a.wav", None, model_factory=failing)


# --- full runs on the synthetic clip (ffmpeg needed) --------------------------------------------------------


def run_groq(clip: Path, out: Path, opener: Any) -> int:
    args = parse([str(clip), "--engine", "groq", "--out", str(out)])
    return transcribe.run(args, opener=opener, environ={"GROQ_API_KEY": SECRET}, key_file=Path("/nonexistent"),
                          model_factory=forbidden_factory, find_spec=present)


def test_auto_with_a_key_runs_groq_and_never_builds_a_model(synthetic_clip: Path, tmp_path: Path) -> None:
    opener = GroqRecorder()
    args = parse([str(synthetic_clip), "--out", str(tmp_path / "words.json")])
    code = transcribe.run(args, opener=opener, environ={"GROQ_API_KEY": SECRET}, key_file=Path("/nonexistent"),
                          model_factory=forbidden_factory, find_spec=present)
    assert code == 0 and opener.calls == 1


def test_auto_without_a_key_runs_local(synthetic_clip: Path, tmp_path: Path) -> None:
    factory = FakeFactory(local_model())
    out = tmp_path / "words.json"
    args = parse([str(synthetic_clip), "--out", str(out)])
    code = transcribe.run(args, opener=forbidden_opener, environ={}, key_file=tmp_path / "none",
                          model_factory=factory, find_spec=present)
    assert code == 0 and len(factory.calls) == 1 and out.is_file()


def test_local_run_writes_the_same_words_json_as_groq(synthetic_clip: Path, tmp_path: Path) -> None:
    groq_out, local_out = tmp_path / "groq.json", tmp_path / "local.json"
    assert run_groq(synthetic_clip, groq_out, GroqRecorder()) == 0

    model = local_model(language="ru")
    args = parse([str(synthetic_clip), "--engine", "local", "--out", str(local_out)])
    code = transcribe.run(args, opener=forbidden_opener, environ={}, key_file=tmp_path / "none",
                          model_factory=FakeFactory(model), find_spec=present)
    assert code == 0

    groq_data = json.loads(groq_out.read_text(encoding="utf-8"))
    local_data = json.loads(local_out.read_text(encoding="utf-8"))
    assert set(local_data) == set(groq_data) == {"version", "source", "language", "duration", "words"}
    for key in ("version", "source", "duration", "words"):
        assert local_data[key] == groq_data[key], key
    assert all(set(w) == {"text", "start", "end"} for w in local_data["words"])
    texts = [w["text"] for w in local_data["words"]]
    assert texts == [w.text for w in fake_words()]  # empty word skipped, ghost beyond the end dropped
    assert GHOST[0] not in texts
    assert local_data["language"] == "ru"  # detected by the model, --language was not given
    assert local_data["duration"] == pytest.approx(CLIP_DURATION, abs=0.1)
    assert local_data["source"] == str(synthetic_clip.resolve())
    # The local engine got mono 16 kHz WAV from ffmpeg.
    assert model.audio_facts == [(1, 16000)]
    assert model.calls[0]["audio"].endswith(".wav")


def test_local_run_passes_the_language_through(synthetic_clip: Path, tmp_path: Path) -> None:
    model = local_model(language="en")
    out = tmp_path / "words.json"
    args = parse([str(synthetic_clip), "--engine", "local", "--language", "ru", "--out", str(out)])
    transcribe.run(args, opener=forbidden_opener, environ={}, key_file=tmp_path / "none",
                   model_factory=FakeFactory(model), find_spec=present)
    assert model.calls[0]["language"] == "ru"
    assert json.loads(out.read_text(encoding="utf-8"))["language"] == "ru"


def test_local_run_never_talks_to_groq_even_with_a_key(synthetic_clip: Path, tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_opener)

    class NoGroq:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("GroqClient must not be built for the local engine")

    monkeypatch.setattr(transcribe, "GroqClient", NoGroq)
    out = tmp_path / "words.json"
    args = parse([str(synthetic_clip), "--engine", "local", "--out", str(out)])
    code = transcribe.run(args, opener=forbidden_opener, environ={"GROQ_API_KEY": SECRET},
                          key_file=tmp_path / "none", model_factory=FakeFactory(local_model()), find_spec=present)
    assert code == 0
    assert SECRET not in out.read_text(encoding="utf-8")


def test_local_run_with_no_words_is_an_error(synthetic_clip: Path, tmp_path: Path) -> None:
    model = FakeModel([FakeSegment([FakeWord(" ", 0.1, 0.2)])], "ru")
    args = parse([str(synthetic_clip), "--engine", "local", "--out", str(tmp_path / "words.json")])
    with pytest.raises(MontageError, match="No words were recognised"):
        transcribe.run(args, opener=forbidden_opener, environ={}, key_file=tmp_path / "none",
                       model_factory=FakeFactory(model), find_spec=present)
