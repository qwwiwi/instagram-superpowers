#!/usr/bin/env python3
"""Small client for the local automation server of Edits for Mac (MCP over HTTP).

The server listens on 127.0.0.1:19501. ``GET /health`` needs no token; every
other path needs ``Authorization: Bearer <access token>``. The token comes from
the environment variable ``EDITS_TOKEN`` or, when it is not set, from a file
(``EDITS_TOKEN_FILE``, default ``~/.config/edits/automation.token``). The token
is never printed or logged, and error messages name only where it was looked for.

Command line (results go to stdout, diagnostics to stderr)::

    edits_client.py health
    edits_client.py selftest
    edits_client.py list [--json]
    edits_client.py schema <tool>
    edits_client.py call <tool> '<json arguments>'

Exit codes: 0 ok, 1 the tool answered with ``isError``, 2 connection, auth or
protocol problem.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import http.client
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("edits")

DEFAULT_URL = "http://127.0.0.1:19501"
DEFAULT_TOKEN_FILE = "~/.config/edits/automation.token"
TOKEN_ENV = "EDITS_TOKEN"
TOKEN_FILE_ENV = "EDITS_TOKEN_FILE"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "reels-edits-publish"
# Tools this skill never calls: they delete other people's work, crash or are refused
# by the app, or ripple the timeline (see the hard rules in SKILL.md).
FORBIDDEN_TOOLS = frozenset({
    "deleteProject",
    "selectSpeedPanelPreset",
    "dragSpeedPanelCurve",
    "addGif",
    "addSongToComposition",
})
# A base64 string longer than this inside JSON text is treated as an image payload.
MIN_INLINE_IMAGE_CHARS = 1000


class EditsError(RuntimeError):
    """Any failure talking to the Edits automation server."""


class EditsAuthError(EditsError):
    """The token is missing, malformed or rejected (HTTP 403)."""


@dataclass
class ToolResult:
    """Parsed answer of one ``tools/call``.

    Attributes:
        is_error: True when the server flagged the call as failed.
        data: The tool payload. The server returns it as a JSON string inside a
            text block; this is the decoded value (or the raw text if it is not JSON).
        images: Decoded image bytes found in the answer (image blocks or long
            base64 strings inside the JSON payload).
        raw: The untouched ``result`` object.
    """

    is_error: bool
    data: Any
    images: List[bytes] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


def token_file_path() -> Path:
    """Return the token file path from ``EDITS_TOKEN_FILE`` or the default."""
    return Path(os.environ.get(TOKEN_FILE_ENV, DEFAULT_TOKEN_FILE)).expanduser()


def token_hint(token_file: Path) -> str:
    """Tell the user where the token lives in the app and where to put it."""
    return (
        "Open Edits > Settings > Automation > Access token, copy the token into "
        f"{token_file} (one line, chmod 600) or into the environment variable {TOKEN_ENV}, and retry. "
        f"Set {TOKEN_FILE_ENV} to use another file."
    )


def auth_error_message(token_file: Path) -> str:
    """Build the human message for a 403 answer.

    Args:
        token_file: Where the client looks for the token when the variable is not set.

    Returns:
        A message that says where the token lives in the app and where to save it.
    """
    return "Edits answered 403: the access token is missing or wrong. " + token_hint(token_file)


def check_token(token: str, origin: str, token_file: Path) -> str:
    """Validate a token without ever echoing it.

    Args:
        token: The raw value.
        origin: Where it came from (a file path or the variable name), for messages.
        token_file: The default file, for the hint.

    Returns:
        The stripped token.

    Raises:
        EditsAuthError: Empty, or not a single printable word. The message names
            the origin only, never the value.
    """
    token = token.strip()
    if not token:
        raise EditsAuthError(f"The token in {origin} is empty. " + token_hint(token_file))
    # An extra line or a control character would make http.client reject the
    # header and echo the whole value, so refuse it before any request.
    if any(ch.isspace() or not ch.isprintable() for ch in token):
        raise EditsAuthError(
            f"The token in {origin} must be one line with the token only "
            "(no spaces, extra lines or control characters). " + token_hint(token_file)
        )
    return token


def load_token(token_file: Path, environ: Optional[Dict[str, str]] = None) -> str:
    """Read the token from ``EDITS_TOKEN`` or from the token file.

    Args:
        token_file: Path of the token file, used when the variable is not set.
        environ: Environment to read (default ``os.environ``); for tests.

    Returns:
        The stripped token.

    Raises:
        EditsAuthError: No token, or a malformed one.
    """
    env = os.environ if environ is None else environ
    if env.get(TOKEN_ENV):
        return check_token(env[TOKEN_ENV], TOKEN_ENV, token_file)
    try:
        raw = token_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise EditsAuthError(
            f"No {TOKEN_ENV} and cannot read the token file {token_file} ({exc.strerror}). " + token_hint(token_file)
        ) from exc
    return check_token(raw, str(token_file), token_file)


def build_rpc_body(request_id: int, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build one JSON-RPC 2.0 request for the ``/mcp`` endpoint.

    Args:
        request_id: Increasing request id.
        method: ``initialize``, ``tools/list`` or ``tools/call``.
        params: Method parameters; omitted from the body when None.

    Returns:
        The request body as a dict.
    """
    body: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def initialize_params() -> Dict[str, Any]:
    """Parameters of the MCP ``initialize`` handshake."""
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": CLIENT_NAME, "version": "1.0"},
    }


def tool_call_params(tool: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Parameters of a ``tools/call`` request.

    Args:
        tool: Tool name.
        arguments: Tool arguments (an empty object when None).

    Returns:
        ``{"name": tool, "arguments": {...}}``.

    Raises:
        ValueError: Empty or forbidden tool name, or arguments that are not an object.
    """
    if not tool:
        raise ValueError("tool name is empty")
    if tool in FORBIDDEN_TOOLS:
        raise ValueError(f"tool {tool!r} is forbidden by this skill (see the hard rules in SKILL.md)")
    if arguments is not None and not isinstance(arguments, dict):
        raise ValueError("tool arguments must be a JSON object")
    return {"name": tool, "arguments": arguments or {}}


def _decode_image(value: str) -> Optional[bytes]:
    """Decode a long base64 string, or return None if it is not base64."""
    if len(value) < MIN_INLINE_IMAGE_CHARS:
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


def _walk_images(node: Any, out: List[bytes]) -> None:
    """Collect base64 images from nested JSON values."""
    if isinstance(node, str):
        blob = _decode_image(node)
        if blob is not None:
            out.append(blob)
    elif isinstance(node, dict):
        for value in node.values():
            _walk_images(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_images(value, out)


def parse_tool_result(result: Dict[str, Any]) -> ToolResult:
    """Turn the ``result`` of a ``tools/call`` into a :class:`ToolResult`.

    The server puts the payload as a JSON string in ``content[0].text``. Several
    text blocks are joined. Output is never truncated.

    Args:
        result: The ``result`` member of the JSON-RPC answer.

    Returns:
        The parsed result.
    """
    texts: List[str] = []
    images: List[bytes] = []
    for block in result.get("content", []) or []:
        kind = block.get("type")
        if kind == "text":
            texts.append(block.get("text", ""))
        elif kind == "image" and block.get("data"):
            blob = _decode_image(block["data"])
            if blob is not None:
                images.append(blob)
    text = "".join(texts)
    data: Any = text
    if text.strip():
        try:
            data = json.loads(text)
        except ValueError:
            data = text
    _walk_images(data, images)
    return ToolResult(is_error=bool(result.get("isError")), data=data, images=images, raw=result)


def parse_body(raw: str) -> Dict[str, Any]:
    """Parse a plain JSON body or a single ``data:`` line of an event stream.

    Raises:
        EditsError: An event stream without a data line.
    """
    stripped = raw.lstrip()
    if stripped.startswith(("event:", "data:")):
        for line in stripped.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise EditsError("Event stream without a data line")
    return json.loads(raw)


class EditsClient:
    """JSON-RPC client for the Edits automation server."""

    def __init__(self, base_url: Optional[str] = None, token_file: Optional[Path] = None, timeout: float = 120.0) -> None:
        """Create a client.

        Args:
            base_url: Server root, default ``EDITS_URL`` or ``http://127.0.0.1:19501``.
            token_file: Token file, default :func:`token_file_path`.
            timeout: Seconds to wait for one request.
        """
        self.base_url = (base_url or os.environ.get("EDITS_URL") or DEFAULT_URL).rstrip("/")
        self.token_file = token_file or token_file_path()
        self.timeout = timeout
        self._token: Optional[str] = None
        self._next_id = 0
        self._initialized = False

    def _get_token(self) -> str:
        if self._token is None:
            self._token = load_token(self.token_file)
        return self._token

    def health(self) -> Dict[str, Any]:
        """Call ``GET /health`` (no token needed).

        Returns:
            The decoded JSON, e.g. ``{"ok": true, "screen": "editor", "appVersion": ...}``.

        Raises:
            EditsError: The server is not reachable.
        """
        request = urllib.request.Request(f"{self.base_url}/health")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise EditsError(
                f"Edits is not reachable at {self.base_url} ({exc}). "
                "Start Edits and check that automation is on in Settings > Automation."
            ) from exc

    def rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send one JSON-RPC request to ``/mcp``.

        Args:
            method: JSON-RPC method, e.g. ``tools/list``.
            params: Method parameters.

        Returns:
            The ``result`` member.

        Raises:
            EditsAuthError: HTTP 403 or no usable token.
            EditsError: Any other transport or protocol failure.
        """
        self._next_id += 1
        token = self._get_token()
        request = urllib.request.Request(
            f"{self.base_url}/mcp",
            data=json.dumps(build_rpc_body(self._next_id, method, params)).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise EditsAuthError(auth_error_message(self.token_file)) from None
            raise EditsError(f"Edits answered HTTP {exc.code} to {method}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise EditsError(f"Edits is not reachable at {self.base_url} ({exc})") from None
        except (ValueError, http.client.HTTPException) as exc:
            # http.client may echo the Authorization header value in its message;
            # drop the original text and the exception chain on purpose.
            raise EditsError(
                f"Cannot send the request to Edits ({type(exc).__name__}; details hidden to keep the token private)"
            ) from None
        message = parse_body(raw)
        if "error" in message:
            raise EditsError(f"{method} failed: {json.dumps(message['error'], ensure_ascii=False)}")
        return message.get("result", {})

    def initialize(self) -> None:
        """Run the MCP ``initialize`` handshake once."""
        if self._initialized:
            return
        self.rpc("initialize", initialize_params())
        self._initialized = True

    def list_tools(self) -> List[Dict[str, Any]]:
        """Return every tool of the current screen (follows ``nextCursor``).

        The tool set depends on the screen (home or editor): list again after
        opening a project.
        """
        self.initialize()
        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            result = self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def schema(self, tool: str) -> Dict[str, Any]:
        """Return the definition (description, inputSchema) of one tool.

        Raises:
            EditsError: The tool does not exist on the current screen.
        """
        for item in self.list_tools():
            if item.get("name") == tool:
                return item
        raise EditsError(f"No tool named {tool!r} on the current screen (home and editor differ)")

    def call(self, tool: str, arguments: Optional[Dict[str, Any]] = None) -> ToolResult:
        """Call one tool and return the parsed result (check ``is_error``)."""
        try:
            params = tool_call_params(tool, arguments)
        except ValueError as exc:
            raise EditsError(str(exc)) from None
        self.initialize()
        return parse_tool_result(self.rpc("tools/call", params))

    def call_ok(self, tool: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Call a tool and return its payload, raising on ``isError`` or ``ok: false``.

        Raises:
            EditsError: The tool reported an error; the message carries the tool
                name and the app's error text.
        """
        outcome = self.call(tool, arguments)
        data = outcome.data
        app_failed = isinstance(data, dict) and data.get("ok") is False
        if outcome.is_error or app_failed:
            if app_failed and data.get("error") not in (None, ""):
                err = data["error"]
                text = err if isinstance(err, str) else json.dumps(err, ensure_ascii=False)
            else:
                text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
            raise EditsError(f"{tool} failed: {text}")
        return data


def selftest(client: EditsClient) -> List[str]:
    """Connection self-test: health without a token, then the handshake and the tool list.

    Args:
        client: The client to test.

    Returns:
        Report lines (screen, app version, tool count). Never contains the token.

    Raises:
        EditsError: Any step failed; the message says which and what to do.
    """
    health = client.health()
    lines = [f"health: ok={health.get('ok')} screen={health.get('screen')} appVersion={health.get('appVersion')}"]
    tools = client.list_tools()
    names = {str(t.get("name")) for t in tools}
    lines.append(f"tools: {len(tools)} on the current screen")
    for needed in ("newProject", "importMedia", "clips"):
        if needed not in names:
            lines.append(f"warning: no tool {needed!r} on this screen (the set follows home/editor; list again in the editor)")
    lines.append("selftest: OK")
    return lines


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Client for the Edits automation server")
    parser.add_argument("-v", "--verbose", action="store_true", help="log requests to stderr")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health", help="GET /health (no token)")
    sub.add_parser("selftest", help="health, handshake and tool count")
    p_list = sub.add_parser("list", help="list tools of the current screen")
    p_list.add_argument("--json", action="store_true", help="print the full tools/list as JSON")
    p_schema = sub.add_parser("schema", help="print one tool definition")
    p_schema.add_argument("tool")
    p_call = sub.add_parser("call", help="call one tool")
    p_call.add_argument("tool")
    p_call.add_argument("arguments", nargs="?", default="{}", help="JSON object, default {}")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Run the command line.

    Args:
        argv: Arguments without the program name (default ``sys.argv[1:]``).

    Returns:
        The process exit code.
    """
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")
    client = EditsClient()
    try:
        if args.command == "health":
            print(json.dumps(client.health(), ensure_ascii=False))
        elif args.command == "selftest":
            for line in selftest(client):
                print(line)
        elif args.command == "list":
            tools = client.list_tools()
            if args.json:
                print(json.dumps(tools, ensure_ascii=False, indent=1))
            else:
                print(f"tools: {len(tools)}")
                print(", ".join(str(t.get("name")) for t in tools))
        elif args.command == "schema":
            print(json.dumps(client.schema(args.tool), ensure_ascii=False, indent=1))
        else:
            try:
                arguments = json.loads(args.arguments)
                params_ok = tool_call_params(args.tool, arguments)
            except ValueError as exc:
                LOG.error("Bad arguments: %s", exc)
                return 2
            outcome = client.call(args.tool, params_ok["arguments"])
            if outcome.images:
                LOG.warning("%d image(s) in the answer; use capture_frames.py to save frames", len(outcome.images))
            print(outcome.data if isinstance(outcome.data, str) else json.dumps(outcome.data, ensure_ascii=False))
            return 1 if outcome.is_error else 0
    except EditsError as exc:
        LOG.error("%s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
