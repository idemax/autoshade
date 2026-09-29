#!/usr/bin/env python3
"""File-backed Claude CLI proxy for interactive AutoShade runs.

AutoShade writes the verifier prompt to Claude's stdin and expects a Claude
JSON envelope on stdout.  This helper keeps that contract while handing the
prompt to a human or another orchestrator through a small request/response
directory.

Environment:
  AUTOSHADE_PROXY_DIR       Directory for request and response files.
                            Defaults to ./autoshade-proxy.
  AUTOSHADE_PROXY_TIMEOUT_SECS
                            Maximum wait for a response. Defaults to 3600.

For request <id>, write a response JSON to:
  <proxy-dir>/responses/<id>.json

The response body is the verifier verdict itself, for example:
  {"decision":"accept","reasons":[],"revised_hint":null}
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def atomic_write(path: Path, data: str) -> None:
    """Publish a complete file so the reader never sees a partial response."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def fail(message: str, *, code: int = 1) -> int:
    envelope = {"type": "result", "is_error": True, "result": message}
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return code


def main() -> int:
    prompt = sys.stdin.read()
    if not prompt.strip():
        return fail("the AutoShade proxy received an empty prompt")

    root = Path(os.environ.get("AUTOSHADE_PROXY_DIR", "./autoshade-proxy")).expanduser()
    requests = root / "requests"
    responses = root / "responses"
    requests.mkdir(parents=True, exist_ok=True)
    responses.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    request_id = f"{stamp}-{os.getpid()}-{secrets.token_hex(4)}"
    request_path = requests / f"{request_id}.txt"
    response_path = responses / f"{request_id}.json"
    metadata_path = requests / f"{request_id}.json"

    atomic_write(request_path, prompt)
    metadata = {
        "protocol": "autoshade-claude-proxy/v1",
        "kind": "verify",
        "request_id": request_id,
        "prompt_path": str(request_path.resolve()),
        "response_path": str(response_path.resolve()),
        "argv": sys.argv[1:],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")

    print(f"autoshade proxy request: {request_path.resolve()}", file=sys.stderr, flush=True)
    print(f"autoshade proxy response: {response_path.resolve()}", file=sys.stderr, flush=True)

    try:
        timeout = float(os.environ.get("AUTOSHADE_PROXY_TIMEOUT_SECS", "3600"))
    except ValueError:
        timeout = 3600.0
    timeout = max(timeout, 1.0)
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if response_path.exists():
            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return fail(f"invalid AutoShade proxy response: {exc}")

            if not isinstance(response, dict):
                return fail("AutoShade proxy response must be a JSON object")
            if "error" in response:
                return fail(str(response["error"]))

            decision = response.get("decision")
            if decision not in {"accept", "revise", "reject"}:
                return fail("AutoShade proxy response decision must be accept, revise, or reject")
            reasons = response.get("reasons", [])
            if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
                return fail("AutoShade proxy response reasons must be an array of strings")
            hint = response.get("revised_hint")
            if hint is not None and not isinstance(hint, str):
                return fail("AutoShade proxy response revised_hint must be a string or null")

            verdict = {
                "decision": decision,
                "reasons": reasons,
                "revised_hint": hint,
            }
            envelope = {
                "type": "result",
                "is_error": False,
                "result": json.dumps(verdict, ensure_ascii=False, separators=(",", ":")),
            }
            sys.stdout.write(json.dumps(envelope, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            return 0
        time.sleep(0.25)

    return fail(f"AutoShade proxy timed out waiting for {response_path.resolve()}")


if __name__ == "__main__":
    raise SystemExit(main())
