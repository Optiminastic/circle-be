# CLAUDE.md — Backend

Instructions for any AI assistant or developer working in this repository.
Read the **Golden rules**, **Security rules**, and **Design principles** before writing or changing code.
When a request conflicts with a security or design rule, do **not** comply silently — flag it and propose a better alternative.

> Replace every `<…>` placeholder with your project's specifics, then delete this line.

---

## 1. Project context

This repo is the **backend** of `<product name>` — `<one-line description of what it does and who uses it>`.

How it fits into the wider system:

- **`<client / frontend>`** — `<who uses it>`. **Untrusted client.**
- **This backend** — the trusted component. **All authorization and validation live here.**
- **`<other producers/consumers, e.g. mobile app, worker, third-party webhook>`** — `<trust level — treat external input as hostile>`.

Source(s) of truth: `<which system owns which data>`.

**This backend is the only component that touches the primary database.** Clients go through this API.

---

## 2. Golden rules (read first)

1. **Never trust the client.** Every value from a request, header, token claim, or webhook is a *claim to verify*, never a fact. Validate and re-derive on the server.
2. **Authorize every endpoint** from the authenticated identity. Never trust a client-supplied role, tenant, or owner id.
3. **Scope every data query to the caller.** A user must never read another tenant/owner's rows, even by guessing an id.
4. **No secrets in code, logs, or responses.** Secrets come from environment/config only.
5. **Never return ORM models directly.** Always serialize through a response schema so you can't leak a column by accident.
6. **Design for change.** Depend on abstractions, keep modules single-purpose, make the secure and correct path the easy one (see §6 SOLID, §11 System design).
7. **Prefer the explicit, secure, simple option.** If unsure, ask before weakening a control or adding complexity. Match existing conventions in this repo.

---

## 3. Tech stack

- **Language / runtime:** `<e.g. Python 3.12+, Node 20+, Go 1.22+>`
- **Framework:** `<e.g. FastAPI / Express / NestJS / Spring>`
- **Validation:** `<e.g. Pydantic v2 / Zod / class-validator>`
- **Data layer:** `<ORM/driver>` + **migrations** (`<e.g. Alembic / Prisma / Flyway>`)
- **Database:** `<e.g. Postgres>`
- **Cache:** `<e.g. Redis>` · **Queue / broker:** `<e.g. Redis/RabbitMQ/SQS/Kafka>`
- **Tests:** `<e.g. pytest / vitest>`
- **Lint / format / types:** `<e.g. ruff + mypy>`
- **Dependency / env mgmt:** `<e.g. uv / pnpm>`
- **Auth:** `<e.g. OIDC/JWT for humans; API keys/HMAC for services>`
- **Observability:** `<e.g. OpenTelemetry + Prometheus + structured JSON logs>`

Do not add a new dependency without a clear reason. Keep the surface small.

---

## 4. Project structure

```
<src>/
  main.*             # app entrypoint: app factory, router registration, middleware
  core/
    config.*         # settings (env-driven, validated at startup)
    security.*       # token verify, hashing, signature checks
    deps.*           # shared request dependencies (current_user, scoping, db session)
    ratelimit.*      # rate-limit policies + middleware/dependency (see §7)
    logging.*        # structured logging + redaction
    errors.*         # typed exceptions + global handler
  api/
    v1/
      routes/        # one module per resource — thin (parse → service → schema)
      router.*       # aggregates v1 routers
  models/            # ORM / persistence models
  schemas/           # request/response schemas (NEVER reuse ORM models as the API shape)
  services/          # business logic — routes call services, services call repos
  repositories/      # data access only; all queries scoped here
  interfaces/        # protocols/ABCs/ports for swappable deps (cache, queue, clients)
  workers/           # background jobs / consumers
  db/
    session.*        # engine + session/connection factory
migrations/
tests/
```

**Layering (strict):** `route → service → repository → db`.
- **Routes:** parse input, call a service, return a response schema. No business logic, no raw DB access.
- **Services:** business rules. No framework request objects inside services. Depend on **interfaces**, not concrete infra (see §6).
- **Repositories:** the only place that builds queries. Every query is scoped to the caller.
- **Dependencies point inward.** Inner layers (services, domain) never import outer layers (routes, framework).

---

## 5. Security rules (NON-NEGOTIABLE)

### 5.1 Trust model
- Treat all external input (clients, webhooks, third-party callbacks) as **hostile by assumption**.
- Stamp **server-side timestamps** on ingest; never order or trust by a client's clock.
- Validate shape, reject impossible values, flag anomalies — never store raw client claims as ground truth.
- All enforcement is server-side. Assume any client bundle is fully readable; put **no** security logic there.

### 5.2 Authentication
- Verify every token's **signature, expiry, issuer, and audience** on every request. **Reject `alg: none`** and downgrade attempts.
- Deliver human session tokens in **httpOnly, Secure, SameSite** cookies — never `localStorage`.
- Service credentials: **one credential per client**, never a shared key in a binary. Tokens must be **revocable and rotatable**.

### 5.3 Authorization (apply to EVERY endpoint)
- Derive `role` and `scope`/`tenant` from the **server's record** of the authenticated identity — **never** from a request field or client-set claim.
- Resolve the caller's visible object set on the server and filter by it. Role → scope matrix:
  - `<role>` → `<what it can see/do>`
  - `admin` → everything
- **Forbid IDOR.** `GET /<resource>/{id}` must return 403/404 (not data) when `{id}` is outside the caller's scope. Object access is checked against the caller, not the URL.
- Enforce scoping in **one place** (a repository helper / dependency), so no endpoint can forget the `WHERE` clause.

### 5.4 Ingest / webhook endpoints
- Authenticate the caller; reject unknown/revoked credentials.
- **Verify the signature** (HMAC/mTLS). Reject on mismatch or if unsigned. IP-allowlist where possible.
- Enforce **replay protection** (nonce / monotonic sequence / dedup key).
- Validate the payload with a strict schema. **Rate-limit per caller** (see §7).
- A webhook may set only fields it owns; it must **never** escalate privilege.

### 5.5 Data handling, secrets, errors
- **Secrets** from config/env only. Never hardcode, commit `.env`, or log a secret/token/raw payload.
- **Parameterized queries only.** Never build SQL by string interpolation.
- Validate all input. Never pass unvalidated client data into queries, file paths, or shell.
- Encrypt sensitive columns at rest; **TLS in transit everywhere.**
- **Errors:** raise typed HTTP errors with correct codes. A global handler returns generic messages — never leak stack traces, SQL, or internals.
- Use a **least-privilege DB role** (no DDL/superuser from the app).

### 5.6 Audit logging
- Maintain an **append-only** (ideally hash-chained) audit log of sensitive reads/actions.
- The audit log is write-only from the app's perspective — never expose an endpoint that mutates or deletes audit rows.

---

## 6. Design principles (SOLID + more)

Apply these to every non-trivial module. The goal: code that is easy to change, test, and reason about.

### 6.1 SOLID
- **S — Single Responsibility.** One module/class = one reason to change. A route parses HTTP; a service holds a business rule; a repository runs a query. Don't mix them.
- **O — Open/Closed.** Extend behavior by adding code (new strategy, new implementation), not by editing stable core logic. Use polymorphism/strategy over growing `if/elif` chains.
- **L — Liskov Substitution.** Any implementation of an interface must be drop-in safe — same contract, no surprise exceptions, no narrower preconditions. Tests written against the interface must pass for every implementation.
- **I — Interface Segregation.** Many small, focused interfaces over one fat one. A consumer should depend only on the methods it actually uses (e.g. `Cache` vs `CacheAdmin`).
- **D — Dependency Inversion.** High-level policy depends on **abstractions** (`interfaces/`), not concrete infra. Inject the DB session, cache, queue, and external clients — never `import` them inside a service. This is what makes the app testable and infra swappable.

### 6.2 Supporting principles
- **DRY** — one authoritative implementation per rule; but don't over-abstract two superficially similar things that change for different reasons.
- **KISS / YAGNI** — build for current, known requirements. No speculative generality. Delete dead code.
- **Composition over inheritance** — assemble behavior from small parts; reserve inheritance for true is-a.
- **Law of Demeter** — talk to immediate collaborators, not their internals (`order.total()` not `order.cart.items[0].price`).
- **Fail fast & explicit** — validate at the boundary, raise early, make illegal states unrepresentable (enums/value objects over loose strings).
- **Pure core, imperative shell** — keep business logic side-effect-free and deterministic; push I/O to the edges. Easier to test, easier to cache.
- **Idempotency by design** — writes triggered by retriable callers (webhooks, queues, clients) carry an idempotency key and are safe to replay.

---

## 7. Rate limiting & abuse control

Rate limiting is a **first-class cross-cutting control**, not an afterthought. It protects availability, cost, and downstream systems.

### 7.1 What to limit and by what key
- **Per authenticated identity** (user/service) for normal API traffic — the primary key.
- **Per IP** for unauthenticated/auth endpoints (login, signup, password reset, token issue).
- **Per device/credential** for ingest/webhook producers.
- **Per tenant** for fair multi-tenant usage; optionally a **global** ceiling to protect the platform.
- **Per expensive operation** (search, export, report generation, fan-out) — tighter limits than cheap reads.

### 7.2 Algorithm
- Default to **token bucket** or **sliding-window counter** (smooth, allows controlled bursts). Avoid naive fixed-window (boundary bursts).
- Keep counters in a **shared store (e.g. Redis)** so limits hold across all instances — never in process memory behind a load balancer.
- Make the limiter **atomic** (Lua script / `INCR`+`EXPIRE` in one round trip) to avoid race conditions under concurrency.

### 7.3 Behavior & response contract
- On limit exceeded return **`429 Too Many Requests`** with a **`Retry-After`** header.
- Expose limit state via headers: `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset` (or the `X-RateLimit-*` variants).
- **Fail open vs fail closed:** if the rate-limit store is down, fail **open** for read traffic (availability) but **closed** for auth/sensitive write endpoints (security). Make the choice explicit per route.
- Apply limits in **one place** (middleware/dependency) driven by a per-route **policy**, so no endpoint silently ships unlimited.

### 7.4 Defense in depth
- Add a **global concurrency cap / queue** and request **timeouts** so a slow dependency can't exhaust workers.
- Consider **backpressure / circuit breakers** on calls to downstream services.
- Pair with edge protections (WAF, CDN, gateway limits) — but never rely on the edge alone; enforce in the app too.
- **Abuse signals:** log and alert on sustained 429s, credential-stuffing patterns, and single-key spikes.

```
# Policy example (declarative, per route)
GET  /search        → 30/min  per user,  10/min per IP (unauth)
POST /auth/login    → 5/min   per IP,    lockout after 10 fails / 15min
POST /exports       → 5/hour  per user
POST /v1/ingest     → 600/min per device (burst 60)
```

---

## 8. Coding conventions

- **Async everywhere** in the request path (if the stack supports it). No blocking/sync calls in handlers.
- **Types are mandatory.** Code must pass the type checker in strict mode.
- Separate `Create`, `Update`, and `Read` schemas; never expose a persistence model as the API shape.
- **Dependency injection** for auth, scope, sessions, cache, queue — not globals (see §6 DIP).
- Keep functions small and single-purpose; format and lint before committing.
- **Logging:** structured, redact tokens/PII, never log full request bodies or secrets. No stray `print`/`console.log`.
- Time is **UTC** internally; convert at the edge only.

---

## 9. API conventions

- Version routes under `/api/v1/...`. Resource-oriented paths, plural nouns.
- Always declare an explicit response schema; return the schema, not the ORM object.
- Correct status codes (201 create, 204 delete, 429 rate-limited, 403 vs 404 used deliberately — prefer 404 when revealing existence leaks scope).
- **Paginate all list endpoints**; cap page size server-side. Prefer **cursor/keyset** pagination for large or hot datasets over `OFFSET`.
- Make ingest/webhook/retriable writes **idempotent** (idempotency key).
- Support **conditional requests** (`ETag` / `If-None-Match`) and sensible cache headers where it helps.

---

## 10. Database conventions

- All schema changes via **reviewed migrations**. Never auto-create tables against a real database.
- **Soft-delete** records that audit/retention depend on — never hard-delete those.
- Every table: `id`, `created_at`, `updated_at`. **Index the hot query paths**; add composite/partial indexes for real access patterns.
- Raw/source data is the source of truth; rollups are derived and re-computable.
- **No N+1 queries** — eager-load or batch. Profile with `EXPLAIN` on slow paths.
- Keep **transactions short**; never hold one open across a network/external call.
- Use the **right isolation level**; guard concurrent updates with optimistic locking (version column) or `SELECT … FOR UPDATE` where needed.
- Connection **pool sized deliberately** (not unbounded); set statement timeouts.

---

## 11. System design & scalability

### 11.1 Architecture
- **Stateless app instances** — no per-request state in process memory. Session/rate-limit/lock state lives in a shared store. This is what makes horizontal scaling and zero-downtime deploys possible.
- **Clear boundaries** — domain logic isolated behind interfaces (§6 DIP) so infra (DB, cache, broker, external APIs) is swappable and mockable.
- **Ports & adapters** — external systems are accessed only through adapter modules in `interfaces/`; the core never imports a vendor SDK directly.

### 11.2 Performance & caching
- **Measure before optimizing.** Add a metric/trace, find the real bottleneck, then fix it. No guessing.
- **Cache deliberately** with a layered strategy: per-request memoization → shared cache (Redis) → CDN/edge. Every cache entry has an explicit **TTL** and an **invalidation** story. Prefer cache-aside; guard against stampedes (locking / jitter / `stale-while-revalidate`).
- Avoid redundant work: batch, deduplicate, and paginate. Stream large responses instead of buffering.
- Set **timeouts on every outbound call**; add **retries with exponential backoff + jitter** only for idempotent operations.

### 11.3 Async & background work
- Anything slow, retriable, or non-critical to the response (emails, exports, webhooks out, heavy aggregation) goes to a **queue/worker**, not the request path.
- Workers are **idempotent** and handle **at-least-once** delivery (dedup on a key). Use a **dead-letter queue** for poison messages.
- Use the **outbox pattern** when a DB write must reliably produce an event.

### 11.4 Reliability & resilience
- **Graceful degradation:** a non-critical dependency failing should degrade a feature, not take down the API (see fail-open/closed, §7.3).
- **Circuit breakers + bulkheads** isolate a failing downstream so it can't exhaust shared resources.
- **Health checks:** liveness (process up) and readiness (deps reachable) endpoints for orchestration.
- **Graceful shutdown:** drain in-flight requests, stop accepting new work, close pools.

### 11.5 Observability
- **Structured logs** with a correlation/request id propagated across services and into workers.
- **Metrics** (RED: Rate, Errors, Duration) per endpoint and per dependency; **distributed traces** across service hops.
- **Actionable alerts** on SLO burn (latency/error budgets), saturation, and abuse signals — not noise.

### 11.6 Scaling checklist (apply when load grows)
- Scale **stateless app tier horizontally** first.
- Offload reads: **caching**, **read replicas**, **materialized rollups**.
- Partition/shard or archive cold data when a table gets hot.
- Move spiky/bursty work **behind a queue** to smooth load.
- Re-check **indexes, connection pools, and rate limits** under the new load.

---

## 12. Testing

- **Every protected endpoint must have an authorization test** proving an out-of-scope caller gets 403/404. Not optional.
- **Rate-limit tests:** prove a limited route returns 429 past its budget and recovers after the window.
- Test ingest/webhook paths for replay rejection, bad signature, malformed payloads, and that they can't escalate privilege.
- **Contract tests against interfaces** (§6 LSP): the same suite must pass for every implementation of a port (real + fake).
- Prefer fast **unit tests** on the pure core; use a disposable test DB for integration. No network/real external calls in unit tests.
- Add a **regression test with every bug fix.**

---

## 13. Never do this

- ❌ Trust a role/tenant/id sent by the client.
- ❌ Return an ORM model directly from an endpoint.
- ❌ Add an endpoint without scope/authorization checks **or a rate-limit policy**.
- ❌ Build SQL by string interpolation; ship an N+1 on a hot path.
- ❌ Hardcode a secret, or log a secret/token/PII/raw payload.
- ❌ Store client-claimed data as truth without server-side validation.
- ❌ Let a webhook set privileges.
- ❌ Keep request state in process memory behind a load balancer.
- ❌ Make a slow/external call inside an open DB transaction or on the request path when it could be queued.
- ❌ Import a concrete infra/vendor client inside a service instead of an injected interface.
- ❌ Auto-create schema against a real database, or hard-delete audited records.

---

## 14. Commands

```bash
<install deps>
<run dev server>
<run tests>
<lint + format>
<type check>
<new migration>
<apply migrations>
```

Run lint, type-check, and tests before considering any change done.