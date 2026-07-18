# Onboarded Candidate Handoff — Feed API

When a hired candidate arrives for their **first office day**, HR clicks **"Mark arrived"**
on the onboarding screen. That adds them to a single, token-gated **pull feed** that an
**external onboarding / HRMS system** fetches to import the candidate's profile plus the
documents HR verified.

> It's a **pull feed**, not a push webhook: Curcle exposes **one stable URL**, and the
> external system fetches it on its own schedule. There are no per-candidate callbacks to
> configure. (Internally the "Mark arrived" action is sometimes called the handoff webhook.)

Implementation: `app/api/routes/candidate_handoff.py`.

---

## Flow

```
HR clicks "Mark arrived"                     External onboarding system
        │                                              │
        ▼                                              ▼
POST /api/candidate-handoffs/{id}/send        GET /api/candidate-feed/{TOKEN}
   (HR dashboard session)                        (shared secret token)
        │                                              │
        ▼                                              ▼
 row in `candidate_handoffs`  ───────────────►  every arrived candidate
   (arrivedAt stamped once)                     + HR-verified documents
```

1. **HR marks arrived** → `POST /api/candidate-handoffs/{candidateId}/send` (requires an HR
   session). Records/updates a row in `candidate_handoffs`; `arrivedAt` is set once on the
   first mark and never overwritten.
2. **External system pulls** → `GET /api/candidate-feed/{TOKEN}` (public, token-gated).
   Returns **all** arrived candidates with curated fields + verified documents.

---

## The feed endpoint

```
GET https://api.circle.optiminastic.com/api/candidate-feed/<TOKEN>
```

- **Public** (no login) — authenticated solely by the secret `<TOKEN>` in the URL path.
- Returns `application/json`.
- Local dev: `http://localhost:8000/api/candidate-feed/<TOKEN>`.

### Response

```json
{
  "count": 2,
  "generatedAt": "2026-07-03T09:00:00+00:00",
  "candidates": [
    {
      "candidateId": "CAN-8279",
      "name": "Rishi Patel",
      "email": "rishi@example.com",
      "phone": "+91…",
      "previousCompany": "Acme Corp",
      "currentTitle": "Senior Designer",
      "roleHired": "Lead Designer",
      "department": "Design",
      "candidateSource": "candidate",
      "arrivedAt": "2026-07-03T08:40:00+00:00",
      "documents": [
        {
          "docType": "Aadhaar",
          "fileName": "aadhaar.pdf",
          "contentType": "application/pdf",
          "size": 184320,
          "verifiedAt": "2026-07-01T11:20:00+00:00",
          "downloadUrl": "https://…s3…X-Amz-Expires=900…"
        }
      ]
    }
  ]
}
```

Field notes:
- **Curated profile only** — name, id, email, phone, previous company, current title, role
  hired, department. No salary, notes, or the full candidate record is exposed.
- `candidateSource` — where the profile was resolved from (`candidate` / `onboarding` /
  `employee` / `doc-request`); a hired candidate may already be converted to an employee.
- `documents` — **only the documents HR approved** during verification (deduped per type,
  latest wins). Each `downloadUrl` is a **short-lived presigned S3 URL (~15 minutes)**.

---

## Configuration

Set the shared secret in circle-be's `.env`:

```
CANDIDATE_FEED_TOKEN=<a long random secret>
```

- Generate one: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
- **Empty/unset ⇒ the feed is disabled** — every request returns `404`.
- Restart the backend after changing it (`docker compose up -d` on the VPS).
- The URL you hand to the external team is:
  `https://api.circle.optiminastic.com/api/candidate-feed/<CANDIDATE_FEED_TOKEN>`

---

## How the external system should consume it

- **Poll** the feed on a schedule (e.g. every 5–15 min) or on demand. There's no push.
- **Download documents immediately.** `downloadUrl`s are presigned and **expire in ~15
  minutes** — fetch and store the file on receipt; do not persist the URL.
- The feed is **cumulative** — it returns every candidate ever marked arrived (newest
  `arrivedAt` first). Deduplicate by `candidateId` on your side and track what you've
  already imported (e.g. via `arrivedAt`).
- Documents that HR hasn't verified never appear, so a candidate may show with an empty
  `documents` array until verification completes.

Example fetch:
```bash
curl -s "https://api.circle.optiminastic.com/api/candidate-feed/$CANDIDATE_FEED_TOKEN" | jq .
```

---

## Security — how secure is it?

**The token in the URL is the entire security boundary.** Anyone who has the full URL can
read every arrived candidate's PII and verified documents. Treat the URL itself as a secret.

### What protects it

| Control | Detail |
|---|---|
| **Token-gated** | Requires the exact `CANDIDATE_FEED_TOKEN`. |
| **Constant-time compare** | `secrets.compare_digest` — no timing side-channel to guess the token. |
| **404, not 401** | Wrong/missing/disabled token returns `404 Not found` — never reveals the endpoint exists. |
| **HTTPS only** | Served via Caddy with TLS; token + data encrypted in transit. |
| **Private bucket** | Documents are **short-lived (~15 min) presigned S3 URLs**, not public objects. |
| **Least data** | Only the agreed curated fields + **HR-verified** documents — no salary, notes, or raw record. |
| **Per-IP rate limited** | The feed path is throttled per IP (`app/main.py`), limiting scraping/brute force. |
| **No secret in logs** | The token is never logged. |

### Honest limitations

- **Bearer token in the URL.** It's a shared secret with no per-consumer identity. URLs can
  leak via proxy logs, browser history, or referrer headers — so **never** put this URL in a
  browser, client-side code, email body, or ticket. Share it over a secure channel
  (password manager / secret store) and call it **server-to-server only**.
- **One shared token** ⇒ revocation = rotation (below). There's no per-partner key or
  granular revoke.
- **15-min document window.** If a feed response is captured, its `downloadUrl`s remain
  usable until they expire (then `403`). Keep the TTL short.
- The feed exposes candidate **PII** to whoever holds the token — protect it like a password.

### Optional hardening (not implemented; needs a change)

- Move the token to a request **header** instead of the URL path (avoids URL logging).
- **IP-allowlist** the external system at Caddy so only their egress IP can reach the feed.
- Rotate the token on a schedule; shorten the presigned TTL if required.

### Rotating the token

1. Generate a new value: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Update `CANDIDATE_FEED_TOKEN` in `.env` and restart the backend.
3. Give the new URL to the external team. **The old URL immediately returns `404`.**
