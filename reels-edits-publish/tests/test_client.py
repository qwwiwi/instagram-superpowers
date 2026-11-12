"""Offline tests of the Edits client: payloads, token handling, forbidden tools. No network."""

from __future__ import annotations

import base64
import io
import json
import urllib.error
from pathlib import Path
from typing import Any, Dict, List

import pytest

import edits_client
from edits_client import EditsAuthError, EditsClient, EditsError


def test_rpc_body_and_initialize_params() -> None:
    body = edits_client.build_rpc_body(3, "tools/list")
    assert body == {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    init = edits_client.build_rpc_body(1, "initialize", edits_client.initialize_params())
    assert init["params"]["protocolVersion"] == "2025-06-18"
    assert init["params"]["clientInfo"]["name"] == "reels-edits-publish"


def test_tool_call_params_guards() -> None:
    assert edits_client.tool_call_params("clips") == {"name": "clips", "arguments": {}}
    assert edits_client.tool_call_params("importMedia", {"path": "/a.mp4"})["arguments"] == {"path": "/a.mp4"}
    with pytest.raises(ValueError):
        edits_client.tool_call_params("")
    with pytest.raises(ValueError):
        edits_client.tool_call_params("clips", [1, 2])  # type: ignore[arg-type]


@pytest.mark.parametrize("tool", sorted(edits_client.FORBIDDEN_TOOLS))
def test_forbidden_tools_are_refused_before_any_request(tool: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a forbidden tool must not reach the server")

    monkeypatch.setattr(edits_client.urllib.request, "urlopen", no_network)
    with pytest.raises(EditsError, match="forbidden"):
        EditsClient().call(tool, {})
    assert edits_client.main(["call", tool, "{}"]) == 2


def test_parse_tool_result_json_text_and_images() -> None:
    payload = {"ok": True, "frame": base64.b64encode(b"\xff\xd8" + b"x" * 2000).decode()}
    parsed = edits_client.parse_tool_result({"content": [{"type": "text", "text": json.dumps(payload)}]})
    assert parsed.is_error is False and parsed.data["ok"] is True
    assert len(parsed.images) == 1 and parsed.images[0].startswith(b"\xff\xd8")
    err = edits_client.parse_tool_result({"isError": True, "content": [{"type": "text", "text": "bad id"}]})
    assert err.is_error is True and err.data == "bad id"


def test_parse_body_event_stream() -> None:
    assert edits_client.parse_body('event: message\ndata: {"result": {"ok": 1}}\n') == {"result": {"ok": 1}}
    with pytest.raises(EditsError):
        edits_client.parse_body("event: message\n")


def test_token_from_env_wins_and_is_validated(tmp_path: Path) -> None:
    missing = tmp_path / "none"
    assert edits_client.load_token(missing, {"EDITS_TOKEN": " abc123 \n"}) == "abc123"
    with pytest.raises(EditsAuthError) as info:
        edits_client.load_token(missing, {"EDITS_TOKEN": "two words"})
    assert "two words" not in str(info.value) and "EDITS_TOKEN" in str(info.value)


def test_token_file_problems_never_echo_the_value(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    with pytest.raises(EditsAuthError) as info:
        edits_client.load_token(token_file, {})
    assert str(token_file) in str(info.value)
    token_file.write_text("secret-value\nsecond-line\n")
    with pytest.raises(EditsAuthError) as info:
        edits_client.load_token(token_file, {})
    assert "secret-value" not in str(info.value)
    token_file.write_text("secret-value\n")
    assert edits_client.load_token(token_file, {}) == "secret-value"


def test_403_message_points_to_the_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-value\n")
    monkeypatch.delenv("EDITS_TOKEN", raising=False)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.HTTPError("http://x/mcp", 403, "Forbidden", {}, io.BytesIO(b""))  # type: ignore[arg-type]

    monkeypatch.setattr(edits_client.urllib.request, "urlopen", forbidden)
    with pytest.raises(EditsAuthError) as info:
        EditsClient(token_file=token_file).rpc("tools/list", {})
    message = str(info.value)
    assert "Settings > Automation > Access token" in message
    assert "secret-value" not in message


class FakeResponse:
    """Minimal context-manager response for urlopen."""

    def __init__(self, payload: Dict[str, Any]) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


def test_selftest_reports_without_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDITS_TOKEN", "secret-value")
    sent: List[Dict[str, Any]] = []

    def fake_urlopen(request: Any, timeout: float = 0) -> FakeResponse:
        if request.full_url.endswith("/health"):
            assert request.get_header("Authorization") is None
            return FakeResponse({"ok": True, "screen": "home", "appVersion": "1.0"})
        body = json.loads(request.data.decode("utf-8"))
        sent.append(body)
        assert request.get_header("Authorization") == "Bearer secret-value"
        if body["method"] == "tools/list":
            return FakeResponse({"result": {"tools": [{"name": "newProject"}, {"name": "importMedia"}]}})
        return FakeResponse({"result": {}})

    monkeypatch.setattr(edits_client.urllib.request, "urlopen", fake_urlopen)
    lines = edits_client.selftest(EditsClient())
    text = "\n".join(lines)
    assert "screen=home" in text and "tools: 2" in text and "selftest: OK" in text
    assert "'clips'" in text  # missing on this screen -> warning
    assert "secret-value" not in text
    assert [b["method"] for b in sent] == ["initialize", "tools/list"]
