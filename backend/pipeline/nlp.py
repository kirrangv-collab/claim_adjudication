"""Shared, dependency-free text utilities used by the pipeline agents.

Includes phrase normalisation, sentence splitting tuned for CMS policy text,
a NegEx-style assertion classifier (present / negated / undocumented /
historical / family / hypothetical) and quantity extraction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into",
    "is", "it", "its", "of", "on", "or", "the", "to", "with", "was", "were", "has",
    "have", "had", "this", "that", "these", "those", "which", "who", "whom", "their",
    "his", "her", "patient", "patients", "any", "all", "other", "such", "than",
})

_IRREGULAR = {
    "lenses": "lens", "predominately": "predominantly", "haemorrhage": "hemorrhage",
    "oedema": "edema", "disc": "disk", "discs": "disk", "opthalmologist": "ophthalmologist",
    "bulbous": "bulbous",
}
_KEEP_S = ("ss", "us", "is", "ys", "as")


def stem(token: str) -> str:
    if token in _IRREGULAR:
        return _IRREGULAR[token]
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith(_KEEP_S):
        return token[:-1]
    return token


@dataclass(frozen=True)
class Token:
    text: str
    norm: str
    start: int
    end: int


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?|%|≥|≤")


def tokenize(text: str) -> list[Token]:
    tokens = []
    for match in _TOKEN_RE.finditer(text):
        raw = match.group(0)
        norm = stem(raw.casefold().replace("'s", "s").replace("'", ""))
        tokens.append(Token(raw, norm, match.start(), match.end()))
    return tokens


def normalize_phrase(text: str) -> str:
    return " ".join(token.norm for token in tokenize(text))


def content_tokens(text: str) -> list[str]:
    return [t.norm for t in tokenize(text) if t.norm not in STOP_WORDS and len(t.norm) > 1]


def clean_text(text: str) -> str:
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u00a0", " ")
    # PDF extraction splits hyphenated words across line breaks ("non- coverage").
    text = re.sub(r"(\w)- (\w)", r"\1-\2", text)
    return re.sub(r"\s+", " ", text).strip()


# --- Sentence splitting ----------------------------------------------------

_DOT = "\u2024"
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "Rev.", "Dr.", "No.", "U.S.", "approx.", "Fig.", "St.")
ENUM_OPEN, ENUM_CLOSE = "\u27e8", "\u27e9"


def _protect(text: str) -> str:
    for abbreviation in _ABBREVIATIONS:
        text = text.replace(abbreviation, abbreviation.replace(".", _DOT))
    text = re.sub(r"(\d)\.(\d)", rf"\1{_DOT}\2", text)
    # Enumerators like ": 1. X; and, 2. Y" become ⟨1⟩ markers so they survive splitting.
    text = re.sub(
        r"(?:(?<=[:;])|(?<=\band,)|(?<=\bor,)|(?<=\band)|(?<=\bor))\s*(\d{1,2})\.\s+",
        lambda m: f" {ENUM_OPEN}{m.group(1)}{ENUM_CLOSE} ",
        text,
    )
    out, depth = [], 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        out.append(_DOT if char == "." and depth else char)
    return "".join(out)


def split_sentences(text: str) -> list[str]:
    protected = _protect(text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"\u201c\u2022(])", protected)
    return [p.replace(_DOT, ".").strip() for p in parts if p.strip()]


def strip_enumerators(text: str) -> str:
    return re.sub(rf"\s*{ENUM_OPEN}\d+{ENUM_CLOSE}\s*", " ", text).strip()


# --- Assertion detection (NegEx-style) -------------------------------------

_PRE_NEGATION = (
    "no evidence of", "no signs of", "no sign of", "negative for", "free of", "absence of",
    "without", "denies", "denied", "ruled out", "rules out", "never", "not", "no", "non",
)
_PRE_UNDOCUMENTED = (
    "no documentation of", "no record of", "unknown", "undetermined", "missing",
    "unable to determine",
)
_PRE_HISTORICAL = (
    "past history of", "history of", "previously", "previous", "prior", "status post",
    "s p", "resolved", "former",
)
_PRE_FAMILY = ("family history of", "mother", "father", "sibling", "brother", "sister")
_PRE_HYPOTHETICAL = (
    "rule out", "r o", "possible", "suspected", "suspicion of", "concern for", "risk of",
    "to evaluate for", "evaluate for", "screening for", "cannot exclude", "may have",
)
_POST_UNDOCUMENTED = (
    "not documented", "not recorded", "not provided", "not available", "not reported",
    "not measured", "not assessed", "not stated", "not specified", "undocumented",
    "unknown", "pending",
)
_POST_NEGATION = ("not present", "not seen", "not found", "ruled out", "is absent", "are absent", "absent")
_SCOPE_BREAKERS = frozenset({
    "but", "however", "although", "though", "except", "aside", "apart", "yet", "whereas",
    "while", "presents", "reports", "documents", "shows", "confirms", "demonstrates",
    # ConText-style termination terms: a negation does not cross into a cause/association.
    "associated", "secondary", "due", "cause", "source", "origin", "etiology", "which", "who",
    "still", "nevertheless",
})
# Prefix negations only scope over the immediately following token ("non-diseased eyes").
_PREFIX_NEGATIONS = frozenset({"non"})
_MAX_SCOPE_TOKENS = 14


def _phrase_positions(tokens: list[Token], phrase: str) -> list[tuple[int, int]]:
    words = phrase.split()
    norms = [t.text.casefold() for t in tokens]
    hits = []
    for i in range(len(norms) - len(words) + 1):
        if norms[i:i + len(words)] == words:
            hits.append((i, i + len(words)))
    return hits


def _clauses(text: str) -> list[tuple[int, int]]:
    """Character spans of clauses: split on ';', sentence ends and 'but'."""
    spans, start = [], 0
    for match in re.finditer(r";|(?<=[.!?])\s|\bbut\b|\bhowever\b", text, flags=re.IGNORECASE):
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(text)))
    return [(a, b) for a, b in spans if b > a]


class AssertionClassifier:
    """Classifies the assertion status of a character span within a text."""

    def __init__(self, text: str):
        self.text = text
        self.clauses = []
        for a, b in _clauses(text):
            tokens = [Token(t.text, t.norm, t.start + a, t.end + a) for t in tokenize(text[a:b])]
            self.clauses.append((a, b, tokens))

    def classify(self, start: int, end: int) -> str:
        for a, b, tokens in self.clauses:
            if a <= start < b:
                return self._classify_in_clause(tokens, start, end)
        return "present"

    def _classify_in_clause(self, tokens: list[Token], start: int, end: int) -> str:
        first = next((i for i, t in enumerate(tokens) if t.end > start), len(tokens))
        last = max((i for i, t in enumerate(tokens) if t.start < end), default=first)

        def post(triggers) -> bool:
            for phrase in triggers:
                for s, _ in _phrase_positions(tokens, phrase):
                    if s > last and not any(tok.text.casefold() in _SCOPE_BREAKERS for tok in tokens[last + 1:s]):
                        return True
            return False

        def pre(triggers) -> bool:
            for phrase in triggers:
                for s, e in _phrase_positions(tokens, phrase):
                    if phrase in _PREFIX_NEGATIONS and e != first:
                        continue
                    if e <= first and first - e <= _MAX_SCOPE_TOKENS and not any(
                        tok.text.casefold() in _SCOPE_BREAKERS for tok in tokens[e:first]
                    ):
                        return True
            return False

        if post(_POST_UNDOCUMENTED):
            return "undocumented"
        if pre(_PRE_FAMILY):
            return "family"
        if pre(_PRE_UNDOCUMENTED):
            return "undocumented"
        if pre(_PRE_NEGATION) or post(_POST_NEGATION):
            return "negated"
        if pre(_PRE_HYPOTHETICAL):
            return "hypothetical"
        if pre(_PRE_HISTORICAL):
            return "historical"
        return "present"


# --- Quantities ------------------------------------------------------------

_UNIT_PATTERN = r"(disk areas?|disc areas?|letters?|%|percent|mm ?hg|diopters?|months?)"
_UNIT_CANON = {
    "disk area": "disk area", "disk areas": "disk area", "disc area": "disk area",
    "disc areas": "disk area", "letter": "letter", "letters": "letter", "%": "%",
    "percent": "%", "mm hg": "mmhg", "mmhg": "mmhg", "diopter": "diopter",
    "diopters": "diopter", "month": "month", "months": "month",
}


def canonical_unit(raw: str) -> str:
    return _UNIT_CANON.get(raw.casefold().strip(), raw.casefold().strip())


@dataclass(frozen=True)
class Quantity:
    value: float
    unit: str
    start: int
    end: int
    text: str


def extract_quantities(text: str) -> list[Quantity]:
    found = []
    for match in re.finditer(rf"(\d+(?:\.\d+)?)\s*{_UNIT_PATTERN}", text, flags=re.IGNORECASE):
        found.append(Quantity(float(match.group(1)), canonical_unit(match.group(2)), match.start(), match.end(), match.group(0)))
    return found


_CONSTRAINT_PATTERNS = (
    (rf"(\d+(?:\.\d+)?)\s*{_UNIT_PATTERN}\s*or\s*(?:less|fewer|smaller)", "<=", 1, 2),
    (rf"(\d+(?:\.\d+)?)\s*{_UNIT_PATTERN}\s*or\s*(?:more|greater|larger)", ">=", 1, 2),
    (rf"(?:at least|no less than|a minimum of)\s*(\d+(?:\.\d+)?)\s*{_UNIT_PATTERN}", ">=", 1, 2),
    (rf"(?:no more than|at most|up to|a maximum of)\s*(\d+(?:\.\d+)?)\s*{_UNIT_PATTERN}", "<=", 1, 2),
    (rf"(?:less than|fewer than|under)\s*(\d+(?:\.\d+)?)\s*{_UNIT_PATTERN}", "<", 1, 2),
    (rf"(?:more than|greater than|over)\s*(\d+(?:\.\d+)?)\s*{_UNIT_PATTERN}", ">", 1, 2),
    (r"(≥|>=)\s*(\d+(?:\.\d+)?)\s*(%)", ">=", 2, 3),
    (r"(≤|<=)\s*(\d+(?:\.\d+)?)\s*(%)", "<=", 2, 3),
)


def extract_constraint(text: str) -> tuple[str, float, str] | None:
    """Return (op, value, unit) for the first quantity constraint in ``text``."""
    for pattern, op, value_group, unit_group in _CONSTRAINT_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return op, float(match.group(value_group)), canonical_unit(match.group(unit_group))
    return None


def compare(value: float, op: str, threshold: float) -> bool:
    return {
        "<=": value <= threshold, ">=": value >= threshold, "<": value < threshold,
        ">": value > threshold, "==": value == threshold,
    }[op]
