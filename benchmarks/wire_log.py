"""Record what Claude Code sends to the model, one file per request.

Claude Code talks to the model through whatever ANTHROPIC_BASE_URL names. Run this script in
one Command Prompt window and point Claude Code at it in another, and every request body is
saved as JSON in the folder given, numbered in order, with the token usage the model reported.
Nothing else is kept: the request's headers (the login token among them) are forwarded and not
written, and the response is passed through untouched.

Window 1:   py benchmarks\\wire_log.py results\\wire
Window 2:   set ANTHROPIC_BASE_URL=http://127.0.0.1:8787
            py benchmarks\\claude_pair.py ... (or plain claude)

Then `py benchmarks\\wire_report.py results\\wire` says how each request differs from the one
before it, which is what the prompt cache sees.
"""
from __future__ import annotations

import http.client
import itertools
import json
import os
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

UPSTREAM = os.environ.get("WIRE_UPSTREAM", "https://api.anthropic.com")
# Hop-by-hop headers, and the ones the forwarded connection sets itself.
SKIP = {"host", "content-length", "transfer-encoding", "connection", "keep-alive", "accept-encoding"}
counter = itertools.count(1)
lock = threading.Lock()


def usage_of(path: str, body: bytes) -> dict | None:
    """Token usage from a messages response, streamed or not."""
    if "/messages" not in path or "count_tokens" in path:
        return None
    usage: dict = {}
    text = body.decode("utf-8", "replace")
    if text.startswith("{"):
        try:
            usage = json.loads(text).get("usage") or {}
        except ValueError:
            return None
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            event = json.loads(line[6:])
        except ValueError:
            continue
        if event.get("type") == "message_start":
            usage.update(event.get("message", {}).get("usage") or {})
        elif event.get("type") == "message_delta":
            usage.update(event.get("usage") or {})
    return usage or None


class Forward(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    folder = "."

    def log_message(self, *args) -> None:
        pass

    def _forward(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        target = urlsplit(UPSTREAM)
        connection = (http.client.HTTPSConnection(target.hostname, target.port or 443, context=ssl.create_default_context(), timeout=600)
                      if target.scheme == "https" else http.client.HTTPConnection(target.hostname, target.port or 80, timeout=600))
        headers = {k: v for k, v in self.headers.items() if k.lower() not in SKIP}
        headers["Host"] = target.netloc
        headers["Accept-Encoding"] = "identity"
        try:
            connection.request(self.command, self.path, body=body if body else None, headers=headers)
            response = connection.getresponse()
        except OSError as error:
            self.send_error(502, f"upstream unreachable: {error}")
            return
        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() not in {"content-length", "transfer-encoding", "connection", "content-encoding"}:
                self.send_header(key, value)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        collected = []
        while True:
            chunk = response.read(4096)
            if not chunk:
                break
            collected.append(chunk)
            self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()
        connection.close()
        if self.command == "POST":
            self._save(body, response.status, b"".join(collected))

    def _save(self, body: bytes, status: int, answer: bytes) -> None:
        with lock:
            number = next(counter)
        try:
            request = json.loads(body)
        except ValueError:
            request = {"raw": body.decode("utf-8", "replace")}
        record = {"path": self.path, "status": status, "request": request, "usage": usage_of(self.path, answer)}
        name = self.path.strip("/").split("?")[0].replace("/", "_") or "request"
        with open(os.path.join(self.folder, f"{number:03d}-{name}.json"), "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=1, ensure_ascii=False)
        usage = record["usage"] or {}
        print(f"{number:03d} {self.path} -> {status}" + (f"  write {usage.get('cache_creation_input_tokens', 0):,} read {usage.get('cache_read_input_tokens', 0):,} "
                                                        f"in {usage.get('input_tokens', 0):,} out {usage.get('output_tokens', 0):,}" if usage else ""), flush=True)

    do_POST = _forward
    do_GET = _forward
    do_HEAD = _forward
    do_PUT = _forward
    do_DELETE = _forward


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in {"-h", "--help"}:
        print(__doc__)
        return 1
    folder = argv[1]
    port = int(argv[2]) if len(argv) > 2 else 8787
    os.makedirs(folder, exist_ok=True)
    Forward.folder = folder
    print(f"Recording requests to {folder}; forwarding to {UPSTREAM}.\nIn the other window: set ANTHROPIC_BASE_URL=http://127.0.0.1:{port}\nPress Ctrl+C to stop.", flush=True)
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Forward).serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
