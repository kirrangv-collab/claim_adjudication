# Research data policy

This project does not contain the original frozen 24-case adjudication benchmark. It was not in the supplied workspace and must not be reconstructed from the mentor report's summary metrics.

## Public real-world data source identified

- **Dataset:** CMS Medicare Physician & Other Practitioners by Provider and Service (2024 resource; catalog last updated 2026-05-21).
- **Publisher:** Centers for Medicare & Medicaid Services (CMS), U.S. Department of Health and Human Services.
- **Official catalog:** <https://catalog.data.gov/dataset/medicare-physician-other-practitioners-by-provider-and-service>
- **Official CMS dataset page:** <https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners/medicare-physician-other-practitioners-by-provider-and-service>
- **2024 catalog resource API URL:** <https://data.cms.gov/data-api/v1/dataset/92396110-2aed-4d63-a6a2-5d6207d46a29/data>
- **Data type:** annual provider/service aggregate utilization, charges, and payments, organized by provider NPI, HCPCS code, and place of service; it is not case-level insurance adjudication data and has no approve/deny ground-truth labels.
- **Use here:** contextual analysis or prototype UI demonstration only. It cannot validly train/evaluate coverage adjudication or support claims about adjudication accuracy.
- **Conditions:** review and comply with the source's current CMS Public Use File terms and documentation; cite CMS; do not attempt to re-identify anyone.

## Download status

The catalog identifies the 2024 resource and its official API URL, but no source data is checked into this folder. The official dataset page and API resource returned an Akamai HTTP 403 access-denied response from this environment, so the dataset could not be retrieved or verified here. The exact source and failure are recorded in `manifest.json`. Do not substitute an unofficial mirror or fabricate a dataset. Once the official CMS download/API link is available from an allowed network, save the unmodified source as `data/raw/` and record its exact URL, publication year, retrieval timestamp, file size, SHA-256, accompanying data dictionary, and applicable terms in the manifest before any processing.

Never put patient-level claims, PHI, credentials, or data requiring a restricted CMS data-use agreement in this public demo workspace.
