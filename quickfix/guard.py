"""Clean the model's output and decide whether it is safe to paste over the user's text."""
from __future__ import annotations

import re
from dataclasses import dataclass

_FENCE = re.compile(r"^\s*```[^\n]*\n(.*?)\n?```\s*$", re.S)
_TAGS = re.compile(r"^\s*<text>\s*\n?(.*?)\n?\s*</text>\s*$", re.S)
_PREAMBLE = re.compile(
    r"^\s*(here(?:'s| is| are)\b[^\n]*|sure[,!.][^\n]*|certainly[,!.][^\n]*|of course[,!.][^\n]*|"
    r"corrected(?: text| version)?\s*:[^\n]*|"
    r"štai\b[^\n]*|pataisyt[a-zėąų]*(?: tekstas| variantas)?\s*:[^\n]*|žinoma[,!.][^\n]*)"
    r":?\s*\n+",
    re.I,
)
_TRAILER = re.compile(
    r"\n\s*\n\s*(note:|notes:|changes:|pastaba:|pastabos:|pakeitimai:|i (?:corrected|fixed|changed|made)\b|"
    r"pataisiau\b|ištaisiau\b)[^\n]*(?:\n[^\n]*)*\s*$",
    re.I,
)
_REFUSAL = re.compile(
    r"^\s*(i'm sorry|i am sorry|sorry,|i can't|i cannot|i can not|i won't|i'm unable|as an ai|"
    r"atsiprašau|negaliu|please provide|prašau pateikti)",
    re.I,
)
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.I)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


@dataclass
class Checked:
    text: str
    ok: bool
    reason: str = ""


def _strip_wrappers(output: str, original: str) -> str:
    text = output
    for _ in range(3):
        before = text
        if "```" not in original:
            match = _FENCE.match(text)
            if match:
                text = match.group(1)
        match = _TAGS.match(text)
        if match:
            text = match.group(1)
        match = _PREAMBLE.match(text)
        if match and not _PREAMBLE.match(original):
            text = text[match.end():]
        match = _TRAILER.search(text)
        if match and not _TRAILER.search(original):
            text = text[:match.start()]
        if len(text) >= 2 and text[0] == text[-1] == '"' and original.strip()[:1] != '"':
            text = text[1:-1]
        if text == before:
            break
    return text


def _match_shape(text: str, original: str) -> str:
    """Give the result the original's line endings and outer whitespace (selections often include them)."""
    text = text.replace("\r\n", "\n").strip()
    body = original.replace("\r\n", "\n")
    lead = body[: len(body) - len(body.lstrip())]
    trail = body[len(body.rstrip()):]
    result = lead + text + trail
    if "\r\n" in original:
        result = result.replace("\n", "\r\n")
    return result


def check(output: str, original: str, strict: bool) -> Checked:
    cleaned = _strip_wrappers(output or "", original)
    if not cleaned.strip():
        return Checked("", False, "Claude returned an empty result.")
    result = _match_shape(cleaned, original)

    if _REFUSAL.match(cleaned) and not _REFUSAL.match(original):
        return Checked(result, False, "The answer looks like a reply to you, not a corrected text.")

    new_len, old_len = len(cleaned.strip()), len(original.strip())
    slack = 40  # short messages may legitimately change a lot in relative terms
    if strict:
        low, high = old_len * 0.5 - slack, old_len * 1.5 + slack
    else:
        low, high = old_len * 0.1 - slack, old_len * 6 + 200
    if not low <= new_len <= high:
        return Checked(result, False, f"The length changed too much ({old_len} to {new_len} characters).")

    if strict:
        missing = [u for u in _URL.findall(original) if u not in cleaned]
        if missing:
            return Checked(result, False, "A link from the original is missing from the result.")
        if sorted(_EMOJI.findall(original)) != sorted(_EMOJI.findall(cleaned)):
            return Checked(result, False, "The emoji in the result differ from the original.")
    return Checked(result, True)
