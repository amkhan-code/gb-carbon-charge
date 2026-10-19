"""Errors an agent can act on.

Every failure reaches the agent as an MCP error result (``isError``) whose structured content is an
:class:`ErrorPayload`: a stable code, what was wrong, what would be valid, and what to try next. Three
paths produce one:

* a tool raises :class:`ToolFailure` (expected, domain-level problems);
* the :func:`guard` decorator turns any other exception into a safe ``INTERNAL`` error with a correlation
  id, logging the detail to stderr only;
* the SDK rejects arguments before the tool runs, and :func:`shape_errors` (middleware) rewrites its raw
  Pydantic text into the same payload.
"""

import difflib
import functools
import logging
import re
import uuid
from typing import Any, Literal

import mcp.types as types
from pydantic import BaseModel, Field

log = logging.getLogger("carbon_charge_mcp")

ErrorCode = Literal[
    "INVALID_ARGUMENT",      # an argument is malformed or fails validation
    "OUT_OF_RANGE",          # well-formed, but outside the data's coverage
    "UNKNOWN_NAME",          # a model, scenario or id that does not exist
    "NOT_A_CHARGING_NIGHT",  # the scenario does not charge on that night
    "INCOMPLETE_DATA",       # the data needed for this answer has gaps
    "NOT_AVAILABLE",         # this server or dataset cannot provide that
    "INTERNAL",              # a server fault; not caused by the arguments
]


class FieldProblem(BaseModel):
    field: str
    message: str
    got: str | None = None
    allowed_values: list[str] | None = None
    did_you_mean: list[str] = Field(default_factory=list)


class ErrorPayload(BaseModel):
    error_code: ErrorCode
    message: str
    field: str | None = None
    got: str | None = None
    expected: str | None = None
    allowed_values: list[str] | None = None
    valid_range: str | None = None
    did_you_mean: list[str] = Field(default_factory=list)
    hint: str | None = None
    retryable: bool = Field(default=False, description="True if correcting the arguments and calling again can succeed.")
    problems: list[FieldProblem] = Field(default_factory=list)
    correlation_id: str | None = None


class ToolFailure(Exception):
    """Raise from a tool to return a structured error to the agent."""

    def __init__(self, payload: ErrorPayload):
        super().__init__(payload.message)
        self.payload = payload


# "Retryable" means: correcting the arguments and calling again can succeed. It is true for every
# argument-class error, false for faults and for things this server cannot provide at all.
RETRYABLE = {"INVALID_ARGUMENT": True, "OUT_OF_RANGE": True, "UNKNOWN_NAME": True, "NOT_A_CHARGING_NIGHT": True,
             "INCOMPLETE_DATA": True, "NOT_AVAILABLE": False, "INTERNAL": False}


def failure(code: ErrorCode, message: str, **kw: Any) -> ToolFailure:
    kw.setdefault("retryable", RETRYABLE[code])
    return ToolFailure(ErrorPayload(error_code=code, message=message, **kw))


def suggest(got: str, options: list[str], n: int = 3) -> list[str]:
    """Close matches for a mistyped name, best first."""
    return difflib.get_close_matches(str(got), options, n=n, cutoff=0.5)


def _dump(payload: ErrorPayload) -> dict[str, Any]:
    d = payload.model_dump(mode="json", exclude_none=True)
    return {k: v for k, v in d.items() if v not in ([], "", None) or k == "retryable"}


def render(payload: ErrorPayload) -> str:
    """A compact, readable rendering for the text channel (the structured copy has the same facts)."""
    lines = [f"{payload.error_code}: {payload.message}"]
    for label, value in (("field", payload.field), ("got", payload.got), ("expected", payload.expected),
                         ("valid range", payload.valid_range)):
        if value:
            lines.append(f"  {label}: {value}")
    if payload.allowed_values:
        lines.append("  allowed values: " + ", ".join(payload.allowed_values))
    if payload.did_you_mean:
        lines.append("  did you mean: " + ", ".join(payload.did_you_mean))
    for p in payload.problems[1:]:
        lines.append(f"  also: {p.field}: {p.message}")
    if payload.hint:
        lines.append(f"  hint: {payload.hint}")
    if payload.correlation_id:
        lines.append(f"  correlation id: {payload.correlation_id}")
    lines.append(f"  retryable: {'yes' if payload.retryable else 'no'}")
    return "\n".join(lines)


def as_result(payload: ErrorPayload) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=render(payload))],
                                structured_content=_dump(payload), is_error=True)


def guard(fn):
    """Turn failures into agent-readable error results; never let an exception text reach the agent."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolFailure as exc:
            return as_result(exc.payload)
        except Exception:  # noqa: BLE001 - the whole point is to contain it
            cid = uuid.uuid4().hex[:8]
            log.exception("unexpected error in %s (correlation id %s)", fn.__name__, cid)
            return as_result(ErrorPayload(
                error_code="INTERNAL", correlation_id=cid,
                message="The server hit an unexpected error. This is a server fault, not a problem with your arguments.",
                hint="Retrying with the same arguments is unlikely to help. Report the correlation id if this persists."))

    return wrapper


# --- SDK-level validation failures ------------------------------------------------

# Optional per-argument hints, registered by the tools that own those arguments.
FIELD_HINTS: dict[str, str] = {}

_BLOCK = re.compile(
    r"(?m)^(?P<loc>\S[^\n]*)\n  (?P<msg>[^\n]*?) \[type=(?P<type>[^,\]]+), input_value=(?P<val>.*?), input_type=(?P<it>[^\]]+)\]")
_QUOTED = re.compile(r"'([^']+)'")


def _problem(loc: str, msg: str, etype: str, val: str) -> FieldProblem:
    field = loc.split(".")[0]
    got = val.strip("'\"") if val else None
    allowed = _QUOTED.findall(msg) if etype in ("literal_error", "enum") else None
    if etype == "missing":
        text = f"The required argument '{field}' is missing."
    elif allowed:
        text = f"'{got}' is not one of the allowed values."
    elif etype.startswith("date"):
        text = f"'{got}' is not a valid calendar date. Use ISO format YYYY-MM-DD, for example 2025-06-01."
    elif etype in ("int_parsing", "float_parsing", "bool_parsing"):
        text = f"'{got}' is not a valid {etype.split('_')[0]}."
    else:
        text = msg.rstrip(".") + "."
    return FieldProblem(field=field, message=text, got=None if etype == "missing" else got, allowed_values=allowed,
                        did_you_mean=suggest(got, allowed) if allowed and got else [])


def from_validation_text(text: str) -> ErrorPayload | None:
    """Parse the SDK's Pydantic validation message into an ErrorPayload (None if it is not one)."""
    if "validation error" not in text:
        return None
    problems = [_problem(m["loc"], m["msg"], m["type"], m["val"]) for m in _BLOCK.finditer(text)]
    if not problems:
        return None
    first = problems[0]
    return ErrorPayload(
        error_code="INVALID_ARGUMENT", message=first.message, field=first.field, got=first.got,
        allowed_values=first.allowed_values, did_you_mean=first.did_you_mean, problems=problems,
        hint=FIELD_HINTS.get(first.field) or ("Choose one of the allowed values." if first.allowed_values
                                              else "Check the tool's input schema (tools/list) for the expected types."),
        retryable=True)


async def shape_errors(ctx, call_next):
    """Middleware: give SDK-level validation failures the same structured shape as every other error."""
    result = await call_next(ctx)
    if (getattr(ctx, "method", None) == "tools/call" and isinstance(result, dict)
            and result.get("isError") and "structuredContent" not in result):
        text = next((c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)), "")
        payload = from_validation_text(text)
        if payload is None:  # an exception the guard did not see: mask it, never echo it
            cid = uuid.uuid4().hex[:8]
            log.error("unshaped tool error (correlation id %s): %s", cid, text[:300])
            payload = ErrorPayload(error_code="INTERNAL", correlation_id=cid,
                                   message="The server hit an unexpected error. This is a server fault, not a problem with your arguments.",
                                   hint="Retrying with the same arguments is unlikely to help.")
        result = {**result, "content": [{"type": "text", "text": render(payload)}], "structuredContent": _dump(payload)}
    return result
