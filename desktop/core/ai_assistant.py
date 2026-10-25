"""The assistant's brain: OpenRouter, the model chain, the key, the transcript.

Deliberately dependency-free. The whole application already ships as one frozen
``.exe``; pulling in ``requests`` or an SDK for one text box would add megabytes
and a supply-chain surface for no benefit, so this speaks HTTP with the standard
library and parses SSE by hand.

Three rules shape the error handling, and they exist because each one is a way
this kind of feature usually lies to its user:

* A **429 / 5xx / timeout** is a model problem: fall back to the next model and
  say so in the answer, so a reply from the fallback is never presented as if it
  came from the primary.
* A **401** (bad key) or **402** (no credits) is not a model problem. Falling
  back hides it and produces a much more confusing error two models later, so
  these are raised as themselves.
* Text that actually arrived is never discarded because the stream died halfway.
  A partial answer is labelled partial.
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Optional, Sequence

#: Ordered fallback chain. The first model is the one the operator asked for;
#: the second exists so a rate limit does not stop work.
DEFAULT_MODELS: tuple = (
    "google/gemma-3-4b-it",
    "meta-llama/llama-3.1-8b-instruct",
)

API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"
KEY_URL = "https://openrouter.ai/api/v1/key"

#: Identifies the client to OpenRouter. No user data leaves in these.
ATTRIBUTION = {
    "HTTP-Referer": "https://github.com/tecxe/DroidForenX",
    "X-Title": "DroidForenX",
}

#: Error kinds the UI is allowed to branch on.
AUTH = "auth"
CREDITS = "credits"
RATE_LIMIT = "rate_limit"
UPSTREAM = "upstream"
NETWORK = "network"
MALFORMED = "malformed"
BAD_REQUEST = "bad_request"

#: Kinds that mean "this model could not answer, try the next one".
RETRYABLE = frozenset({RATE_LIMIT, UPSTREAM, NETWORK})


class AssistantError(Exception):
    """A failure with a kind, so the UI can say something specific."""

    def __init__(self, kind: str, message: str, status: int = 0):
        super().__init__(message)
        self.kind = kind
        self.status = status

    @property
    def user_message(self) -> str:
        return {
            AUTH: "OpenRouter rejected the API key. Open settings and paste a "
                  "valid key (it must start with sk-or-).",
            CREDITS: "This OpenRouter key has no credits left. Add credits, or "
                     "switch to a model the key can afford.",
            RATE_LIMIT: "Every model in the chain is rate-limited right now. "
                        "Wait a moment and send again.",
            UPSTREAM: "The model provider returned an error. Try again.",
            NETWORK: "Could not reach openrouter.ai. Check the network "
                     "connection; the rest of DroidForenX works offline.",
            MALFORMED: "The provider sent a response that could not be read.",
            BAD_REQUEST: "OpenRouter rejected the request.",
        }.get(self.kind, str(self))


# --------------------------------------------------------------------- key


class KeyStore:
    """Where the OpenRouter key lives.

    On Windows the key is encrypted with DPAPI under the current user, so a
    copied file is useless on another account or machine. Elsewhere it falls
    back to a 0600 file, because refusing to work at all would be worse. The key
    is never written into the case folder, the database or a log.
    """

    def __init__(self, path: Optional[Path] = None):
        if path is None:
            base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".droidforenx")
            path = Path(base) / "DroidForenX" / "assistant.key"
        self.path = Path(path)

    # -- public API -------------------------------------------------------

    def load(self) -> str:
        """Key from the environment first, then the encrypted store."""
        env = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if env:
            return env
        try:
            blob = self.path.read_bytes()
        except OSError:
            return ""
        if not blob:
            return ""
        plain = _dpapi_unprotect(blob)
        if plain is None:
            # Written on another account, or by an older build: unreadable.
            return ""
        return plain.decode("utf-8", "replace").strip()

    def save(self, key: str) -> None:
        key = (key or "").strip()
        if not key:
            raise ValueError("key is empty")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        blob = _dpapi_protect(key.encode("utf-8"))
        if blob is None:
            blob = key.encode("utf-8")
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        self.path.write_bytes(blob)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


def mask_key(key: str) -> str:
    """``sk-or-v1-abcd…wxyz`` -- enough to recognise, not enough to use."""
    key = (key or "").strip()
    if len(key) <= 12:
        return "\u2026" if key else ""
    return f"{key[:10]}\u2026{key[-4:]}"


# ------------------------------------------------------------------ DPAPI


def _dpapi_protect(data: bytes) -> Optional[bytes]:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        import ctypes.wintypes as wt  # noqa: F401

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        crypt32 = ctypes.windll.crypt32
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in), "DroidForenX assistant key", None, None, None,
            0x1,  # CRYPTPROTECT_UI_FORBIDDEN
            ctypes.byref(blob_out))
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    except Exception:  # pragma: no cover - platform dependent
        return None


def _dpapi_unprotect(blob: bytes) -> Optional[bytes]:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        buf = ctypes.create_string_buffer(blob, len(blob))
        blob_in = DATA_BLOB(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None,
            0x1, ctypes.byref(blob_out))
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    except Exception:  # pragma: no cover - platform dependent
        return None


# ----------------------------------------------------------------- client


@dataclass
class Answer:
    """One reply, with an honest account of where it came from."""

    text: str
    model: str
    usage: dict = field(default_factory=dict)
    #: Models that were tried and why they were abandoned.
    failures: List[tuple] = field(default_factory=list)
    partial: bool = False

    @property
    def used_fallback(self) -> bool:
        return bool(self.failures)


#: ``transport(url, headers, body, timeout) -> iterator of raw SSE lines``.
#: Injectable so the tests never touch the network.
Transport = Callable[[str, dict, bytes, int], Iterable[bytes]]


def urllib_transport(url: str, headers: dict, body: bytes, timeout: int) -> Iterator[bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    context = ssl.create_default_context()
    try:
        response = urllib.request.urlopen(request, timeout=timeout, context=context)
    except urllib.error.HTTPError as exc:
        # Read the body: OpenRouter explains *why* in JSON, and the UI needs it.
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise _http_error(exc.code, detail) from None
    except urllib.error.URLError as exc:
        raise AssistantError(NETWORK, f"network error: {exc.reason}") from None
    except TimeoutError:
        raise AssistantError(NETWORK, "the request timed out") from None

    with response:
        for line in response:
            yield line


def _http_error(status: int, body: str) -> AssistantError:
    message = body
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            message = (parsed.get("error") or {}).get("message") or body
    except Exception:
        pass
    if status in (401, 403):
        kind = AUTH
    elif status == 402:
        kind = CREDITS
    elif status == 429:
        kind = RATE_LIMIT
    elif status >= 500:
        kind = UPSTREAM
    elif status == 404:
        kind = UPSTREAM   # model not served right now; a fallback may work
    else:
        kind = BAD_REQUEST
    return AssistantError(kind, f"HTTP {status}: {message}", status=status)


class AssistantClient:
    """Streams a reply from the first model that will actually answer."""

    def __init__(self, api_key: str, models: Sequence[str] = DEFAULT_MODELS,
                 transport: Optional[Transport] = None, timeout: int = 90):
        self.api_key = (api_key or "").strip()
        self.models = [m for m in models if m] or list(DEFAULT_MODELS)
        self.transport = transport or urllib_transport
        self.timeout = timeout

    # -- public -----------------------------------------------------------

    def chat(self, messages: List[dict], on_delta: Optional[Callable[[str], None]] = None,
             should_stop: Optional[Callable[[], bool]] = None) -> Answer:
        if not self.api_key:
            raise AssistantError(AUTH, "no API key configured")

        failures: List[tuple] = []
        for index, model in enumerate(self.models):
            try:
                return self._stream_model(model, messages, on_delta, should_stop, failures)
            except AssistantError as exc:
                if exc.kind not in RETRYABLE:
                    raise
                failures.append((model, exc.kind))
                if index == len(self.models) - 1:
                    raise AssistantError(
                        exc.kind,
                        f"every model failed; last error: {exc}") from None
        raise AssistantError(UPSTREAM, "no models configured")  # pragma: no cover

    # -- internals --------------------------------------------------------

    def _stream_model(self, model: str, messages: List[dict],
                      on_delta: Optional[Callable[[str], None]],
                      should_stop: Optional[Callable[[], bool]],
                      failures: List[tuple]) -> Answer:
        payload = json.dumps({
            "model": model,
            "messages": messages,
            "stream": True,
        }).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            **ATTRIBUTION,
        }

        text_parts: List[str] = []
        usage: dict = {}
        started = False

        def _partial() -> Answer:
            return Answer("".join(text_parts), model, usage,
                          failures=list(failures), partial=True)

        try:
            for raw in self.transport(API_URL, headers, payload, self.timeout):
                if should_stop and should_stop():
                    return Answer("".join(text_parts), model, usage,
                                  failures=list(failures), partial=bool(text_parts))
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    event = json.loads(chunk)
                except json.JSONDecodeError:
                    # A keep-alive or a stray line is not worth losing the reply
                    # over; anything real arrives as parseable JSON.
                    continue
                if "error" in event:
                    message = (event.get("error") or {}).get("message", "provider error")
                    raise AssistantError(UPSTREAM, f"provider error: {message}")
                if event.get("usage"):
                    usage = event["usage"]
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        started = True
                        text_parts.append(piece)
                        if on_delta:
                            on_delta(piece)
        except AssistantError:
            # A provider error after text arrived is still a partial answer; the
            # text is the user's, and throwing it away to show a red banner is a
            # worse trade than labelling it.
            if text_parts:
                return _partial()
            raise
        except (urllib.error.URLError, TimeoutError):
            if text_parts:
                return _partial()
            raise AssistantError(NETWORK, "network error while opening the stream") from None
        except Exception as exc:  # pragma: no cover - transport specific
            if text_parts:
                return _partial()
            raise AssistantError(NETWORK, f"stream failed: {exc}") from None

        text = "".join(text_parts)
        if not text.strip():
            if not started:
                raise AssistantError(UPSTREAM, "the model returned no content")
            raise AssistantError(MALFORMED, "the reply was empty")
        return Answer(text, model, usage, failures=list(failures),
                      partial=bool(should_stop and should_stop()))

    # -- account ----------------------------------------------------------

    def check(self) -> dict:
        """``/key`` -- validates the key and returns credit information."""
        if not self.api_key:
            raise AssistantError(AUTH, "no API key configured")
        request = urllib.request.Request(
            KEY_URL, headers={"Authorization": f"Bearer {self.api_key}", **ATTRIBUTION})
        try:
            with urllib.request.urlopen(request, timeout=30,
                                        context=ssl.create_default_context()) as response:
                body = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:
                pass
            raise _http_error(exc.code, detail) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AssistantError(NETWORK, f"network error: {exc}") from None
        return body.get("data") or {}

    def available_models(self) -> List[str]:
        """Ids the key can currently see -- used by the settings dialog."""
        request = urllib.request.Request(MODELS_URL, headers=dict(ATTRIBUTION))
        with urllib.request.urlopen(request, timeout=30,
                                    context=ssl.create_default_context()) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
        return [item.get("id", "") for item in body.get("data") or []]


# --------------------------------------------------------------- transcript


class ChatStore:
    """Per-case transcript, kept as JSONL in the profile, never in the case.

    A conversation with a language model is the examiner's working note. If it
    travelled inside ``cases/<id>/`` it could be mistaken for part of the
    exhibit, or handed over with a disclosure that never mentioned it.
    """

    def __init__(self, base_dir: Optional[Path] = None):
        if base_dir is None:
            base_dir = Path.home() / ".droidforenx" / "assistant"
        self.base_dir = Path(base_dir)

    def path_for(self, case_id: str = "") -> Path:
        safe = "".join(ch for ch in (case_id or "unassigned")
                       if ch.isalnum() or ch in "-_") or "unassigned"
        return self.base_dir / f"{safe}.jsonl"

    def load(self, case_id: str = "") -> List[dict]:
        path = self.path_for(case_id)
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        messages = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("role"):
                messages.append(record)
        return messages

    def append(self, message: dict, case_id: str = "") -> None:
        path = self.path_for(case_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = dict(message)
        record.setdefault("ts", time.time())
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def clear(self, case_id: str = "") -> None:
        try:
            self.path_for(case_id).unlink()
        except OSError:
            pass


__all__ = [
    "API_URL", "DEFAULT_MODELS", "RETRYABLE",
    "AUTH", "CREDITS", "RATE_LIMIT", "UPSTREAM", "NETWORK", "MALFORMED", "BAD_REQUEST",
    "Answer", "AssistantClient", "AssistantError", "ChatStore", "KeyStore",
    "mask_key", "urllib_transport",
]
