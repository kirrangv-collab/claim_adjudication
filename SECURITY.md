# Security policy

ClaimLab is a **research prototype**. It is not certified for clinical or
payment use and must never be given real patient data (PHI/PII). Public demo
sites run on synthetic data only.

## Supported versions

Only the latest commit on the default branch and the most recent release tag
receive security fixes.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

1. Go to the repository's **Security** tab → **Report a vulnerability**
   (GitHub private vulnerability reporting).
2. Describe the issue, affected component (API, web UI, deploy kit), steps to
   reproduce and the impact you expect.

You should receive an acknowledgement within 7 days. Once a fix is available we
will publish a GitHub security advisory and credit you unless you prefer to
stay anonymous.

## Scope

In scope:

- `backend/` API (authentication, authorisation, maker–checker review rules,
  rate limiting, input validation)
- `frontend/` web UI
- `deploy/` kit (Docker Compose, Caddy, scripts) and the published container
  images

Out of scope:

- Findings that require a compromised server or stolen `deploy/.env` secrets
- The intentionally public demo credentials (`demo-maker` / `demo-checker`)
  shown on demo sites; they only grant the reviewer role on synthetic data
- Volumetric denial of service against a hosted demo

## Hardening notes for operators

- Generate secrets with `bash deploy/configure.sh`; never reuse the examples.
- Keep `CAF_MODEL_API_KEY` empty on public sites (it would let visitors spend
  your LLM quota).
- Create admin accounts with strong passwords; demo accounts are reviewers only.
- Keep the host patched (`unattended-upgrades`) and run `bash deploy/update.sh`
  regularly.
