"""Incremental release of a model's finish summary as message_delta events.

Replies are JSON actions, so the only user-facing text is action.summary of a finish
action. Everything else stays unseen until the complete reply has been validated.
"""

import json
from collections.abc import Callable

from .repo import PolicyError

MIN_RELEASE_CHARS = 32
MIN_HOLDBACK_CHARS = 64


class SummaryExtractor:
    """Scan partial JSON and return the decoded finish summary available so far.

    The tool must appear before the summary, as it does in the published schema.
    Otherwise nothing is released early and the validated summary arrives at the end.
    """

    def __init__(self):
        self.depth = 0
        self.in_action = False
        self.in_string = False
        self.escaped = False
        self.after_colon = False
        self.key = ""
        self.token: list[str] = []
        self.tool = ""
        self.capturing = False
        self.seen_summary = False
        self.body: list[str] = []
        self.text = ""

    def feed(self, raw: str) -> str:
        for char in raw:
            if self.in_string:
                self._string_char(char)
            elif char == '"':
                self.in_string = True
                self.token = []
                self.capturing = (
                    self.after_colon
                    and self.in_action
                    and self.depth == 2
                    and self.key == "summary"
                    and self.tool == "finish"
                    and not self.seen_summary
                )
            elif char in "{[":
                if char == "{" and self.depth == 1 and self.after_colon and self.key == "action":
                    self.in_action = True
                self.depth += 1
                self.after_colon = False
            elif char in "}]":
                self.depth -= 1
                if self.depth == 1:
                    self.in_action = False
            elif char == ":":
                self.after_colon = True
            elif char == ",":
                self.after_colon = False
        self._decode()
        return self.text

    def _string_char(self, char: str):
        if self.escaped:
            self.escaped = False
        elif char == "\\":
            self.escaped = True
        elif char == '"':
            self._string_end()
            return
        self.token.append(char)
        if self.capturing:
            self.body.append(char)

    def _string_end(self):
        value = "".join(self.token)
        self.in_string = False
        if self.capturing:
            self.capturing, self.seen_summary = False, True
        elif not self.after_colon:
            self.key = value
        elif self.in_action and self.depth == 2 and self.key == "tool":
            self.tool = value
        self.after_colon = False

    def _decode(self):
        raw = "".join(self.body)
        try:
            self.text = json.loads(f'"{raw[: complete_prefix(raw)]}"', strict=False)
        except ValueError:
            pass  # Malformed output is rejected by full validation; release nothing more.


def complete_prefix(raw: str) -> int:
    """Length of raw that ends outside any partial escape or surrogate pair."""
    index, safe = 0, 0
    while index < len(raw):
        if raw[index] != "\\":
            index += 1
        elif index + 1 >= len(raw):
            break
        elif raw[index + 1] != "u":
            index += 2
        elif index + 6 > len(raw):
            break
        elif raw[index + 2 : index + 4].lower() in {"d8", "d9", "da", "db"}:
            if index + 12 > len(raw):
                break
            index += 12
        else:
            index += 6
        safe = index
    return safe


class TextStream:
    """Release checked text, holding back enough that a partial secret is never shown."""

    def __init__(
        self,
        check: Callable[[str], None],
        emit: Callable[[str], None],
        secrets: list[str],
    ):
        self.check, self.emit = check, emit
        self.holdback = max([MIN_HOLDBACK_CHARS, *(len(secret) for secret in secrets)])
        self.extractor = SummaryExtractor()
        self.released = ""

    def feed(self, raw: str):
        text = self.extractor.feed(raw)
        if not text:
            return
        self.check(text)
        ready = text[: max(0, len(text) - self.holdback)]
        if len(ready) - len(self.released) >= MIN_RELEASE_CHARS:
            self.emit(ready[len(self.released) :])
            self.released = ready

    def remainder(self, summary: str) -> str:
        if not summary.startswith(self.released):
            raise PolicyError("Streamed text did not match the validated summary")
        return summary[len(self.released) :]
