"""Agent 1: Policy Logic Agent.

Parses free-text coverage policy into structured rules whose conditions are
abstract syntax trees (``AND`` / ``OR`` / ``NOT`` / ``EXCEPT`` / ``UNLESS`` /
``ONLY_IF`` over predicate leaves). The parser is a deterministic,
rule-based grammar tuned to CMS National Coverage Determination language:

* section headers, "Not Covered" banners, cross-references and citations;
* effect detection (COVERED / NOT_COVERED / MAC-DISCRETIONARY), choosing the
  governing marker of each clause and splitting mixed clauses;
* subject extraction with anaphora resolution ("It", "Such cases", "The above
  2 indications ... only when", "These include ...", "In this situation");
* qualifier scoping: EXCEPT/UNLESS/ONLY IF bind to the clause they appear in
  (not to the whole document, which is the core failure of lexical rules);
* list structure: bullets, numbered enumerations (connector-aware), comma
  lists, "such as"/"e.g." expansions, and/or splits;
* definitions ("X must be documented by A, B, or C") expanded in place;
* quantity constraints ("4 disk areas or less").

When Gemini is configured, clauses the grammar parses with low confidence may
be re-parsed by the LLM; the result is accepted only if it passes structural
validation and every predicate is quoted from the clause itself.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from backend.pipeline.contracts import (
    ASTNode,
    PolicyDefinition,
    PolicyRule,
    PolicySectionInfo,
    QuantityConstraint,
    SkippedClause,
    StructuredPolicyRules,
)
from backend.pipeline.nlp import (
    ENUM_CLOSE,
    ENUM_OPEN,
    clean_text,
    content_tokens,
    extract_constraint,
    normalize_phrase,
    split_sentences,
    strip_enumerators,
)

logger = logging.getLogger(__name__)

# --- Lexicons ----------------------------------------------------------------

_HEADER_RE = re.compile(
    r"(?<![\w.])(?P<id>\d{2,3}(?:\.\d{1,2}){0,2})\s+-\s+(?P<title>.{3,160}?)\s+\(Rev\.[^)]*\)"
)
_BANNER_RE = re.compile(r"^\s*(Not Covered|Non-Covered|Noncovered|Covered)\b\s+(?=[A-Z])")
_SUBSECTION_RE = re.compile(
    r"(?:(?<=[.)])|^)\s*[A-D]\.\s+(?:General|Nationally Covered Indications|"
    r"Nationally Non-Covered Indications|Other)\b"
)

_POSITIVE = [
    (r"\b(?:is|are|be|been|being|remains?|continues? to be)\s+(?:only\s+|also\s+)?covered\b", "verb"),
    (r"\bcovered\s+(?:procedure|service|indication|benefit)s?\b", "verb"),
    (r"\b(?:may|would|will|shall|can)\s+(?:also\s+)?be\s+covered\b", "verb"),
    (r"\bpayment\s+may\s+be\s+made\b", "noun"),
    (r"\breimbursement\s+may\s+be\s+made\b", "noun"),
    (r"\bmake\s+(?:medicare\s+)?payment\b", "noun"),
    (r"\b(?:considered\s+)?reasonable\s+and\s+necessary\b", "verb"),
    (r"\b(?:is|are|was|were|be|been)\s+approved\s+for\b", "verb"),
    (r"\bcontinues?\s+to\s+be\s+approved\s+for\b", "verb"),
    (r"\beligible\s+for\s+coverage\b", "verb"),
]
_NEGATIVE = [
    (r"\bnon-?covered\b", "verb"),
    (r"\bnoncovered\b", "verb"),
    (r"\bnon-?coverage\b", "noun"),
    (r"\bnot\s+covered\b", "verb"),
    (r"\b(?:is|are)\s+(?:specifically\s+)?excluded(?:\s+from\s+(?:coverage|benefits))?\b", "verb"),
    (r"\bspecifically\s+excluded(?:\s+from\s+(?:coverage|benefits))?\b", "verb"),
    (r"\bexcluded\s+from\s+coverage\b", "verb"),
    (r"\bnot\s+(?:be\s+)?(?:payable|eligible\s+for\s+coverage)\b", "verb"),
    (r"\bnot\s+(?:considered\s+)?reasonable\s+and\s+necessary\b", "verb"),
    (r"\bno\s+payment\b", "noun"),
]
_DISCRETION = [r"\bdiscretion\b", r"\blocal\s+coverage\s+determinations?\b"]
_HISTORICAL = re.compile(r"\bintent\s+to\s+cover\b|\bnever\s+implemented\b|\bannounced\b", re.IGNORECASE)
_REQUIREMENT = re.compile(r"\bcoverage\s+is\s+restricted\s+to\b", re.IGNORECASE)
_ONLY_RE = re.compile(r"\bonly\s+(?:covered\s+)?(?:when|if)\b:?|\bonly\s+covered\s+(?=with\b)", re.IGNORECASE)
_ANAPHORIC_PREFIXES = (
    "it", "this", "these", "they", "such", "the above", "the lens", "the device", "the procedure",
    "the service", "the drug", "the subject", "necessary services", "services", "the technique",
)
_COPULA_RE = re.compile(
    r"\s(?:is|are|was|were|may|will|would|can|shall|should|must|continues?|remains?)\b", re.IGNORECASE
)
_SUBJECT_SEPARATORS = (
    " for the purpose of ", " for patients with ", " in patients with ", " for treating ", " for ",
    " to treat ", " that treats ", " that treat ", " with a diagnosis of ", " when ", " if ",
    " approved by ", " used ", " prescribed ", " furnished ", " in connection with ",
    " in the treatment of ", " by ",
)
_DISCOURSE_RE = re.compile(
    r"^(?:in addition|therefore|accordingly|thus|however|also|furthermore|"
    r"effective\s+[a-z]+\s+\d{1,2},?\s+\d{4}|on\s+[a-z]+\s+\d{1,2},?\s+\d{4}|"
    r"after [^,]*?(?:policy|review)|in this situation|in such a case|in such cases|"
    r"in this instance)\s*,?\s*",
    re.IGNORECASE,
)
_ANAPHORIC_CONDITION_RE = re.compile(
    r"\b(?:in|for)\s+(?:these|this|such)\s+(?:situations?|cases?|a case|instances?)\b|"
    r"\b(?:for\s+)?the above indications?\b",
    re.IGNORECASE,
)
_FILLERS = sorted([
    "under medicare", "when used for the diagnosis of", "when used in", "when used for", "when used",
    "used in", "used for", "used as", "for the treatment of", "in the treatment of", "for treating",
    "for patients with a diagnosis of", "for patients with", "in patients with",
    "patients with a diagnosis of", "with a diagnosis of", "a diagnosis of", "for a diagnosis of",
    "for patients who meet one or more of the following criteria", "who meet one or more of the following criteria",
    "for the following conditions", "the following conditions", "such conditions as", "the following",
    "for the purpose of", "for use in", "when it is used in the treatment of", "when it is used",
    "in the rare case", "as a prosthetic device", "to identify", "who", "that", "for", "with", "when",
    "if", "where", "in", "of", "and", "or", "and/or", "to", "the", "a", "an", "by", "as", "all other indications related to",
    "other indications related to", "other uses of", "have", "has", "are", "is",
], key=len, reverse=True)
_TRAILING_NOISE = [
    r"\s*,?\s*as determined by (?:an?\s+)?(?:fluorescein angiogram|fa)\b(?:\s*\([^)]*\))?",
    r"\s*\bat the initial visit\b", r"\s*\bat the time of initial treatment\b(?:\s+or)?",
    r"\s*\bwithin the \d+ months prior to initial treatment\b", r"\s*\bprior to initial treatment\b",
    r"\s*\bshould remain in effect\b", r"\s*\bnot already addressed by [^,;.]*",
    r"\s*\bas part of\b.*$", r",?\s*and not in addition to it\b", r"\s*\bin the rare case\b",
    r"\s*\bof diverse etiology\b", r"\s*\bunder the prosthetic device benefit\b",
    r"\s*\b(?:under|in|of|by|per)\s+(?:this|the)\s+(?:policy|section|manual|guideline|determination)\b",
]
_LIST_INTRO_RE = re.compile(
    r"(?:\b(?:for|in)\s+patients\s+)?\b(?:who|that)?\s*meets?\s+(?:one or more|any|all|at least one)\s+of\s+"
    r"the\s+following(?:\s+(?:criteria|conditions|indications))?\s*:?",
    re.IGNORECASE,
)
_STATUTE_RE = re.compile(
    r"(?:\b(?:under|by|in)\s+)?§+\s*\d+[a-z]?(?:\s*\([0-9a-zA-Z]+\))*(?:\s+of\s+the\s+(?:social\s+security\s+)?act\b)?",
    re.IGNORECASE,
)
_PROTECTED_COMPOUNDS = (
    ("food and drug administration", "food & drug administration"),
    ("centers for medicare and medicaid", "centers for medicare & medicaid"),
    ("classic and/or occult", "classic & occult"),
    ("reasonable and necessary", "reasonable & necessary"),
)
# Quantity comparators ("4 disk areas or less", "88% or less") must not be split
# as OR lists; only protected when they follow a number.
_QUANTITY_OR_RE = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:%|[a-z]+(?:\s[a-z]+)?)?)\s+or\s+(less|fewer|more|greater)\b", re.IGNORECASE
)


# --- Helpers ---------------------------------------------------------------------

class _IdGen:
    def __init__(self, prefix: str):
        self.prefix = prefix
        self.count = 0

    def __call__(self) -> str:
        self.count += 1
        return f"{self.prefix}.n{self.count}"


@dataclass
class _Marker:
    start: int
    end: int
    polarity: str
    kind: str
    text: str


@dataclass
class _SectionState:
    id: str | None
    title: str
    banner: str | None = None
    prev_rule: PolicyRule | None = None
    prev_sentence: str | None = None
    prev_sentence_rule: PolicyRule | None = None
    situation: str | None = None
    list_rule: PolicyRule | None = None
    rules: list[PolicyRule] = field(default_factory=list)


def _markers(clause: str) -> list[_Marker]:
    low = clause.casefold()
    negatives = [
        _Marker(m.start(), m.end(), "NOT_COVERED", kind, m.group(0))
        for pattern, kind in _NEGATIVE for m in re.finditer(pattern, low)
    ]
    positives = [
        _Marker(m.start(), m.end(), "COVERED", kind, m.group(0))
        for pattern, kind in _POSITIVE for m in re.finditer(pattern, low)
        if not any(n.start < m.end() and m.start() < n.end for n in negatives)
    ]
    found = sorted(negatives + positives, key=lambda m: (m.start, -(m.end - m.start)))
    deduped: list[_Marker] = []
    for marker in found:
        if deduped and marker.start < deduped[-1].end:
            continue
        deduped.append(marker)
    history = _HISTORICAL.search(clause)
    if history:
        deduped = [m for m in deduped if m.start > history.end()]
    return deduped


def _has_discretion(clause: str) -> bool:
    return any(re.search(p, clause, re.IGNORECASE) for p in _DISCRETION)


def _strip_fillers(text: str) -> str:
    text = text.strip(" ,;:.")
    changed = True
    while changed and text:
        changed = False
        low = text.casefold()
        for filler in _FILLERS:
            if low == filler or low.startswith((filler + " ", filler + ":")):
                text = text[len(filler):].strip(" ,;:.")
                changed = True
                break
    return text.strip(" ,;:.")


def _clean_condition_text(text: str) -> str:
    text = _STATUTE_RE.sub(" ", text)
    for pattern in _TRAILING_NOISE:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(\s*(?:where|see|effective)\b[^)]*\)", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" ,;:.")


def _mask_parens(text: str) -> tuple[str, list[str]]:
    saved: list[str] = []

    def repl(match):
        saved.append(match.group(0))
        return f"\u0000{len(saved) - 1}\u0000"

    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\([^()]*\)", repl, text)
    return text, saved


def _unmask(text: str, saved: list[str]) -> str:
    while "\u0000" in text:
        text = re.sub(r"\u0000(\d+)\u0000", lambda m: saved[int(m.group(1))], text)
    return text


def _is_anaphoric(subject: str) -> bool:
    low = subject.casefold().strip()
    if not low:
        return True
    return low.startswith(_ANAPHORIC_PREFIXES) or "such procedures" in low or "such cases" in low


def _clean_subject(text: str) -> str:
    text = _DISCOURSE_RE.sub("", text.strip(" ,;:."))
    text = _DISCOURSE_RE.sub("", text)
    text = re.sub(r"^(?:the\s+)?(?:use|uses)\s+of\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^other\s+uses\s+of\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+and$", "", text.strip(" ,;:."), flags=re.IGNORECASE)
    return text.strip(" ,;:.")


def _split_subject(text: str) -> tuple[str, str]:
    """Split "<subject> <separator> <condition>" at the earliest separator."""
    low = f" {text.casefold()} "
    best = None
    for separator in _SUBJECT_SEPARATORS:
        idx = low.find(separator)
        if idx >= 0 and (best is None or idx < best[0]):
            best = (idx, separator)
    if best is None:
        return text.strip(), ""
    idx, separator = best
    subject = text[: max(0, idx - 1)].strip()
    condition = text[idx - 1 + len(separator):].strip() if separator.strip() not in {"when", "if"} else text[idx - 1:].strip()
    return subject, condition


def _copula_cut(head: str) -> str:
    match = _COPULA_RE.search(f" {head}")
    return head[: max(0, match.start() - 1)] if match else head


# --- Condition grammar ---------------------------------------------------------------

class _ConditionParser:
    def __init__(self, ids: _IdGen):
        self.ids = ids
        self.long_predicates = 0

    def node(self, op: str, children: list[ASTNode] | None = None, text: str | None = None,
             quantity: QuantityConstraint | None = None) -> ASTNode:
        return ASTNode(id=self.ids(), op=op, children=children or [], text=text, quantity=quantity)

    def true(self) -> ASTNode:
        return self.node("TRUE")

    def combine(self, op: str, children: list[ASTNode | None]) -> ASTNode | None:
        kept = [c for c in children if c is not None and c.op != "TRUE"]
        if not kept:
            return None
        if len(kept) == 1:
            return kept[0]
        flattened = []
        for child in kept:
            flattened.extend(child.children if child.op == op else [child])
        return self.node(op, flattened)

    def predicate(self, text: str) -> ASTNode | None:
        text = strip_enumerators(text)
        text = _strip_fillers(_clean_condition_text(text))
        text = re.sub(r"^(?:\u2022\s*)+", "", text).strip(" ,;:.")
        text = re.sub(r"[;,]?\s*\b(?:and|or)\b\s*,?$", "", text).strip(" ,;:.")
        if not text or not content_tokens(re.sub(r"\([^)]*\)", "", text) or text):
            return None
        constraint = extract_constraint(text)
        quantity = QuantityConstraint(op=constraint[0], value=constraint[1], unit=constraint[2]) if constraint else None
        if len(text.split()) > 18:
            self.long_predicates += 1
        negation = re.match(r"^(?:no|not|without)\s+(.+)$", text, flags=re.IGNORECASE)
        if negation and not quantity:
            return self.node("NOT", [self.node("PRED", text=negation.group(1))])
        return self.node("PRED", text=text, quantity=quantity)

    def parse(self, text: str, connector_mode: bool = False) -> ASTNode | None:
        text = _clean_condition_text(text)
        if not text or not content_tokens(text):
            return None
        masked, saved = _mask_parens(text)

        # Qualifiers bind to the clause they appear in.
        for paren_match in re.finditer(r"\u0000(\d+)\u0000", masked):
            chunk = saved[int(paren_match.group(1))]
            if chunk.casefold().startswith("(except"):
                exception = _unmask(chunk, saved)[len("(except"):-1]
                base = _unmask(masked[: paren_match.start()] + masked[paren_match.end():], saved)
                return self.node("EXCEPT", [
                    self.parse(base, connector_mode) or self.true(),
                    self.parse(exception) or self.true(),
                ])
        for keyword, op in ((r",?\s*\bexcept(?:\s+for|\s+when|\s+in)?\b", "EXCEPT"), (r",?\s*\bunless\b", "UNLESS")):
            match = re.search(keyword, masked, flags=re.IGNORECASE)
            if match:
                base = _unmask(masked[: match.start()], saved)
                qualifier = _unmask(masked[match.end():], saved)
                return self.node(op, [self.parse(base, connector_mode) or self.true(), self.parse(qualifier) or self.true()])
        only = _ONLY_RE.search(masked)
        if only:
            base = _unmask(masked[: only.start()], saved)
            qualifier = _unmask(masked[only.end():], saved)
            return self.node("ONLY_IF", [self.parse(base) or self.true(), self.parse(qualifier, connector_mode=True) or self.true()])

        if "\u2022" in masked:
            intro, *items = masked.split("\u2022")
            all_of = re.search(r"\ball of the following\b", intro, flags=re.IGNORECASE)
            listed = self.combine("AND" if all_of else "OR", [self.predicate(_unmask(i, saved)) for i in items])
            intro_text = _LIST_INTRO_RE.sub("", _unmask(intro, saved))
            intro_node = self.parse(intro_text) if re.search(r"necessary|approved|fda", intro_text, re.IGNORECASE) else None
            return self.combine("AND", [intro_node, listed])

        if ENUM_OPEN in masked:
            intro, *rest = re.split(rf"{ENUM_OPEN}\d+{ENUM_CLOSE}", masked)
            conjunctive = connector_mode and bool(re.search(rf"(?:;|,)\s*and,?\s*{ENUM_OPEN}", masked, flags=re.IGNORECASE))
            listed = self.combine("AND" if conjunctive else "OR", [self.parse(_unmask(i, saved)) for i in rest])
            intro_text = _LIST_INTRO_RE.sub("", _unmask(intro, saved))
            intro_node = self.parse(intro_text) if content_tokens(_strip_fillers(intro_text)) else None
            return self.combine("AND", [intro_node, listed])

        # "other X (e.g., A, B, or C)" / "X such as A or B"
        for idx, chunk in enumerate(saved):
            if re.match(r"\(\s*(?:e\.g\.|i\.e\.|such as)", chunk, flags=re.IGNORECASE):
                marker = f"\u0000{idx}\u0000"
                if marker in masked:
                    head = _unmask(masked.split(marker)[0], saved).strip(" ,")
                    tail = _unmask(masked.split(marker, 1)[1], saved)
                    inner = re.sub(r"^\(\s*(?:e\.g\.|i\.e\.|such as)\s*,?\s*", "", chunk, flags=re.IGNORECASE)[:-1]
                    examples = self._examples(head, inner)
                    expanded = examples if re.match(r"^(?:other|all other)\b", _strip_fillers(head), re.IGNORECASE) \
                        else self.combine("OR", [self.predicate(head), examples])
                    return self.combine("AND", [expanded, self.parse(tail) if content_tokens(tail) else None])
        such_as = re.search(r",?\s*\bsuch as\b", masked, flags=re.IGNORECASE)
        if such_as:
            head = _unmask(masked[: such_as.start()], saved)
            examples = self._examples(head, _unmask(masked[such_as.end():], saved))
            if re.match(r"^(?:other|all other)\b", _strip_fillers(head), re.IGNORECASE) or not content_tokens(_strip_fillers(head)):
                return examples
            return self.combine("OR", [self.predicate(head), examples])
        masked = re.sub(r"\bsuch conditions as\b", "", masked, flags=re.IGNORECASE)
        # "<purpose> in patients with <indications>": the indications are the condition.
        population = re.search(r"\b(?:in|for)\s+patients\s+(?:with|who)\b", masked, flags=re.IGNORECASE)
        if population and content_tokens(masked[population.end():]):
            return self.parse(_unmask(masked[population.end():], saved), connector_mode)

        protected = masked
        for original, replacement in _PROTECTED_COMPOUNDS:
            protected = re.sub(rf"\b{re.escape(original)}\b", replacement, protected, flags=re.IGNORECASE)
        protected = _QUANTITY_OR_RE.sub(lambda m: f"{m.group(1)} or\u2011{m.group(2)}", protected)
        segments = [s for s in re.split(r",\s*", protected) if s.strip()]
        if len(segments) >= 3 or (len(segments) == 2 and re.search(r"\b(?:and|or|and/or)\b", segments[1], re.IGNORECASE)):
            items = []
            for segment in segments:
                segment = re.sub(r"^(?:and/or|and|or)\s+", "", segment.strip(), flags=re.IGNORECASE)
                parts = [p.strip() for p in re.split(r"\s+(?:and/or|or|and)\s+", segment) if p.strip()]
                # "atrophic or dry AMD" -> "atrophic AMD", "dry AMD"
                if len(parts) > 1 and len(re.sub(r"\u0000\d+\u0000", "", parts[-1]).split()) >= 2:
                    head_noun = re.findall(r"[A-Za-z]+", re.sub(r"\u0000\d+\u0000", "", parts[-1]))[-1]
                    parts = [f"{p} {head_noun}" if len(p.split()) == 1 else p for p in parts[:-1]] + [parts[-1]]
                items.extend(parts)
            op = "AND" if re.search(r"\ball of the following\b", protected, re.IGNORECASE) else "OR"
            return self.combine(op, [self.predicate(self._restore(i, saved)) for i in items])
        for pattern, op, min_tokens in ((r"\s+(?:and/or|or)\s+", "OR", 1), (r"\s+and\s+", "AND", 2)):
            parts = [p for p in re.split(pattern, protected) if p.strip()]
            if len(parts) >= 2 and all(len(content_tokens(self._restore(p, saved))) >= min_tokens for p in parts):
                return self.combine(op, [self.parse(self._restore(p, saved), connector_mode) for p in parts])
        return self.predicate(self._restore(protected, saved))

    @staticmethod
    def _restore(text: str, saved: list[str]) -> str:
        text = _unmask(text, saved)
        for original, replacement in _PROTECTED_COMPOUNDS:
            text = text.replace(replacement, original)
        return text.replace(" or\u2011", " or ")

    def _examples(self, head: str, inner: str) -> ASTNode | None:
        # Only vague heads ("other types of AMD (e.g., atrophic, or dry AMD)") lend
        # their noun to bare adjectives in the example list.
        vague = bool(re.match(r"^(?:other|all other)\b", _strip_fillers(head), re.IGNORECASE))
        head_tokens = re.findall(r"[A-Za-z]+", head)
        noun = head_tokens[-1] if head_tokens and vague else ""
        items = [p for p in re.split(r",\s*(?:and/or|or|and)?\s*|\s+(?:and/or|or|and)\s+", inner) if p.strip()]
        nodes = []
        for item in items:
            item = item.strip()
            if len(item.split()) == 1 and noun and noun.casefold() != item.casefold():
                item = f"{item} {noun}"
            nodes.append(self.predicate(item))
        return self.combine("OR", nodes)


def _copy_with_new_ids(node: ASTNode, ids: _IdGen) -> ASTNode:
    return ASTNode(
        id=ids(), op=node.op, text=node.text, quantity=node.quantity,
        children=[_copy_with_new_ids(c, ids) for c in node.children],
    )


# --- Agent ---------------------------------------------------------------------------

def _split_sections(text: str) -> list[_SectionState]:
    headers = list(_HEADER_RE.finditer(text))
    if not headers:
        return [_SectionState(id=None, title="")], [text]
    states, bodies = [], []
    if text[: headers[0].start()].strip():
        states.append(_SectionState(id=None, title=""))
        bodies.append(text[: headers[0].start()])
    for i, header in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        title = re.sub(r"\s+-\s+Effective.*$", "", header.group("title")).strip()
        states.append(_SectionState(id=header.group("id"), title=title))
        bodies.append(text[header.end():end])
    return states, bodies


def _clean_body(body: str, state: _SectionState) -> str:
    body = re.sub(r"\bCIM\s*-?\s*\d+(?:[-.]\d+)*", " ", body)
    body = re.sub(r"Cross-references?:.*$", "", body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"\(\s*see\b[^)]*\)", "", body, flags=re.IGNORECASE)

    def quoted(match):
        inner = match.group(1)
        words = inner.split()
        title_like = len(words) >= 2 and all(w[:1].isupper() or not w[:1].isalpha() for w in words)
        return "" if title_like else inner

    body = re.sub(r"[\u201c\"]([^\u201d\"]*)[\u201d\"]", quoted, body)
    body = clean_text(body)
    banner = _BANNER_RE.match(body)
    if banner:
        state.banner = "NOT_COVERED" if "not" in banner.group(1).casefold() or "non" in banner.group(1).casefold() else "COVERED"
        body = body[banner.end():]
    body = _SUBSECTION_RE.sub(". ", body)
    body = re.sub(r"\b(.{8,80}?)\s+\1\b", r"\1", body)
    body = re.sub(r"\s+-\s+(?=[A-Z])", ". ", body)
    return clean_text(body)


def _abbreviations(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in re.finditer(r"\(([A-Z]{2,6})s?\)", text):
        abbr = match.group(1)
        words = re.findall(r"[A-Za-z][\w'-]*", text[max(0, match.start() - 120): match.start()])
        for length in range(len(abbr) - 1, len(abbr) + 2):
            if length <= 0 or length > len(words):
                continue
            candidate = words[-length:]
            if candidate[0][0].casefold() != abbr[0].casefold():
                continue
            long_form = " ".join(candidate)
            pos = 0
            ok = True
            for letter in abbr.casefold():
                pos = long_form.casefold().find(letter, pos)
                if pos < 0:
                    ok = False
                    break
                pos += 1
            if ok:
                found.setdefault(abbr, long_form)
                break
    return found


def _clauses(sentence: str) -> list[str]:
    parts = re.split(r"\s*;\s*however,?\s*|,?\s+but\s+|\s+and\s+(?=will\b)", sentence, flags=re.IGNORECASE)
    if len(parts) > 1 and all(_markers(p) for p in parts):
        return parts
    return [sentence]


class PolicyLogicAgent:
    name = "policy_logic"

    def parse(self, policy_text: str) -> StructuredPolicyRules:
        states, bodies = _split_sections(clean_text(policy_text))
        rules: list[PolicyRule] = []
        skipped: list[SkippedClause] = []
        definitions: list[PolicyDefinition] = []
        abbreviations = _abbreviations(policy_text)
        counter = [0]

        for state, body in zip(states, bodies):
            body = _clean_body(body, state)
            if state.banner and state.title:
                counter[0] += 1
                rid = f"R{counter[0]}"
                rules.append(PolicyRule(
                    id=rid, section=state.id, effect=state.banner, modality="sufficient",
                    subject=state.title, subject_is_anaphoric=False,
                    condition=ASTNode(id=f"{rid}.n1", op="TRUE"),
                    source_text=f"Section {state.id} banner: {state.banner.replace('_', ' ').title()}",
                    parse_confidence=0.9,
                ))
            for sentence in split_sentences(body):
                for clause in _clauses(sentence):
                    self._clause(clause, state, rules, skipped, definitions, counter)
                state.prev_sentence = sentence

        self._expand_definitions(rules, definitions)
        return StructuredPolicyRules(
            sections=[PolicySectionInfo(id=s.id or "-", title=s.title, banner=s.banner) for s in states if s.id or s.rules],
            rules=rules,
            definitions=definitions,
            abbreviations=abbreviations,
            skipped_clauses=skipped[:40],
            notes=[f"{len(skipped)} non-normative clause(s) skipped."] if skipped else [],
        )

    # -- clause handling --------------------------------------------------------

    def _clause(self, clause, state, rules, skipped, definitions, counter):
        text = clause.strip()
        if len(content_tokens(text)) < 2:
            return
        if re.match(r"^(?:see|also,?\s+see)\b", text, re.IGNORECASE):
            skipped.append(SkippedClause(text=text, reason="cross_reference"))
            return
        if _REQUIREMENT.search(text):
            skipped.append(SkippedClause(text=text, reason="provider_requirement"))
            return
        definition = re.match(
            r"^(?P<term>[^,;]{3,60}?)\s+(?:must be documented by|is defined as|are defined as|(?<!by )means)\s+(?P<body>.+)$",
            text, re.IGNORECASE,
        )
        markers = _markers(text)
        if definition and not markers:
            ids = _IdGen(f"D{len(definitions) + 1}")
            parser = _ConditionParser(ids)
            body = re.sub(r"\([^)]*\)", "", definition.group("body"))
            expansion = parser.parse(body)
            if expansion is not None:
                definitions.append(PolicyDefinition(term=definition.group("term").strip(), expansion=expansion, source_text=text))
            return
        situation = re.match(r"^(?:where|when|if)\s+(.+?),\s", text, re.IGNORECASE)
        if situation and not markers:
            state.situation = situation.group(1)
        if re.match(r"^these include\b", text, re.IGNORECASE) and state.prev_rule is not None and not markers:
            self._extension_rule(text, state, rules, counter)
            return
        if text.startswith("\u2022") and not markers and state.list_rule is not None:
            self._append_list_item(text, state)
            return
        if not markers:
            reason = "historical" if _HISTORICAL.search(text) else "no_coverage_effect"
            skipped.append(SkippedClause(text=text, reason=reason))
            state.prev_sentence_rule = None
            return
        rule = self._rule_from_clause(text, markers, state, counter)
        if rule is not None:
            rules.append(rule)
            state.rules.append(rule)
            state.prev_rule = rule
            state.prev_sentence_rule = rule

    def _rule_from_clause(self, text, markers, state, counter) -> PolicyRule | None:
        # The governing marker is the first one, unless a complement clause
        # ("CMS determines that X is covered") introduces a later marker.
        complements = [m.end() for m in re.finditer(r"\b(?:that|whether)\s+", text, flags=re.IGNORECASE)]
        governing = markers[0]
        if complements and any(m.start > complements[-1] for m in markers):
            governing = next(m for m in markers if m.start > complements[-1])
        effect = "DISCRETIONARY" if _has_discretion(text) else governing.polarity
        head = text[: governing.start]
        complement = list(re.finditer(r"\b(?:that|whether)\s+", head, flags=re.IGNORECASE))
        if complement:
            head = head[complement[-1].end():]
        tail = text[governing.end:]
        # Later markers with the same effect ("is not covered and is excluded
        # from benefits") restate the effect; they are not conditions.
        for marker in reversed(markers):
            if marker.start >= governing.end and marker.polarity == governing.polarity:
                start, end = marker.start - governing.end, marker.end - governing.end
                tail = tail[:start] + " " + tail[end:]
        tail = re.sub(r"^\s*(?:,\s*)?and\s*$", "", tail)
        only_marker = "only" in governing.text or bool(re.match(r"^\s*only\s+(?:when|if)\b", tail, re.IGNORECASE))

        # The above N indications ... only when: merge into the previous rule.
        if re.match(r"^\s*the above\b", head, re.IGNORECASE) and state.prev_rule is not None and (only_marker or _ONLY_RE.search(tail)):
            return self._merge_only_if(text, tail, state)

        ids_prefix = f"R{counter[0] + 1}"
        ids = _IdGen(ids_prefix)
        parser = _ConditionParser(ids)
        head_conditions: list[str] = []
        head = _DISCOURSE_RE.sub("", head.strip())
        for _ in range(3):
            leading = re.match(r"^(?:for|in)\s+patients\s+with\s+([^,]+),\s*|^(?:when|if|where)\s+([^,]+),\s*", head, re.IGNORECASE)
            if not leading:
                break
            head_conditions.append(leading.group(1) or leading.group(2))
            head = head[leading.end():]

        subject_conditions: list[str] = []
        if governing.kind == "noun" or not _clean_subject(_copula_cut(head)):
            tail_text = re.sub(r"^\s*(?:policy|instruction)\b", "", _STATUTE_RE.sub(" ", tail), flags=re.IGNORECASE)
            tail_text = re.sub(r"^[\s,]*(?:under the prosthetic device benefit)?[\s,]*(?:however,?)?\s*", "", tail_text, flags=re.IGNORECASE)
            for_match = re.match(r"^\s*for\s+(?:a|an|the)?\s*(.+)$", tail_text, re.IGNORECASE | re.DOTALL)
            if for_match and governing.kind == "noun":
                subject, remainder = _split_subject(for_match.group(1))
                tail = remainder
            else:
                subject = ""
        else:
            subject = _clean_subject(_copula_cut(head))
            subject, extra = _split_subject(subject)
            if extra:
                subject_conditions.append(extra)
        subject = _clean_subject(subject)

        anaphoric = _is_anaphoric(subject)
        inherited: ASTNode | None = None
        if anaphoric:
            low = subject.casefold()
            if ("such" in low or not subject) and state.prev_sentence_rule is not None:
                subject = state.prev_sentence_rule.subject
                if "such" in low:
                    inherited = _copy_with_new_ids(state.prev_sentence_rule.condition, ids)
            elif "such" in low and state.prev_sentence:
                prior_subject, prior_condition = _split_subject(_clean_subject(_copula_cut(state.prev_sentence)))
                subject = _clean_subject(prior_subject) or state.title
                if prior_condition:
                    subject_conditions.append(prior_condition)
            elif state.prev_rule is not None and not low.startswith(("it", "this", "the lens", "the device", "necessary services", "services")):
                subject = state.prev_rule.subject
            else:
                subject = (state.prev_rule.subject if state.prev_rule and low.startswith(("this", "the use")) else state.title) or subject

        tail = re.sub(r"^\s*(?:only\s+)?", "", tail)
        residual = bool(re.search(r"\bother uses\b|\bnot already addressed\b|\ball other indications\b", text, re.IGNORECASE)) and effect == "NOT_COVERED"

        anaphoric_condition = None
        if _ANAPHORIC_CONDITION_RE.search(text):
            tail = _ANAPHORIC_CONDITION_RE.sub(" ", tail)
            antecedent = None
            if state.situation:
                antecedent = parser.parse(state.situation)
            elif state.prev_rule is not None:
                antecedent = _copy_with_new_ids(state.prev_rule.condition, ids)
            anaphoric_condition = antecedent

        necessary_constraint = only_marker and not head_conditions and not subject_conditions and inherited is None
        condition_node = parser.parse(tail, connector_mode=only_marker)
        if only_marker:
            base = parser.combine("AND", [parser.parse(c) for c in head_conditions + subject_conditions] + [inherited])
            condition = parser.node("ONLY_IF", [base or parser.true(), condition_node or parser.true()])
        else:
            parts = [parser.parse(c) for c in head_conditions + subject_conditions] + [inherited, anaphoric_condition, condition_node]
            if effect == "COVERED" and re.search(r"(?:reasonable|medically)\s+(?:and\s+)?necessary", text, re.IGNORECASE) \
                    and not re.search(r"(?:reasonable|medically)\s+(?:and\s+)?necessary", tail, re.IGNORECASE):
                parts.append(parser.node("PRED", text="reasonable and necessary"))
            condition = parser.combine("AND", parts) or parser.true()

        confidence = 1.0
        confidence -= 0.25 * min(parser.long_predicates, 2)
        if anaphoric and subject == state.title:
            confidence -= 0.15
        if not subject:
            confidence -= 0.2
        if len({m.polarity for m in markers}) > 1:
            confidence -= 0.15
        counter[0] += 1
        rule = PolicyRule(
            id=ids_prefix,
            section=state.id,
            effect=effect,
            modality="residual" if residual else ("necessary" if necessary_constraint and effect == "COVERED" else "sufficient"),
            subject=subject,
            subject_is_anaphoric=anaphoric,
            condition=condition,
            source_text=text,
            parse_confidence=max(0.2, round(confidence, 2)),
        )
        if "\u2022" in tail or ENUM_OPEN in tail:
            state.list_rule = rule
        return rule

    def _merge_only_if(self, text, tail, state) -> PolicyRule | None:
        previous = state.prev_rule
        ids = _IdGen(previous.id + "q")
        parser = _ConditionParser(ids)
        only = _ONLY_RE.search(tail)
        qualifier_text = tail[only.end():] if only else tail
        qualifier = parser.parse(qualifier_text, connector_mode=True)
        if qualifier is None:
            return None
        merged = previous.model_copy(update={
            "condition": ASTNode(id=ids(), op="ONLY_IF", children=[previous.condition, qualifier]),
            "source_text": f"{previous.source_text} {text}",
        })
        rules_index = state.rules.index(previous)
        state.rules[rules_index] = merged
        state.prev_rule = merged
        state.prev_sentence_rule = merged
        if state.list_rule is previous:
            state.list_rule = merged
        self._pending_replacements.append((previous, merged))
        return None

    def _extension_rule(self, text, state, rules, counter):
        previous = state.prev_rule
        counter[0] += 1
        rid = f"R{counter[0]}"
        parser = _ConditionParser(_IdGen(rid))
        body = re.sub(r"^these include,?\s*(?:but are not limited to,?)?\s*(?:the following)?[^:]*:?", "", text, flags=re.IGNORECASE)
        condition = parser.parse(body)
        if condition is None:
            return
        rule = PolicyRule(
            id=rid, section=state.id, effect=previous.effect, modality="sufficient",
            subject=previous.subject, subject_is_anaphoric=True, condition=condition,
            source_text=text, parse_confidence=0.8,
        )
        rules.append(rule)
        state.rules.append(rule)
        state.prev_sentence_rule = rule

    def _append_list_item(self, text, state):
        target = state.list_rule
        parser = _ConditionParser(_IdGen(target.id + "b"))
        item = parser.predicate(text.lstrip("\u2022 "))
        if item is None:
            return

        def add(node: ASTNode) -> ASTNode:
            if node.op == "OR":
                return node.model_copy(update={"children": [*node.children, item]})
            if node.op in {"AND", "ONLY_IF"} and node.children:
                return node.model_copy(update={"children": [*node.children[:-1], add(node.children[-1])]})
            return ASTNode(id=parser.ids(), op="OR", children=[node, item])

        updated = target.model_copy(update={"condition": add(target.condition), "source_text": f"{target.source_text} {text}"})
        self._pending_replacements.append((target, updated))
        state.rules[state.rules.index(target)] = updated
        if state.prev_rule is target:
            state.prev_rule = updated
        state.list_rule = updated

    # -- post-processing -----------------------------------------------------------

    @staticmethod
    def _expand_definitions(rules: list[PolicyRule], definitions: list[PolicyDefinition]) -> None:
        if not definitions:
            return
        terms = [(normalize_phrase(d.term), d) for d in definitions]

        def expand(node: ASTNode, ids: _IdGen) -> ASTNode:
            if node.op == "PRED" and node.text:
                norm = normalize_phrase(node.text)
                for term, definition in terms:
                    if term and term in norm:
                        return _copy_with_new_ids(definition.expansion, ids)
            return node.model_copy(update={"children": [expand(c, ids) for c in node.children]})

        for index, rule in enumerate(rules):
            rules[index] = rule.model_copy(update={"condition": expand(rule.condition, _IdGen(rule.id + "d"))})

    def run(self, policy_text: str) -> StructuredPolicyRules:
        self._pending_replacements: list[tuple[PolicyRule, PolicyRule]] = []
        result = self.parse(policy_text)
        rules = list(result.rules)
        for old, new in self._pending_replacements:
            for index, rule in enumerate(rules):
                if rule.id == old.id:
                    rules[index] = new
        if self._pending_replacements:
            self._expand_definitions(rules, result.definitions)
        # Internal enumerator markers (⟨1⟩) are parsing aids; show "1." to readers.
        return result.model_copy(update={
            "rules": [r.model_copy(update={"source_text": _display(r.source_text)}) for r in rules],
            "definitions": [d.model_copy(update={"source_text": _display(d.source_text)}) for d in result.definitions],
            "skipped_clauses": [s.model_copy(update={"text": _display(s.text)}) for s in result.skipped_clauses],
        })


def _display(text: str) -> str:
    return re.sub(rf"{ENUM_OPEN}(\d+){ENUM_CLOSE}", r"\1.", text)


# --- Optional LLM assist ------------------------------------------------------------

_LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "root": {"type": "string"},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "op": {"type": "string", "enum": ["PRED", "AND", "OR", "NOT", "EXCEPT", "UNLESS", "ONLY_IF", "TRUE"]},
                    "text": {"type": "string"},
                    "children": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "op"],
            },
        },
    },
    "required": ["root", "nodes"],
}
_LLM_PROMPT = (
    "You convert one insurance coverage-policy clause into a logic tree for its CONDITION "
    "(when the clause's coverage effect applies). Operators: AND, OR, NOT, EXCEPT(base, exception), "
    "UNLESS(base, condition), ONLY_IF(base, condition), PRED (leaf), TRUE (unconditional). "
    "Every PRED text MUST be copied verbatim from the clause. Do not add outside knowledge. "
    "Return JSON {root, nodes:[{id, op, text, children:[ids]}]}."
)
MAX_LLM_ASSISTS = 3


def _validate_llm_tree(data: dict, clause: str, rule_id: str) -> ASTNode:
    nodes = {n["id"]: n for n in data["nodes"]}
    if not 1 <= len(nodes) <= 25 or data["root"] not in nodes:
        raise ValueError("invalid node set")
    clause_norm = normalize_phrase(clause)
    ids = _IdGen(rule_id + "llm")

    def build(node_id: str, depth: int, seen: frozenset) -> ASTNode:
        if depth > 6 or node_id in seen:
            raise ValueError("tree too deep or cyclic")
        raw = nodes[node_id]
        op = raw["op"]
        children = raw.get("children") or []
        if op == "PRED":
            text = (raw.get("text") or "").strip()
            if not text or normalize_phrase(text) not in clause_norm:
                raise ValueError(f"predicate not quoted from clause: {text!r}")
            constraint = extract_constraint(text)
            return ASTNode(id=ids(), op="PRED", text=text, quantity=QuantityConstraint(
                op=constraint[0], value=constraint[1], unit=constraint[2]) if constraint else None)
        if op == "TRUE":
            return ASTNode(id=ids(), op="TRUE")
        arity = {"NOT": 1, "EXCEPT": 2, "UNLESS": 2, "ONLY_IF": 2}.get(op)
        if (arity and len(children) != arity) or (op in {"AND", "OR"} and len(children) < 2):
            raise ValueError(f"bad arity for {op}")
        return ASTNode(id=ids(), op=op, children=[build(c, depth + 1, seen | {node_id}) for c in children])

    return build(data["root"], 0, frozenset())


async def refine_with_llm(structured: StructuredPolicyRules) -> StructuredPolicyRules:
    """Re-parse the lowest-confidence rules with Gemini; keep grammar output on any failure."""
    from backend.llm.gemini_client import (
        GeminiCallError,
        GeminiNotConfigured,
        generate_json,
    )

    candidates = sorted(
        (r for r in structured.rules if r.parse_confidence < 0.6 and r.parse_method == "grammar"),
        key=lambda r: r.parse_confidence,
    )[:MAX_LLM_ASSISTS]
    if not candidates:
        return structured
    replaced: dict[str, PolicyRule] = {}
    notes = list(structured.notes)
    for rule in candidates:
        try:
            tree, _ = await generate_json(
                _LLM_PROMPT,
                f"Coverage effect: {rule.effect}\nSubject: {rule.subject}\nClause: {rule.source_text}",
                _LLM_SCHEMA,
                lambda data, r=rule: _validate_llm_tree(data, r.source_text, r.id),
            )
        except (GeminiCallError, GeminiNotConfigured) as exc:
            notes.append(f"LLM clause parsing for {rule.id} failed validation or call ({exc}); grammar parse kept.")
            continue
        replaced[rule.id] = rule.model_copy(update={
            "condition": tree, "parse_method": "llm_assisted", "parse_confidence": max(rule.parse_confidence, 0.7),
        })
    if not replaced:
        return structured.model_copy(update={"notes": notes})
    return structured.model_copy(update={
        "rules": [replaced.get(r.id, r) for r in structured.rules],
        "llm_assisted_rules": len(replaced),
        "notes": notes,
    })
