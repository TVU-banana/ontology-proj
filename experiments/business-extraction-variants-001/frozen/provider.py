"""Small OpenAI-compatible relay adapter; credentials never enter saved artifacts."""
from __future__ import annotations

import json
import time
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


class ProviderError(RuntimeError):
    pass


class Relay:
    def __init__(self, config: Path = HERE / ".env"):
        values = {}
        for line in config.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, sep, value = line.partition("=")
            if not sep:
                raise ProviderError("Invalid local configuration line")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[name.strip()] = value
        self.base = values.get("EXTRACTION_BASE_URL", "").rstrip("/")
        self.model = values.get("EXTRACTION_MODEL", "")
        self._key = values.get("EXTRACTION_API_KEY", "")
        parsed = urlsplit(self.base)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.query:
            raise ProviderError("Invalid relay base URL")
        if not self._key or not self.model:
            raise ProviderError("Local model or API key is empty")

    def scrub(self, value):
        if isinstance(value, str):
            return value.replace(self._key, "[REDACTED]")
        if isinstance(value, list):
            return [self.scrub(x) for x in value]
        if isinstance(value, dict):
            return {k: self.scrub(v) for k, v in value.items() if k.lower() not in ("authorization", "api_key")}
        return value

    def request(self, path: str, payload: dict | None = None, timeout: int = 90):
        # Network calls to .local require the host's network permissions in
        # this Codex sandbox. Send credentials via stdin configuration,
        # never command-line arguments, a temporary file or diagnostic output.
        def quoted(value):
            return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r') + '"'

        lines = ["url = " + quoted(self.base + path),
                 "header = " + quoted("Authorization: Bearer " + self._key),
                 'header = "Content-Type: application/json"']
        if payload is not None:
            lines.append("data = " + quoted(json.dumps(payload, ensure_ascii=False)))
        start = time.monotonic()
        try:
            result = subprocess.run([
                "curl", "--config", "-", "--silent", "--show-error",
                "--noproxy", "*", "--connect-timeout", "5", "--max-time", str(timeout),
                "--request", "GET" if payload is None else "POST",
                "--write-out", "\n%{http_code}",
            ], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=timeout + 5)
            if result.returncode:
                raise ProviderError(f"Relay transport failed for {path} (curl exit {result.returncode})")
            body, status_text = result.stdout.rsplit("\n", 1)
            status = int(status_text)
            if not 200 <= status < 300:
                raise ProviderError(f"Relay returned HTTP {status} for {path}")
            data = json.loads(body)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            raise ProviderError(f"Relay transport or JSON decoding failed for {path}") from None
        return self.scrub(data), {"http_status": status, "elapsed_seconds": round(time.monotonic() - start, 3)}

    def chat(self, messages: list, max_tokens: int):
        return self.request("/chat/completions", {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
        }, timeout=180)


def probe() -> None:
    relay = Relay()
    result = {"base_url": relay.base, "requested_model": relay.model, "authenticated": True}
    models, metadata = relay.request("/models", timeout=20)
    ids = [x.get("id") for x in models.get("data", [])]
    result["models"] = {**metadata, "count": len(ids), "requested_model_listed": relay.model in ids}
    write_json(HERE / "results/provider-check.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if relay.model not in ids:
        raise ProviderError("Configured model is not present in relay model list; no alternative selected")
    response, metadata = relay.chat([
        {"role": "system", "content": 'Return exactly the JSON object {"ping":"ok"}.'},
        {"role": "user", "content": "Test JSON output."},
    ], max_tokens=1024)
    write_json(HERE / "results/provider-probe-response.json", response)
    choice = response.get("choices", [{}])[0]
    content = choice.get("message", {}).get("content")
    try:
        valid = json.loads(content) == {"ping": "ok"}
    except (ValueError, TypeError):
        valid = False
    result["chat"] = {**metadata, "returned_model": response.get("model"), "finish_reason": choice.get("finish_reason"), "requested_thinking": "disabled", "json_probe_passed": valid, "usage": response.get("usage")}
    write_json(HERE / "results/provider-check.json", result)
    print(json.dumps(result["chat"], ensure_ascii=False), flush=True)
    if not valid or choice.get("finish_reason") != "stop":
        raise ProviderError("JSON probe did not finish with the expected object")


if __name__ == "__main__":
    try:
        probe()
    except ProviderError as error:
        print(str(error))
        raise SystemExit(1)
