# Security

## Threat model

The Printed Parser is a **single-user, local-first tool**. The dashboard and API
are intended to run on `127.0.0.1` for the person operating them; they are not a
multi-tenant service. The primary sources of untrusted input are:

1. **Scraped content** — titles, descriptions, URLs and images come from third-
   party sites and must be treated as attacker-controlled.
2. **Cross-site requests** — while the local server is running, any website the
   user visits could try to reach `http://127.0.0.1:8000`.

## Mitigations in place

| Risk | Mitigation |
| --- | --- |
| **CSV / formula injection** via scraped titles | Export values starting with `= + - @` (or tab/CR) are prefixed with `'` so spreadsheets treat them as text (`services._csv_safe`). |
| **CSRF / DNS-rebinding** on state-changing endpoints | Requests are rejected unless the `Host` header is `localhost`/`127.0.0.1`/`::1`, and mutating `POST`s require `Content-Type: application/json` (blocks drive-by `<form>` posts). |
| **XSS** from scraped strings | All rendered values are HTML-escaped; URLs used in `href`/`src` are restricted to `http(s)`/relative schemes (blocks `javascript:`). |
| **Browser sandbox escape** from malicious pages | Chromium runs **with its sandbox enabled** by default; it is only disabled when `TPP_NO_SANDBOX=1` is set explicitly (e.g. as root in a container). |
| **Network exposure** | The dashboard binds to `127.0.0.1`; the Docker Compose port is published to loopback only. |
| **SQL injection** | All database access uses SQLAlchemy's parameterized ORM — no string-built SQL. |
| **Unsafe deserialization** | Only `json` is parsed; no `pickle`/`yaml`. |
| **Information disclosure** | Internal exceptions are logged server-side; clients receive generic error messages. |
| **Supply chain** | Runtime and dev dependencies are pinned to exact versions. |
| **Request memory DoS** | Request bodies are capped (1 MB). |

## Known limitations (by design)

- **No authentication.** This is a personal, localhost tool; access control is
  provided by binding to loopback plus the Host/CSRF checks above. Do not expose
  it to untrusted networks.
- **The Docker image runs as root** and therefore sets `TPP_NO_SANDBOX=1`. If you
  harden the image to run as a non-root user, remove that variable to restore the
  Chromium sandbox.

## Reporting

Found something? Please open a private security advisory or an issue describing
the impact and reproduction steps.
