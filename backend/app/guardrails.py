"""Input and output guardrails. Deliberately simple and deterministic - they run on every request.

Input:  size/type limits on uploads, question length, control-character stripping, an injection heuristic
        (logged, not blocked: blocking on keywords creates false positives; the prompt + delimiters are the
        real defence).
Output: citation validation - drop [n] markers that don't point at a retrieved excerpt, and flag answers
        that make claims without any citation so they show up in logs/metrics as a quality signal.
"""

import re

ALLOWED_EXTENSIONS = (".txt", ".vtt")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CITATION = re.compile(r"\[(\d{1,2})\]")
_INJECTION_HINTS = re.compile(
    r"ignore (all |any )?(previous|prior|above) instructions|system prompt|you are now|disregard the rules",
    re.IGNORECASE,
)
NOT_FOUND = "I couldn't find that in the selected meetings."


class GuardrailError(ValueError):
    pass


def check_upload(filename: str, size: int, max_bytes: int) -> None:
    if not filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise GuardrailError(f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")
    if size > max_bytes:
        raise GuardrailError(f"File too large ({size} bytes). Limit is {max_bytes} bytes.")
    if size == 0:
        raise GuardrailError("File is empty.")


def decode_upload(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise GuardrailError("File is not valid UTF-8/UTF-16 text.")


def clean_question(question: str, max_chars: int) -> str:
    q = _CONTROL.sub("", question).strip()
    if not q:
        raise GuardrailError("Question is empty.")
    if len(q) > max_chars:
        raise GuardrailError(f"Question too long ({len(q)} chars). Limit is {max_chars}.")
    return q


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION_HINTS.search(text))


def validate_citations(answer: str, n_excerpts: int) -> tuple[str, list[int], bool]:
    """Returns (answer with invalid markers removed, cited excerpt numbers, ungrounded flag)."""
    cited: list[int] = []

    def keep(m: re.Match) -> str:
        n = int(m.group(1))
        if 1 <= n <= n_excerpts:
            if n not in cited:
                cited.append(n)
            return m.group(0)
        return ""

    cleaned = _CITATION.sub(keep, answer)
    ungrounded = not cited and NOT_FOUND.lower() not in cleaned.lower()
    return cleaned, cited, ungrounded
