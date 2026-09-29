#!/usr/bin/env python3
"""Local OpenAI Responses API adapter for interactive AutoShade proposals.

The adapter listens only on loopback, records each proposal prompt and preview
image, waits for a recipe JSON supplied by the operator, and returns the same
Responses API shape AutoShade already parses. It is intentionally transparent:
there is no remote model call and no credential handling.

Environment:
  AUTOSHADE_PROXY_DIR       Directory for request and response files.
                            Defaults to ./autoshade-proxy.
  AUTOSHADE_PROXY_HOST      Bind host. Defaults to 127.0.0.1.
  AUTOSHADE_PROXY_PORT      Bind port. Defaults to 8317.
  AUTOSHADE_PROXY_TIMEOUT_SECS
                            Maximum wait for a response. Defaults to 3600.

For request <id>, write a recipe JSON object to:
  <proxy-dir>/responses/<id>.json
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


def atomic_write(path: Path, data: str | bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if isinstance(data, bytes):
        tmp.write_bytes(data)
    else:
        tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def json_response(handler: BaseHTTPRequestHandler, status: int, body: dict) -> None:
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


class ProxyHandler(BaseHTTPRequestHandler):
    server_version = "AutoShadeOpenAIProxy/1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"autoshade openai proxy: {fmt % args}", file=sys.stderr, flush=True)

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/v1/responses":
            json_response(self, 404, {"error": {"message": "use POST /v1/responses"}})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64 * 1024 * 1024:
                raise ValueError("invalid Content-Length")
            request = json.loads(self.rfile.read(length).decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            json_response(self, 400, {"error": {"message": f"invalid request: {exc}"}})
            return

        root = Path(os.environ.get("AUTOSHADE_PROXY_DIR", "./autoshade-proxy")).expanduser()
        requests = root / "requests"
        responses = root / "responses"
        requests.mkdir(parents=True, exist_ok=True)
        responses.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        request_id = f"{stamp}-{os.getpid()}-{secrets.token_hex(4)}"
        response_path = responses / f"{request_id}.json"
        request_path = requests / f"{request_id}.json"
        prompt_path = requests / f"{request_id}.prompt.txt"

        content = request.get("input", [{}])[0].get("content", [])
        prompt = ""
        images: list[str] = []
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "input_text" and isinstance(item.get("text"), str):
                prompt = item["text"]
            if item.get("type") == "input_image" and isinstance(item.get("image_url"), str):
                images.append(item["image_url"])

        image_paths: list[str] = []
        for index, data_url in enumerate(images, start=1):
            prefix, separator, encoded = data_url.partition(",")
            if not separator or ";base64" not in prefix:
                continue
            try:
                image = base64.b64decode(encoded, validate=True)
            except (ValueError, base64.binascii.Error):
                continue
            image_path = requests / f"{request_id}.image-{index}.jpg"
            atomic_write(image_path, image)
            image_paths.append(str(image_path.resolve()))

        metadata = {
            "protocol": "autoshade-openai-proxy/v1",
            "kind": "propose",
            "request_id": request_id,
            "prompt_path": str(prompt_path.resolve()),
            "request_path": str(request_path.resolve()),
            "response_path": str(response_path.resolve()),
            "image_paths": image_paths,
            "model": request.get("model"),
            "schema": request.get("text", {}).get("format", {}).get("schema"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write(prompt_path, prompt)
        atomic_write(request_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
        print(f"autoshade openai request: {prompt_path.resolve()}", file=sys.stderr, flush=True)
        print(f"autoshade openai response: {response_path.resolve()}", file=sys.stderr, flush=True)

        try:
            timeout = max(float(os.environ.get("AUTOSHADE_PROXY_TIMEOUT_SECS", "3600")), 1.0)
        except ValueError:
            timeout = 3600.0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if response_path.exists():
                try:
                    recipe = json.loads(response_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    json_response(self, 400, {"error": {"message": f"invalid proxy response: {exc}"}})
                    return
                if not isinstance(recipe, dict):
                    json_response(self, 400, {"error": {"message": "recipe response must be a JSON object"}})
                    return
                if "error" in recipe:
                    json_response(self, 400, {"error": {"message": str(recipe["error"])}})
                    return
                text = json.dumps(recipe, ensure_ascii=False, separators=(",", ":"))
                json_response(self, 200, {
                    "id": f"proxy-{request_id}",
                    "object": "response",
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
                })
                return
            time.sleep(0.25)

        json_response(self, 504, {"error": {"message": f"timed out waiting for {response_path.resolve()}"}})


def main() -> int:
    host = os.environ.get("AUTOSHADE_PROXY_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("AUTOSHADE_PROXY_PORT", "8317"))
    except ValueError:
        print("AUTOSHADE_PROXY_PORT must be an integer", file=sys.stderr)
        return 2
    server = ThreadingHTTPServer((host, port), ProxyHandler)
    print(f"autoshade openai proxy listening on http://{host}:{port}/v1/responses", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
