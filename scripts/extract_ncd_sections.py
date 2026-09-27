"""Extract structured policy sections from the downloaded CMS National Coverage
Determinations (NCD) Manual PDF (Pub. 100-03, Chapter 1, Part 1).

This is real, official, publicly published U.S. government policy text (no
license/paywall beyond the standard AMA CPT-code notice already accepted by
downloading the manual itself). It contains no patient data of any kind.

Run from the project root:
    python scripts/extract_ncd_sections.py
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
RAW_PDF = ROOT / "data" / "raw" / "ncd103c1_Part1.pdf"
OUT_PATH = ROOT / "data" / "processed" / "ncd_eye_policy_sections.json"
SOURCE_URL = (
    "https://www.cms.gov/Regulations-and-Guidance/Guidance/Manuals/"
    "downloads/ncd103c1_Part1.pdf"
)
# Pages containing the "80 - Eye" series in this specific PDF revision.
SECTION_PAGE_RANGE = (95, 129)
SECTION_HEADING = re.compile(r"^(80(?:\.\d+)?) - (.+?)\s*$", re.MULTILINE)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_sections(text: str) -> list[dict]:
    matches = list(SECTION_HEADING.finditer(text))
    sections = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        coverage_status = (
            "not_covered" if re.search(r"\bNot Covered\b", body, re.IGNORECASE)
            else "covered" if re.search(r"\bcovered\b", body, re.IGNORECASE)
            else "conditional"
        )
        sections.append({
            "ncd_section": match.group(1),
            "title": match.group(2).strip(),
            "coverage_status_hint": coverage_status,
            "policy_text": " ".join(body.split()),
        })
    return sections


def main() -> None:
    if not RAW_PDF.exists():
        raise SystemExit(
            f"Missing source PDF: {RAW_PDF}. Download it first from {SOURCE_URL}."
        )
    reader = PdfReader(str(RAW_PDF))
    start, end = SECTION_PAGE_RANGE
    text = "\n".join(
        (reader.pages[i].extract_text() or "") for i in range(start, end)
    )
    sections = extract_sections(text)

    payload = {
        "provenance": {
            "source_title": (
                "Medicare National Coverage Determinations (NCD) Manual, "
                "Chapter 1, Part 1 (Sections 10 - 80.12), Section 80 - Eye"
            ),
            "publisher": "Centers for Medicare & Medicaid Services (CMS)",
            "source_url": SOURCE_URL,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source_file": str(RAW_PDF.relative_to(ROOT)),
            "source_sha256": sha256_of(RAW_PDF),
            "license_notice": (
                "CPT codes/descriptions referenced within CMS manuals are "
                "copyright American Medical Association; used here only as "
                "unmodified excerpts of the publicly issued CMS policy text "
                "for research purposes, per the standard CMS/AMA notice "
                "displayed on cms.gov. No patient data is present."
            ),
            "extraction_method": (
                "pypdf text extraction of the '80 - Eye' section range, "
                "split on numbered subsection headings via regex."
            ),
            "coverage_status_hint_caveat": (
                "coverage_status_hint is a simple keyword heuristic over the "
                "official text, not an official CMS determination summary. "
                "Always defer to the full policy_text."
            ),
        },
        "sections": sections,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {len(sections)} sections to {OUT_PATH}")


if __name__ == "__main__":
    main()
