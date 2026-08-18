"""PII redaction for logs and error reporting.

Structured logs are the most common way personal data escapes a system: they
are shipped to third-party aggregators, retained far longer than the data
itself, and read by people who never signed a data processing agreement.

This module is applied as a logging filter, so redaction is the default rather
than something each call site has to remember.
"""
from __future__ import annotations

import logging
import re
from typing import Any

# Ordered most specific first so a PINFL is not partly eaten by a generic rule.
PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b\d{14}\b"), "<PINFL>"),
    (re.compile(r"\b[A-Z]{2}\s?\d{6,8}\b"), "<DOCNO>"),
    (re.compile(r"P<[A-Z]{3}[A-Z0-9<]{20,}"), "<MRZ>"),
    (re.compile(r"^[A-Z0-9<]{28,44}$", re.M), "<MRZ>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "<EMAIL>"),
    (re.compile(r"(?<!\d)(?:\+998|998)?\d{9}(?!\d)"), "<PHONE>"),
    (re.compile(r"\b(?:sk|AIza)[A-Za-z0-9_\-]{12,}\b"), "<APIKEY>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b(?=.*(?:tug|birth|рожд))", re.I), "<DATE>"),
]

# Field names whose VALUES are personal data wherever they appear.
SENSITIVE_KEYS = {
    "pinfl", "jshshir", "doc_number", "document_number", "passport",
    "surname", "given_name", "patronymic", "birth_date", "birth_place",
    "address", "phone", "email", "value", "secret", "api_key", "token",
    "password", "authorization",
}


def redact_text(text: str) -> str:
    if not text:
        return text
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_obj(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact a structure before it is logged or serialised."""
    if _depth > 8:
        return "<TRUNCATED>"
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in SENSITIVE_KEYS and isinstance(v, (str, int)):
                out[k] = "<REDACTED>"
            else:
                out[k] = redact_obj(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, _depth + 1) for v in obj]
    return obj


class RedactionFilter(logging.Filter):
    """Attach to every handler. Redaction must be the default, not opt-in."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = redact_obj(record.args)
            else:
                record.args = tuple(redact_obj(a) for a in record.args)
        return True


def install(logger: logging.Logger | None = None) -> None:
    root = logger or logging.getLogger()
    flt = RedactionFilter()
    root.addFilter(flt)
    for handler in root.handlers:
        handler.addFilter(flt)


def scrub_sentry_event(event: dict, hint: dict | None = None) -> dict:
    """before_send hook for Sentry."""
    return redact_obj(event)
