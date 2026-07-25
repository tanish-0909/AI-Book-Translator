"""OpenAI client wrapper.

Two things this module exists to guarantee:

1. **No silent success.** Every failure path raises. The previous pipeline caught
   translation exceptions, fell through to reassembly, wrote the untranslated
   English, and printed "SUCCESS!". Nothing here does that.
2. **No guessed API surface.** The target model family is a limited preview and
   this machine could not reach GET /v1/models (HTTP 500). Rather than assume
   which parameter names it accepts, we probe once and cache the result.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)

CAPS_FILENAME = "model_caps.json"


class LLMError(RuntimeError):
    """Raised for any unrecoverable model interaction. Never swallowed upstream."""


@dataclass
class ModelCaps:
    """What a specific model actually accepts, determined by probing."""

    model: str
    token_param: str = "max_completion_tokens"  # or "max_tokens"
    supports_temperature: bool = False
    supports_json_schema: bool = False
    supports_vision: bool = False
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @staticmethod
    def load(path: Path) -> "ModelCaps | None":
        if not path.exists():
            return None
        try:
            return ModelCaps(**json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None


# --------------------------------------------------------------------------
# Retry
# --------------------------------------------------------------------------

_RETRYABLE = (RateLimitError, APIConnectionError)


def _with_retry(fn, *, attempts: int = 4, base_delay: float = 3.0, label: str = "request"):
    """Retry transient failures. Client errors (400/404/403) raise immediately.

    A timeout is treated as terminal after one further attempt: retrying a call
    that is slow rather than broken just burns the same wall-clock again.
    """
    last: Exception | None = None
    timeouts = 0
    for i in range(attempts):
        started = time.monotonic()
        try:
            return fn()
        except APITimeoutError as e:
            last = e
            timeouts += 1
            elapsed = time.monotonic() - started
            if timeouts >= 2:
                raise LLMError(
                    f"{label}: timed out twice after {elapsed:.0f}s each. The request is too "
                    f"large for the {REQUEST_TIMEOUT:.0f}s budget -- reduce the chunk size."
                ) from e
        except _RETRYABLE as e:
            last = e
        except APIStatusError as e:
            if e.status_code >= 500:
                last = e
            else:
                raise
        delay = base_delay * (2**i)
        print(f"    [retry {i + 1}/{attempts}] {label}: {type(last).__name__}; "
              f"waiting {delay:.0f}s", flush=True)
        time.sleep(delay)
    raise LLMError(f"{label} failed after {attempts} attempts: {type(last).__name__}: {last}")


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


# gpt-5.6-sol is a reasoning model, so wall-clock is dominated by reasoning
# tokens rather than by prompt size. Measured: 4k chars -> 66s, 12k -> 78s,
# 30k -> 83s. A generous ceiling costs nothing when calls succeed and avoids
# killing a slow-but-healthy request; the retry policy below is what actually
# bounds a stuck call.
REQUEST_TIMEOUT = 900.0


class LLM:
    def __init__(self, api_key: str, caps: ModelCaps):
        # max_retries=0: retries are handled by _with_retry, which distinguishes
        # a timeout (do not hammer) from a transient 5xx (back off and retry).
        self.client = OpenAI(api_key=api_key, max_retries=0, timeout=REQUEST_TIMEOUT)
        self.caps = caps
        self.model = caps.model
        self.usage = {"input": 0, "output": 0, "cached": 0, "calls": 0}
        self.last_seconds = 0.0
        # Translate/review call this client from a thread pool.
        self._lock = threading.Lock()

    # -- capability probing -------------------------------------------------

    @staticmethod
    def probe(api_key: str, model: str) -> ModelCaps:
        """Determine what `model` accepts. Raises if the model is unusable."""
        client = OpenAI(api_key=api_key, max_retries=1, timeout=180.0)
        caps = ModelCaps(model=model)
        msgs = [{"role": "user", "content": "Reply with the single word: ok"}]

        def create(**kw):
            # Retry 5xx/transient here too. OpenAI returned HTTP 500 on three
            # consecutive calls during development; a blip must not be
            # misreported as "model unavailable".
            return _with_retry(
                lambda: client.chat.completions.create(**kw),
                attempts=4, base_delay=3.0, label=f"probe {model}",
            )

        # Which token-limit parameter does it take?
        for param in ("max_completion_tokens", "max_tokens"):
            try:
                create(model=model, messages=msgs, **{param: 16})
                caps.token_param = param
                break
            except BadRequestError as e:
                if "max_tokens" in str(e) or "max_completion_tokens" in str(e):
                    continue
                raise LLMError(f"{model}: unexpected 400 during probe: {e}") from e
            except AuthenticationError as e:
                raise LLMError(
                    "OPENAI_API_KEY was rejected (401). Check the key in .env -- "
                    "it may be mistyped, revoked, or from a different organisation."
                ) from e
            except (NotFoundError, PermissionDeniedError) as e:
                raise LLMError(f"{model}: not available to this API key ({type(e).__name__})") from e
        else:
            raise LLMError(f"{model}: rejected both max_tokens and max_completion_tokens")

        base = {"model": model, "messages": msgs, caps.token_param: 16}

        # Does it accept a sampling temperature?
        try:
            create(**base, temperature=0.3)
            caps.supports_temperature = True
        except BadRequestError:
            caps.notes.append("temperature rejected; using model default")

        # Structured outputs -- the whole pipeline depends on these.
        try:
            r = create(
                model=model,
                messages=[{"role": "user", "content": "Return {\"ok\": true}"}],
                **{caps.token_param: 200},
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "probe",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {"ok": {"type": "boolean"}},
                            "required": ["ok"],
                            "additionalProperties": False,
                        },
                    },
                },
            )
            json.loads(r.choices[0].message.content)
            caps.supports_json_schema = True
        except Exception as e:
            caps.notes.append(f"json_schema unsupported: {type(e).__name__}")

        # Vision -- Stage 6 depends on this.
        try:
            import fitz

            doc = fitz.open()
            pg = doc.new_page(width=64, height=64)
            pg.draw_rect(fitz.Rect(8, 8, 56, 56), color=(0, 0, 0), fill=(0, 0, 0))
            png = pg.get_pixmap().tobytes("png")
            doc.close()
            b64 = base64.b64encode(png).decode()
            create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Answer in one word: what shape?"},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64}"},
                            },
                        ],
                    }
                ],
                **{caps.token_param: 32},
            )
            caps.supports_vision = True
        except Exception as e:
            caps.notes.append(f"vision unsupported: {type(e).__name__}")

        return caps

    # -- calls --------------------------------------------------------------

    def _params(self, max_tokens: int, temperature: float | None) -> dict[str, Any]:
        p: dict[str, Any] = {self.caps.token_param: max_tokens}
        if temperature is not None and self.caps.supports_temperature:
            p["temperature"] = temperature
        return p

    def _record(self, resp) -> None:
        u = getattr(resp, "usage", None)
        if not u:
            return
        details = getattr(u, "prompt_tokens_details", None)
        with self._lock:
            self.usage["calls"] += 1
            self.usage["input"] += getattr(u, "prompt_tokens", 0) or 0
            self.usage["output"] += getattr(u, "completion_tokens", 0) or 0
            if details is not None:
                self.usage["cached"] += getattr(details, "cached_tokens", 0) or 0

    def json_call(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        max_tokens: int = 16000,
        temperature: float | None = 0.2,
        label: str = "json_call",
    ) -> dict[str, Any]:
        """Call the model and return parsed JSON matching `schema`.

        Uses structured outputs when available so the response cannot fail to
        parse. This is the direct fix for the old reflect stage, whose prompt
        asked for "JSON" without declaring a schema -- so the key the caller
        read (`final_translation`) never existed and every page silently fell
        back to the raw draft.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            **self._params(max_tokens, temperature),
        }
        if self.caps.supports_json_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
        else:
            kwargs["response_format"] = {"type": "json_object"}
            kwargs["messages"] = [
                messages[0],
                {
                    "role": "user",
                    "content": user
                    + "\n\nReturn ONLY JSON matching this schema:\n"
                    + json.dumps(schema, ensure_ascii=False),
                },
            ]

        t0 = time.monotonic()
        resp = _with_retry(
            lambda: self.client.chat.completions.create(**kwargs), label=label
        )
        self.last_seconds = time.monotonic() - t0
        self._record(resp)

        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise LLMError(
                f"{label}: response hit the token limit ({max_tokens}) and was truncated. "
                "Refusing to use a partial translation."
            )
        content = choice.message.content
        if not content:
            raise LLMError(f"{label}: model returned empty content (finish_reason={choice.finish_reason})")
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMError(f"{label}: response was not valid JSON: {e}\n{content[:500]}") from e

    def vision_call(
        self,
        *,
        system: str,
        user: str,
        image_png: bytes,
        schema: dict[str, Any],
        schema_name: str,
        max_tokens: int = 4000,
        label: str = "vision_call",
    ) -> dict[str, Any]:
        """Send a rendered page image plus a prompt, and get structured findings back."""
        if not self.caps.supports_vision:
            raise LLMError(f"{self.model} has no vision support; cannot run the visual QA pass")

        b64 = base64.b64encode(image_png).decode()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"},
                        },
                    ],
                },
            ],
            **self._params(max_tokens, None),
        }
        if self.caps.supports_json_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }

        t0 = time.monotonic()
        resp = _with_retry(lambda: self.client.chat.completions.create(**kwargs), label=label)
        self.last_seconds = time.monotonic() - t0
        self._record(resp)
        content = resp.choices[0].message.content
        if not content:
            raise LLMError(f"{label}: empty vision response")
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMError(f"{label}: vision response was not valid JSON: {e}") from e

    def cost_report(self, price_in: float, price_out: float) -> str:
        u = self.usage
        billed_in = max(u["input"] - u["cached"], 0)
        cost = (billed_in * price_in + u["cached"] * price_in * 0.1 + u["output"] * price_out) / 1e6
        return (
            f"{u['calls']} calls | in {u['input']:,} (cached {u['cached']:,}) | "
            f"out {u['output']:,} | ~${cost:.2f}"
        )
