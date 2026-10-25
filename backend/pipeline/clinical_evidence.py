"""Agent 2: Clinical Evidence Agent.

1. Isolates the *active encounter* from noisy clinical text: section headers
   (PMH, prior/previous encounters, family history, copied-forward notes,
   templates/disclaimers), dated encounters (latest = active), boilerplate
   sentences and copy-forward duplicates are separated out with a reason.
2. Runs MCR sliding-window concept recognition with embedding-based ICD-10-CM
   normalisation (SBioBERT when configured, hashing fallback otherwise).
3. Assigns NegEx-style assertion status to every mention and extracts
   quantities (e.g. lesion size in disk areas).
"""
from __future__ import annotations

import re

from backend.knowledge import KnowledgeBase
from backend.pipeline.contracts import (
    ClinicalExtraction,
    ConceptMention,
    EncounterSegment,
    QuantityMention,
)
from backend.pipeline.nlp import (
    AssertionClassifier,
    clean_text,
    extract_quantities,
    normalize_phrase,
    split_sentences,
)

_HEADER_RE = re.compile(
    r"(?i)(?:^|(?<=[\n.;]))\s*(?P<h>hpi|history of present illness|chief complaint|assessment and plan|"
    r"assessment|a/p|plan|impression|exam(?:ination)?|findings|current encounter|today'?s visit|"
    r"pmh|past medical history|past ocular history|past history|prior (?:encounter|visit|note)s?[^:\n]{0,30}|"
    r"previous (?:encounter|visit|note)s?[^:\n]{0,30}|old records?|family history|fhx|social history|"
    r"medications|allergies|copied forward[^:\n]{0,40}|copy[- ]forward[^:\n]{0,40}|template|disclaimer|"
    r"addendum|encounter[^:\n]{0,30}|visit[^:\n]{0,30})\s*:"
)
_HISTORICAL_HEADERS = ("pmh", "past", "prior", "previous", "old record", "copied forward", "copy forward",
                       "copy-forward", "medications", "allergies", "social history")
_FAMILY_HEADERS = ("family history", "fhx")
_BOILERPLATE_HEADERS = ("template", "disclaimer")
_BOILERPLATE_SENTENCE = re.compile(
    r"(?i)\b(?:electronically signed|dictated but not read|this note (?:was|is) (?:generated|auto)|"
    r"all other systems (?:reviewed|negative)|copied from (?:the )?prior note|template text|"
    r"for billing purposes only)\b"
)
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_PREFIX_RE = re.compile(r"^\s*synthetic vignette:\s*", re.IGNORECASE)


class ClinicalEvidenceAgent:
    name = "clinical_evidence"

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def segment(self, evidence: str) -> tuple[list[EncounterSegment], list[EncounterSegment]]:
        text = _PREFIX_RE.sub("", evidence.replace("\r", ""))
        headers = list(_HEADER_RE.finditer(text))
        raw: list[tuple[str, str]] = []
        if not headers or headers[0].start() > 0:
            raw.append(("Note", text[: headers[0].start()] if headers else text))
        for i, header in enumerate(headers):
            end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
            raw.append((header.group("h").strip(), text[header.end():end]))

        dated = [(label, _DATE_RE.search(label)) for label, _ in raw]
        dates = sorted({m.group(1) for _, m in dated if m})
        latest = dates[-1] if dates else None

        active: list[EncounterSegment] = []
        excluded: list[EncounterSegment] = []
        seen: set[str] = set()
        for label, body in raw:
            body = clean_text(body)
            if not body:
                continue
            low = label.casefold()
            date = _DATE_RE.search(label)
            if low.startswith(_FAMILY_HEADERS):
                kind, reason = "family", "Family-history section; not the patient's active condition."
            elif low.startswith(_BOILERPLATE_HEADERS):
                kind, reason = "boilerplate", "Template/disclaimer section."
            elif low.startswith(_HISTORICAL_HEADERS) or any(h in low for h in ("copied forward", "copy forward", "copy-forward")):
                kind, reason = "historical", "Historical or carried-forward section, not the active encounter."
            elif date and latest and date.group(1) != latest:
                kind, reason = "historical", f"Earlier dated encounter ({date.group(1)}); active encounter is {latest}."
            else:
                kind, reason = "active", "Active encounter content."
            for sentence in split_sentences(body):
                norm = normalize_phrase(sentence)
                if _BOILERPLATE_SENTENCE.search(sentence):
                    excluded.append(EncounterSegment(text=sentence, label=label, segment_type="boilerplate",
                                                     reason="Boilerplate sentence."))
                    continue
                if norm in seen:
                    excluded.append(EncounterSegment(text=sentence, label=label, segment_type="duplicate",
                                                     reason="Duplicate of earlier text (copy-forward)."))
                    continue
                seen.add(norm)
                segment = EncounterSegment(text=sentence, label=label, segment_type=kind, reason=reason)
                (active if kind == "active" else excluded).append(segment)
        return active, excluded

    def _mentions(self, text: str, source: str, override: str | None = None) -> tuple[list[ConceptMention], int]:
        found, scanned = self.kb.normalizer.find(text)
        classifier = AssertionClassifier(text)
        mentions = []
        for raw in found:
            concept = self.kb.ontology.get(raw.concept_id)
            assertion = classifier.classify(raw.start, raw.end)
            if override:
                # Statements in historical/family sections (affirmed or negated)
                # describe another time or person, not the active encounter.
                assertion = override
            mentions.append(ConceptMention(
                text=raw.text, concept_id=concept.id, label=concept.label, system=concept.system,
                code=concept.code if concept.system == "ICD10CM" else None, score=raw.score,
                match=raw.match, assertion=assertion, source=source, sentence=text,
            ))
        return mentions, scanned

    def run(self, diagnosis: str, requested_service: str, clinical_evidence: str) -> ClinicalExtraction:
        active, excluded = self.segment(clinical_evidence)
        mentions: list[ConceptMention] = []
        quantities: list[QuantityMention] = []
        scanned_total = 0
        for text, source in ((diagnosis, "diagnosis"), (requested_service, "service")):
            found, scanned = self._mentions(text, source)
            mentions.extend(found)
            scanned_total += scanned
        for segment in active:
            found, scanned = self._mentions(segment.text, "evidence")
            mentions.extend(found)
            scanned_total += scanned
            classifier = AssertionClassifier(segment.text)
            for quantity in extract_quantities(segment.text):
                quantities.append(QuantityMention(
                    value=quantity.value, unit=quantity.unit, text=quantity.text,
                    assertion=classifier.classify(quantity.start, quantity.end), sentence=segment.text,
                ))
        for segment in excluded:
            if segment.segment_type in {"historical", "family"}:
                found, scanned = self._mentions(segment.text, "evidence", override=segment.segment_type)
                mentions.extend(found)
                scanned_total += scanned
        codes = sorted({
            m.code for m in mentions
            if m.code and m.assertion == "present" and m.source in {"diagnosis", "evidence"}
        })
        return ClinicalExtraction(
            active_segments=active,
            excluded_segments=excluded,
            mentions=mentions,
            quantities=quantities,
            icd_codes=codes,
            embedder=self.kb.embedder.name,
            mcr_windows_scanned=scanned_total,
        )
