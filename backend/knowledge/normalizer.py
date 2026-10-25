"""MCR sliding-window concept recognition with embedding-based normalisation.

Every token window of 1..``MAX_WINDOW`` tokens is a candidate mention. A
window is normalised to an ontology concept by (1) exact match on the
normalised synonym index, (2) for a single unknown word, a strict
typo-tolerant spelling match against single-word synonyms, else (3)
nearest-neighbour search of the window embedding against the embedded
ontology terms in the vector store (SBioBERT or the hashing fallback) above
the embedder's match threshold. Overlapping candidates are resolved by
non-maximum suppression (exact > longer > higher score), mirroring
multi-candidate retrieval + selection.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from difflib import SequenceMatcher

from backend.pipeline.nlp import STOP_WORDS, tokenize

MAX_WINDOW = 6
MAX_EMBED_WINDOW = 4
TERMS_COLLECTION = "ontology_terms"
FUZZY_MIN_LENGTH = 6
FUZZY_THRESHOLD = 0.88


@dataclass(frozen=True)
class RawMention:
    start: int
    end: int
    text: str
    concept_id: str
    score: float
    match: str


class ConceptNormalizer:
    def __init__(self, ontology, embedder, vectors, terms_collection: str, cache_size: int = 2048):
        self.ontology = ontology
        self.embedder = embedder
        self.vectors = vectors
        self.terms_collection = terms_collection
        self._cache: OrderedDict[tuple[str, bool], tuple[list[RawMention], int]] = OrderedDict()
        self._cache_size = cache_size
        self._lock = threading.Lock()
        # Single-word synonyms bucketed by first letter, for typo-tolerant matching.
        self._single_words: dict[str, list[tuple[str, str]]] = {}
        for norm, concept_id in ontology.exact_index.items():
            if " " not in norm and len(norm) >= FUZZY_MIN_LENGTH - 2:
                self._single_words.setdefault(norm[0], []).append((norm, concept_id))
        self._fuzzy_cache: dict[str, tuple[str, float] | None] = {}

    def _fuzzy(self, word: str) -> tuple[str, float] | None:
        """Spelling-variant match ("glaucomma" -> glaucoma) for an unknown word.

        Deliberately strict: same first letter, length within 2, similarity
        >= FUZZY_THRESHOLD. Semantic variants are left to the embedder.
        """
        if len(word) < FUZZY_MIN_LENGTH or word in STOP_WORDS or word in self.ontology.term_vocabulary:
            return None
        if word in self._fuzzy_cache:
            return self._fuzzy_cache[word]
        best: tuple[str, float] | None = None
        for candidate, concept_id in self._single_words.get(word[0], ()):
            if abs(len(candidate) - len(word)) > 2:
                continue
            matcher = SequenceMatcher(None, word, candidate)
            if matcher.quick_ratio() < FUZZY_THRESHOLD:
                continue
            ratio = matcher.ratio()
            if ratio >= FUZZY_THRESHOLD and (best is None or ratio > best[1]):
                best = (concept_id, round(ratio, 4))
        if len(self._fuzzy_cache) < 50_000:
            self._fuzzy_cache[word] = best
        return best

    def find(self, text: str, use_embeddings: bool = True) -> tuple[list[RawMention], int]:
        key = (text, use_embeddings)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        result = self._find(text, use_embeddings)
        with self._lock:
            self._cache[key] = result
            if len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return result

    def _find(self, text: str, use_embeddings: bool) -> tuple[list[RawMention], int]:
        tokens = tokenize(text)
        candidates: list[tuple[int, int, RawMention]] = []
        embed_windows: list[tuple[int, int]] = []
        exact_covered: set[int] = set()
        scanned = 0
        vocabulary = self.ontology.term_vocabulary
        for size in range(min(MAX_WINDOW, len(tokens)), 0, -1):
            for i in range(len(tokens) - size + 1):
                window = tokens[i:i + size]
                if size > 1 and (window[0].norm in STOP_WORDS or window[-1].norm in STOP_WORDS):
                    continue
                scanned += 1
                norm = " ".join(t.norm for t in window)
                concept_id = self.ontology.exact_index.get(norm)
                start, end = window[0].start, window[-1].end
                if concept_id:
                    candidates.append((i, i + size, RawMention(start, end, text[start:end], concept_id, 1.0, "exact")))
                    exact_covered.update(range(i, i + size))
                elif use_embeddings and size == 1 and (fuzzy := self._fuzzy(window[0].norm)):
                    candidates.append((i, i + 1, RawMention(start, end, text[start:end], fuzzy[0], fuzzy[1], "fuzzy")))
                elif (
                    use_embeddings
                    and size <= MAX_EMBED_WINDOW
                    and len(norm) >= 5
                    and not exact_covered.issuperset(range(i, i + size))
                    and any(t.norm in vocabulary and t.norm not in STOP_WORDS for t in window)
                ):
                    embed_windows.append((i, i + size))
        if embed_windows:
            phrases = [text[tokens[a].start:tokens[b - 1].end] for a, b in embed_windows]
            hits = self.vectors.search_batch(
                self.terms_collection, self.embedder.encode(phrases), top_k=1,
                min_score=self.embedder.match_threshold,
            )
            for (a, b), phrase, row in zip(embed_windows, phrases, hits):
                if row:
                    hit = row[0]
                    candidates.append((a, b, RawMention(
                        tokens[a].start, tokens[b - 1].end, phrase, hit.payload["concept_id"],
                        round(hit.score, 4), "embedding",
                    )))
        candidates.sort(key=lambda c: (c[2].match != "exact", -(c[1] - c[0]), -c[2].score))
        taken: set[int] = set()
        selected = []
        for a, b, mention in candidates:
            if taken.isdisjoint(range(a, b)):
                taken.update(range(a, b))
                selected.append(mention)
        selected.sort(key=lambda m: m.start)
        return selected, scanned
