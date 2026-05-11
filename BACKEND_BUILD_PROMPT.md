# WellMed Backend — Build Prompt

Hand this entire file to a fresh Claude Code session (or backend engineer). It is self-contained and supersedes the older `BACKEND_API_CONTRACT.md` / `BOOKING_ARCHITECTURE.md` / `DATABASE_SCHEMA.md` docs for purposes of building the backend.

---

## 1. Project context

WellMed is a holistic medical practice in Umhlanga, South Africa (Dr Moodley). The static front-end already exists as a vanilla-JS site hosted on S3 + CloudFront. You are building **only the backend API** that this front-end calls.

The front-end pages that call the backend:
- `pages/book-appointment.html` (booking flow, driven by `js/booking.js`)
- `pages/contact.html` (contact form)
- `pages/admin/dashboard.html` (admin dashboard for Dr Moodley)

The front-end reads the base URL from `WM.api.baseUrl` in `js/config.js` and falls back to synthesised data when the API is unreachable, so deploying a partial backend never breaks the UI.

---

## 2. Tech stack (hard constraints)

| Concern              | Choice                                  |
| -------------------- | --------------------------------------- |
| IaC                  | **AWS SAM** (single `template.yaml`)     |
| Compute              | **AWS Lambda**, Python 3.12              |
| API                  | **API Gateway HTTP API (v2)**            |
| Database             | **Amazon DynamoDB** (no SQL / RDS)       |
| Email                | **Amazon SES** (transactional)           |
| Scheduled jobs       | **EventBridge Scheduler** (reminders only) |
| Secrets / creds      | **Hardcoded** — Lambda env vars + bundled service-account JSON file (no Secrets Manager) |
| Google Calendar auth | **Service account** (no patient OAuth)   |
| Region               | `eu-west-1` (Ireland)                    |

### Out of scope (do not implement)
- Encryption at rest beyond DynamoDB defaults
- Field-level encryption / KMS envelope encryption
- POPIA hardening, audit-log retention policy, full PII review
- Rate limiting / reCAPTCHA / honeypot
- Payment, EHR sync, marketing automation
- Webhooks
- Patient self-service portal beyond the booking flow

### Auth posture (light)
- Patient-facing endpoints (`/api/availability`, `/api/bookings`, `/api/contact`) are **unauthenticated**.
- Admin endpoints (`/api/admin/*`) require an `Authorization: Bearer <jwt>` header. Use a simple JWT (HS256, secret read from the `JWT_SECRET` env var) issued by a `/api/admin/login` endpoint. No MFA, no refresh tokens for v1.
- CORS: allow the production CloudFront origin only. Read it from a stack parameter.

---

## 3. Common conventions

- HTTPS only. JSON request/response. ISO-8601 UTC timestamps everywhere.
- All times **displayed** to users are `Africa/Johannesburg` (UTC+2). Backend stores UTC, returns UTC, front-end converts.
- Validation errors → `400 { "error": "validation", "fields": ["personal.email", ...] }`.
- Slot conflict → `409 { "error": "slot_unavailable" }`.
- Idempotency: `POST /api/bookings` accepts an optional `Idempotency-Key` header; persist the key + response in DynamoDB with 24h TTL so double-clicks never double-book.

---

## 4. API endpoints

### 4.1 `GET /api/availability`
**Query:** `service` (required, one of `gp-practice`, `iv-therapy`, `ozone-therapy`, `red-light-therapy`, `weight-loss`, `yoga-breathwork`), `date` (`YYYY-MM-DD`, required).

**200:**
```json
[
  { "start": "2026-05-12T07:00:00Z", "label": "09:00", "available": true },
  { "start": "2026-05-12T07:30:00Z", "label": "09:30", "available": false }
]
```

Compute slots from `ServiceConfig.businessHours[weekday]` × `durationMinutes` + `bufferMinutes`, then mark each slot unavailable if any `Bookings` row exists with the same `service` and `slotStart` and `status IN (pending, confirmed)`.

### 4.2 `POST /api/bookings`
Submit a new booking + intake. Atomic transaction.

**Request:**
```json
{
  "service": "iv-therapy",
  "requestedSlot": "2026-05-12T07:30:00Z",
  "personal": {
    "firstName": "Nadia",
    "lastName":  "Pillay",
    "idOrPassport": "8501010000080",
    "phone": "+27821234567",
    "email": "nadia@example.co.za",
    "emergencyContact": { "name": "John Pillay", "phone": "+27827654321" },
    "medicalAid": {
      "provider": "Discovery",
      "memberNumber": "1234567890",
      "mainMember": "Nadia Pillay",
      "dependentCode": "00"
    }
  },
  "medical": {
    "existingConditions": "Hypothyroidism",
    "allergies": "Penicillin",
    "currentMeds": "Eltroxin 100mcg daily",
    "reasonForVisit": "Energy IV before a busy work week",
    "notes": "",
    "marketingOptIn": true
  },
  "consent": true,
  "submittedAt": "2026-05-11T12:00:00Z"
}
```

**Server-side validation (mandatory):**
- `service` in allowed slugs
- `requestedSlot` is a future ISO timestamp on a working day per `ServiceConfig`
- `firstName`, `lastName`, `idOrPassport`, `phone`, `email`, `emergencyContact.name`, `emergencyContact.phone` non-empty
- `email` is a valid address
- `consent === true`

**Flow:**
1. Upsert the patient by `email` via `Query GSI1` with `GSI1PK = EMAIL#<email>`; create a new `PATIENT#<uuid>` item if no hit.
2. Allocate `shortId = "WM-" + (1000 + counter)` via `UpdateItem ADD` on the `COUNTER#bookingShortId` item.
3. `TransactWriteItems`: write the `SLOTLOCK#<service>#<slotStartISO>` item with `ConditionExpression: attribute_not_exists(PK)` **and** the `BOOKING#<bookingId>` item with `status = "pending"`. If the condition fails, return `409 { "error": "slot_unavailable" }`.
4. Call Google Calendar `events.insert` (see §5).
5. On success, `UpdateItem` the booking: `status = "confirmed"`, `GSI2PK = "STATUS#confirmed"`, `googleEventId = <id>`, `confirmationSentAt = now`.
6. Send `booking_confirmation` email via SES (fail-soft; do not fail the whole request if SES hiccups — log and continue).
7. Return:
```json
{
  "id": "WM-1042",
  "status": "confirmed",
  "calendarEventId": "abc123def456",
  "patientCalendarInviteSent": true,
  "confirmationEmailSent": true
}
```

### 4.3 `POST /api/contact`
```json
{ "name": "...", "email": "...", "phone": "...", "topic": "...", "message": "...", "ts": "..." }
```
**200:** `{ "ok": true }`. Persist to `ContactMessages`.

### 4.4 `POST /api/admin/login`
```json
{ "email": "...", "password": "..." }
```
**200:** `{ "token": "<jwt>", "expiresIn": 3600 }`. Verify `argon2id` hash against `AdminUsers`. JWT payload: `{ sub: userId, role, exp }`.

### 4.5 `GET /api/admin/bookings` *(auth)*
**Query (all optional):** `status`, `service`, `from`, `to` (`YYYY-MM-DD`), `q` (free-text name/short-id search).
**200:** array of summary rows:
```json
[{
  "id": "WM-1042",
  "patient": "Nadia Pillay",
  "service": "iv-therapy",
  "slot": "2026-05-12T07:30:00Z",
  "status": "confirmed",
  "source": "online",
  "ageBand": "35-44",
  "gender": "F",
  "medicalAid": "Discovery"
}]
```
`ageBand` is computed server-side from `idOrPassport` and returned **only as a band** on the list endpoint.

### 4.6 `GET /api/admin/bookings/:id` *(auth)*
Returns the full booking record including intake fields. Use this only when the doctor opens a single record.

### 4.7 `PATCH /api/admin/bookings/:id` *(auth)*
```json
{ "status": "completed" }
```
or
```json
{ "newSlot": "2026-05-15T08:00:00Z", "notifyPatient": true }
```
When `newSlot` is provided: `events.patch` on the Google Calendar event with the new start/end and `sendUpdates: "all"`; update DynamoDB.

### 4.8 `GET /api/admin/stats` *(auth)*
**Query:** `from`, `to` (default: last 30 days).
**200:**
```json
{
  "totals": { "bookings": 142, "completed": 118, "noshow": 9, "pending": 3, "upcoming": 12 },
  "byService": [{ "service": "gp-practice", "count": 56 }],
  "demographics": {
    "ageBands": { "0-17": 4, "18-24": 12, "25-34": 38, "35-44": 41, "45-54": 27, "55+": 20 },
    "gender": { "F": 92, "M": 47, "Other": 3 }
  }
}
```

---

## 5. Google Calendar integration

**Service account approach** — patients don't grant access; the practice owns the calendar and emails the invite.

**One-off setup (document in README):**
1. GCP project → enable Google Calendar API → create service account → download JSON.
2. Drop the JSON at `backend/src/config/google-sa.json` so it ships inside every Lambda's deployment package. (Add `src/config/google-sa.json` to `.gitignore` and commit a `google-sa.example.json` instead.)
3. Create the practice calendar (e.g., `bookings@wellmed.co.za`).
4. Share that calendar with the service account email, granting **Make changes to events**.
5. Hardcode the calendar ID into the `PRACTICE_CALENDAR_ID` environment variable in `template.yaml`.

**Per-booking call** (in the booking Lambda, using `google-api-python-client`):
```python
event = {
    "summary": f"WellMed · {service_title} · {patient['firstName']} {patient['lastName']}",
    "description": render_event_description(intake),  # no medical-aid number
    "start": {"dateTime": slot_start_iso, "timeZone": "Africa/Johannesburg"},
    "end":   {"dateTime": slot_end_iso,   "timeZone": "Africa/Johannesburg"},
    "attendees": [{
        "email": patient["email"],
        "displayName": f"{patient['firstName']} {patient['lastName']}"
    }],
    "reminders": {
        "useDefault": False,
        "overrides": [
            {"method": "email", "minutes": 24 * 60},
            {"method": "popup", "minutes": 60},
        ],
    },
}

calendar.events().insert(
    calendarId=PRACTICE_CALENDAR_ID,
    body=event,
    sendUpdates="all",
).execute()
```

**Reschedule:** `calendar.events().patch(calendarId=..., eventId=..., body={"start": ..., "end": ...}, sendUpdates="all").execute()`.
**Cancel:** `calendar.events().delete(calendarId=..., eventId=..., sendUpdates="all").execute()`.

**Failure handling:** if `events.insert` raises, the booking Lambda returns `500 { "error": "calendar_unavailable" }` and the booking row is left at `status = "pending"` (no automatic retry — manual cleanup from the admin dashboard if it ever happens). The patient is **never** told `confirmed` until the calendar event is created and `googleEventId` is persisted.

---

## 6. Email (SES)

Two templates, stored as SES email templates (`SES.CreateTemplate` in the SAM stack):

- **`booking_confirmation`** — sent immediately after `POST /api/bookings` succeeds. Contains booking shortId, slot in SAST, doctor name, address, "add to calendar" link (mirrors Google's invite), reschedule URL.
- **`booking_reminder`** — sent ~24h before slot.

From address: `bookings@wellmed.co.za` (verified domain in SES).

---

## 7. Reminder job

**EventBridge Scheduler** invokes a `reminders` Lambda every 30 minutes.

The Lambda queries the single table's `GSI2` with:
- `GSI2PK = "STATUS#confirmed"`
- `GSI2SK BETWEEN "SLOT#<now+23h>" AND "SLOT#<now+25h>"`
- `FilterExpression: attribute_not_exists(reminderSentAt)`

For each hit, send `booking_reminder` via SES, then `UpdateItem` on the booking with `reminderSentAt = now`.

---

## 8. DynamoDB schema — single table

**One table: `WellMed`** (suffixed by stage, e.g. `WellMed-prod`). All entities live in the same table, keyed by `PK` + `SK` with prefixed values that encode the entity type. DynamoDB TTL is enabled on the `ttl` attribute (only the idempotency-key items set it).

### 8.1 Primary key + GSIs

| Key      | Type | Purpose                                                                    |
| -------- | ---- | -------------------------------------------------------------------------- |
| `PK` (S) | HASH | Entity-typed partition (`PATIENT#…`, `BOOKING#…`, `SLOTLOCK#…`, etc.)        |
| `SK` (S) | RANGE | Sub-record discriminator within the partition (`PROFILE`, `META`, `LOCK`, …) |

Three GSIs, all `ProjectionType: ALL`:

| GSI    | Attributes               | Used for                                                              |
| ------ | ------------------------ | --------------------------------------------------------------------- |
| `GSI1` | `GSI1PK` (S), `GSI1SK` (S) | Alt-key lookups: patient by email, booking by shortId, contact messages by month |
| `GSI2` | `GSI2PK` (S), `GSI2SK` (S) | Admin booking list + reminder scan: `STATUS#<status>` + `SLOT#<slotStartISO>` |
| `GSI3` | `GSI3PK` (S), `GSI3SK` (S) | Patient → booking history: `PATIENT#<patientId>` + `SLOT#<slotStartISO>` |

### 8.2 Item shapes

#### Patient profile
| Field      | Value                                                  |
| ---------- | ------------------------------------------------------ |
| `PK`       | `PATIENT#<patientId>`                                  |
| `SK`       | `PROFILE`                                              |
| `GSI1PK`   | `EMAIL#<email>`                                        |
| `GSI1SK`   | `PATIENT`                                              |
| `type`     | `"patient"` (debug-friendly)                            |
| Attributes | `patientId`, `firstName`, `lastName`, `idOrPassport`, `phone`, `email`, `emergencyName`, `emergencyPhone`, `dateOfBirth`, `gender`, `marketingOptIn`, `popiaConsentAt`, `notesForDoctor`, `medicalAid` (map: `provider`, `memberNumber`, `mainMember`, `dependentCode`), `createdAt`, `updatedAt` |

**Upsert by email:** `Query GSI1` with `GSI1PK = EMAIL#<email>` → if a hit, update; else create new with a fresh UUID.

#### Booking
| Field      | Value                                                  |
| ---------- | ------------------------------------------------------ |
| `PK`       | `BOOKING#<bookingId>`                                  |
| `SK`       | `META`                                                 |
| `GSI1PK`   | `SHORTID#<shortId>`                                    |
| `GSI1SK`   | `BOOKING`                                              |
| `GSI2PK`   | `STATUS#<status>`  (e.g. `STATUS#confirmed`)            |
| `GSI2SK`   | `SLOT#<slotStartISO>`                                   |
| `GSI3PK`   | `PATIENT#<patientId>`                                  |
| `GSI3SK`   | `SLOT#<slotStartISO>`                                   |
| `type`     | `"booking"`                                            |
| Attributes | `bookingId`, `shortId`, `patientId`, `service`, `slotStart`, `slotEnd`, `status` (`pending`\|`confirmed`\|`completed`\|`noshow`\|`cancelled`), `source` (`online`\|`phone`\|`walkin`), `googleEventId` (nullable), `intake` (map: `existingConditions`, `allergies`, `currentMeds`, `reasonForVisit`, `notes`), `createdAt`, `updatedAt`, `confirmationSentAt`, `reminderSentAt`, `cancelledAt`, `cancelReason` |

When `status` changes, **also update `GSI2PK`** so the booking moves to the right status-partition on the index.

#### SlotLock (slot-uniqueness guard)
| Field      | Value                                                  |
| ---------- | ------------------------------------------------------ |
| `PK`       | `SLOTLOCK#<service>#<slotStartISO>`                    |
| `SK`       | `LOCK`                                                 |
| `type`     | `"slot_lock"`                                          |
| Attributes | `bookingId`, `createdAt`                                |

Created and deleted as part of a `TransactWriteItems` together with the corresponding Booking item — see §8.3 below. Doesn't use any GSI.

#### ServiceConfig
| Field      | Value                                                  |
| ---------- | ------------------------------------------------------ |
| `PK`       | `SERVICE#<slug>`                                       |
| `SK`       | `CONFIG`                                               |
| `type`     | `"service_config"`                                     |
| Attributes | `service`, `durationMinutes`, `bufferMinutes`, `businessHours` (map: `{ "mon": ["08:00","17:00"], "sat": ["09:00","13:00"], "sun": null }`), `maxPerDay` (nullable) |

Seeded at deploy time (one item per service slug) via a seed Lambda.

#### ContactMessage
| Field      | Value                                                  |
| ---------- | ------------------------------------------------------ |
| `PK`       | `MESSAGE#<messageId>`                                  |
| `SK`       | `RECORD`                                               |
| `GSI1PK`   | `MONTH#<yyyy-mm>`                                      |
| `GSI1SK`   | `MSG#<createdAt>`                                      |
| `type`     | `"contact_message"`                                    |
| Attributes | `messageId`, `name`, `email`, `phone`, `topic`, `message`, `ipAddress`, `handledAt`, `createdAt` |

#### AdminUser
| Field      | Value                                                  |
| ---------- | ------------------------------------------------------ |
| `PK`       | `ADMIN#<email>`                                        |
| `SK`       | `PROFILE`                                              |
| `type`     | `"admin_user"`                                         |
| Attributes | `userId`, `email`, `passwordHash` (argon2id), `role` (`doctor`\|`admin`\|`reception`), `lastLoginAt`, `disabledAt` |

Seeded for Dr Moodley at deploy time.

#### IdempotencyKey
| Field      | Value                                                  |
| ---------- | ------------------------------------------------------ |
| `PK`       | `IDEMPOTENCY#<key>`                                    |
| `SK`       | `RECORD`                                               |
| `type`     | `"idempotency"`                                        |
| Attributes | `response` (JSON-stringified original 201), `createdAt`, `ttl` (epoch seconds, 24h ahead — picked up by DynamoDB TTL) |

#### Counter
| Field      | Value                                                  |
| ---------- | ------------------------------------------------------ |
| `PK`       | `COUNTER#<name>` (e.g. `COUNTER#bookingShortId`)        |
| `SK`       | `VALUE`                                                |
| Attributes | `value` (N) — atomically incremented via `UpdateItem ADD value :one` |

### 8.3 Booking write — TransactWriteItems

Slot uniqueness is enforced by writing the SlotLock and the Booking atomically:

```python
ddb.transact_write_items(TransactItems=[
    {
        "Put": {
            "TableName": TABLE,
            "Item": {
                "PK": f"SLOTLOCK#{service}#{slot_start_iso}",
                "SK": "LOCK",
                "bookingId": booking_id,
                "createdAt": now_iso,
                "type": "slot_lock",
            },
            "ConditionExpression": "attribute_not_exists(PK)"
        }
    },
    {
        "Put": {
            "TableName": TABLE,
            "Item": {
                "PK": f"BOOKING#{booking_id}",
                "SK": "META",
                "GSI1PK": f"SHORTID#{short_id}",
                "GSI1SK": "BOOKING",
                "GSI2PK": "STATUS#pending",
                "GSI2SK": f"SLOT#{slot_start_iso}",
                "GSI3PK": f"PATIENT#{patient_id}",
                "GSI3SK": f"SLOT#{slot_start_iso}",
                # ...all booking attributes
            }
        }
    },
])
```

If the slot is already locked, DynamoDB raises `TransactionCanceledException` with reason `ConditionalCheckFailed` → return `409 { "error": "slot_unavailable" }`.

On cancel/reschedule, delete (or replace) the SlotLock in the same transaction.

### 8.4 Common access patterns

| Need                                          | How                                                                                                     |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Patient by `email`                            | `Query GSI1` with `GSI1PK = EMAIL#<email>`                                                              |
| Patient by `patientId`                        | `GetItem` `PK = PATIENT#<id>`, `SK = PROFILE`                                                            |
| Booking by `bookingId`                        | `GetItem` `PK = BOOKING#<id>`, `SK = META`                                                               |
| Booking by `shortId` (e.g. `WM-1042`)         | `Query GSI1` with `GSI1PK = SHORTID#<shortId>`                                                          |
| All bookings for a patient                    | `Query GSI3` with `GSI3PK = PATIENT#<patientId>`, range over `GSI3SK`                                   |
| Admin list: `status` + slot date range        | `Query GSI2` with `GSI2PK = STATUS#<status>`, `BETWEEN SLOT#<from> AND SLOT#<to>`                       |
| Reminders due (status=confirmed, +23h..+25h)  | `Query GSI2` with `GSI2PK = STATUS#confirmed`, `BETWEEN SLOT#<now+23h> AND SLOT#<now+25h>`, filter on `reminderSentAt = NULL` |
| Availability for service + date               | Compute candidate slots from `ServiceConfig` → `BatchGetItem` on `SLOTLOCK#<service>#<slot>` for each → present items mean unavailable |
| Contact messages by month                     | `Query GSI1` with `GSI1PK = MONTH#<yyyy-mm>`, range over `GSI1SK`                                       |
| Admin login                                   | `GetItem` `PK = ADMIN#<email>`, `SK = PROFILE`                                                          |
| `WM-####` short-id allocation                 | `UpdateItem` `PK = COUNTER#bookingShortId`, `SK = VALUE`, `ADD value :one`, `ReturnValues = UPDATED_NEW` |
| Idempotency check on booking submit           | `GetItem` `PK = IDEMPOTENCY#<header>`, `SK = RECORD` → hit means replay the stored `response`            |

---

## 9. Project layout (suggested)

```
backend/
├── template.yaml
├── requirements.txt           # boto3 is in the Lambda runtime; pin googleapis, PyJWT, argon2-cffi, pydantic
├── samconfig.toml
├── README.md
├── src/
│   ├── lib/
│   │   ├── __init__.py
│   │   ├── dynamo.py           # boto3 resource + helpers
│   │   ├── google_calendar.py  # service-account auth + event helpers
│   │   ├── ses.py              # template send helpers
│   │   ├── jwt_util.py         # sign + verify (PyJWT)
│   │   ├── ids.py              # uuid, shortId via Counters
│   │   ├── time_util.py        # SAST <-> UTC, slot math
│   │   └── validate.py         # request schema validators (pydantic)
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── availability_get.py
│   │   ├── bookings_post.py
│   │   ├── contact_post.py
│   │   ├── admin_login_post.py
│   │   ├── admin_bookings_get.py
│   │   ├── admin_bookings_by_id_get.py
│   │   ├── admin_bookings_by_id_patch.py
│   │   ├── admin_stats_get.py
│   │   └── jobs_reminders.py   # EventBridge-triggered, every 30 min
│   ├── config/
│   │   └── google-sa.json      # gitignored; service-account credentials
│   └── seed/
│       ├── seed_service_config.py
│       └── seed_admin_user.py
└── tests/
    └── ...
```

---

## 10. Deliverables

1. **`template.yaml`** — defines: the single `WellMed` DynamoDB table with `PK`/`SK` plus `GSI1`/`GSI2`/`GSI3` (all `ProjectionType: ALL`) and TTL enabled on the `ttl` attribute, every Lambda function (with the hardcoded env vars from §11 baked into `Globals.Function.Environment.Variables`), API Gateway HTTP API routes, the single EventBridge schedule for reminders, SES templates (as `AWS::SES::Template`), IAM role with least-privilege actions on the one table (plus SES + CloudWatch Logs) per Lambda.
2. **All Lambda handlers** in `src/handlers/`, sharing helpers in `src/lib/`.
3. **`src/config/google-sa.json`** — gitignored real credentials; commit a `google-sa.example.json` with the expected shape.
4. **`README.md`** covering: prerequisites, `sam build && sam deploy`, how to seed `ServiceConfig` and the admin user, how to drop the Google service-account JSON into `src/config/`, how to verify the SES domain identity, how to run `sam local invoke` against each handler with sample events in `events/`.
5. **`events/*.json`** — sample API Gateway events for every handler so the implementer can `sam local invoke` each one.
6. **CORS** configured on the HTTP API to allow the production CloudFront origin (hardcoded from the `FRONT_END_ORIGIN` env var).
7. **No tests required for v1**, but include at least one happy-path integration script in `tests/booking_e2e.py` that hits a deployed stack end-to-end.

---

## 11. Hardcoded configuration (template.yaml `Globals.Function.Environment.Variables`)

No Secrets Manager, no stack parameters for env. Put these directly in `template.yaml` under `Globals.Function.Environment.Variables` (or per-function as needed). The implementer fills in literal values before `sam deploy`.

| Env var                | Example                              | Purpose                                |
| ---------------------- | ------------------------------------ | -------------------------------------- |
| `FRONT_END_ORIGIN`     | `https://wellmed.co.za`              | CORS allow-list                         |
| `PRACTICE_CALENDAR_ID` | `bookings@wellmed.co.za`             | Google calendar to write into           |
| `GOOGLE_SA_PATH`       | `/var/task/src/config/google-sa.json` | Where the bundled SA file lives in the Lambda runtime |
| `JWT_SECRET`           | `replace-me-with-a-long-random-string` | HS256 secret for admin tokens           |
| `JWT_EXPIRY_SECONDS`   | `3600`                               | Admin token TTL                         |
| `SES_FROM_ADDRESS`     | `bookings@wellmed.co.za`             | Verified SES sender                     |
| `STAGE`                | `prod`                               | Suffix on table names                   |

The Google service-account JSON itself is bundled as a file (see §5 step 2), not a secret reference.

---

## 12. Acceptance criteria

- Submitting the booking form on the live front-end against the deployed API produces:
  - A row in `Bookings` with `status = "confirmed"`.
  - A Google Calendar event on the practice calendar with the patient as an attendee.
  - An email to the patient via SES.
- The admin dashboard, with a valid JWT, can list, filter, view, reschedule and complete bookings.
- A double-clicked submit with the same `Idempotency-Key` returns the original 201 and does **not** create a second booking.
- The reminders Lambda, run manually, finds and emails reminders for slots ~24h ahead and records `reminderSentAt`.
- Front-end `js/booking.js` requires **no changes** — your responses match the shapes above.

---

## 13. Notes to the implementing engineer / Claude

- Use **boto3** (already shipped in the Python Lambda runtime — don't add it to `requirements.txt`).
- Suggested dependencies in `requirements.txt`: `google-api-python-client`, `google-auth`, `PyJWT`, `argon2-cffi`, `pydantic` (v2). Keep the dependency tree small; SAM packages all of them into each Lambda's deployment artifact.
- Use **`google-api-python-client`** + **`google-auth`** for the Calendar API. Build the `service_account.Credentials` once at module scope and reuse across warm invocations; reuse the `build('calendar', 'v3', credentials=...)` client too.
- Initialise boto3 resources/clients at module scope (outside the handler function) so they're reused across warm invocations.
- Validate request bodies with **pydantic v2** models; return `400 { "error": "validation", "fields": [...] }` on `ValidationError`.
- Keep handler files thin — push logic into `src/lib/*` so it's unit-testable in isolation.
- Log statements may include patient IDs and emails (no encryption requirement) but avoid logging raw `idOrPassport` or `medicalAid.memberNumber` in plain text.
- The DynamoDB design is **single-table** (§8) — do not split it into per-entity tables. Ask the user before deviating on other choices (e.g. SES vs Postmark).
