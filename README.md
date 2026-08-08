# WellMed backend

AWS SAM stack that powers the WellMed booking flow, contact form, and admin dashboard. Python 3.12 Lambdas behind an HTTP API, DynamoDB single table, SES templated email, Google Calendar via a bundled service account, and an EventBridge schedule that fires reminder emails.

## Layout

```
backend/
├── template.yaml               # SAM template (DynamoDB, API GW, Lambdas, SES, Schedule)
├── samconfig.toml
├── requirements.txt            # google-api-python-client, PyJWT, argon2-cffi, pydantic
├── src/
│   ├── handlers/               # one file per Lambda
│   ├── lib/                    # shared helpers (dynamo, jwt, ses, google_calendar, ...)
│   ├── config/google-sa.json   # gitignored — drop the real service-account JSON here
│   └── seed/                   # ServiceConfig + admin user seeders
├── events/                     # sample API Gateway events for `sam local invoke`
└── tests/booking_e2e.py        # end-to-end smoke test against a deployed stack
```

## Prerequisites

- AWS CLI configured for the target account (`AWS_PROFILE=...`, `eu-west-1`)
- AWS SAM CLI ≥ 1.110
- Python 3.12
- A Google Cloud project with the Calendar API enabled
- A verified SES domain identity for `wellmed.org.za` (or the address you set in `SES_FROM_ADDRESS`)

## One-off setup

### 1. Google service account

1. In the GCP console, create a service account in the project that owns the practice calendar.
2. Enable the **Google Calendar API**.
3. Download the JSON key and save it as `src/config/google-sa.json`. The file is gitignored — commit `google-sa.example.json` only.
4. In Google Calendar, share the practice calendar (e.g. `bookings@wellmed.org.za`) with the service account's email and grant **Make changes to events**.
5. Set `PRACTICE_CALENDAR_ID` in `template.yaml` (`Globals.Function.Environment.Variables`) to the calendar ID.

### 2. SES

1. Verify the sending domain in SES (`wellmed.org.za`).
2. Move the account out of SES sandbox (or verify each recipient address while still sandboxed).
3. `template.yaml` provisions the `booking_confirmation` and `booking_reminder` templates automatically.

### 3. Hardcoded env vars

Edit `template.yaml` → `Globals.Function.Environment.Variables` and replace the placeholder values:

| Var                    | Replace with                                              |
| ---------------------- | --------------------------------------------------------- |
| `FRONT_END_ORIGIN`     | Production CloudFront / site origin                       |
| `PRACTICE_CALENDAR_ID` | Calendar address that the service account can write to    |
| `JWT_SECRET`           | A long random string (≥ 32 chars)                          |
| `SES_FROM_ADDRESS`     | Verified SES sender                                        |

The CORS allow-list in `template.yaml` mirrors `FRONT_END_ORIGIN` — update both if you change it.

## Build + deploy

```powershell
sam build
sam deploy --guided   # first deploy only — subsequent: sam deploy
```

The stack outputs `ApiUrl` (paste into `WM.api.baseUrl` in the front-end `js/config.js`) and `TableName`.

## Seeding data

After the first deploy, seed the per-service config and the doctor's admin user. Both scripts use the same `TABLE_NAME` and AWS credentials as the deployed Lambdas:

```powershell
$env:TABLE_NAME = "WellMed-prod"
$env:AWS_REGION = "eu-west-1"

python -m src.seed.seed_service_config

$env:ADMIN_EMAIL = "doctor@wellmed.org.za"
$env:ADMIN_PASSWORD = "use-a-strong-password"
$env:ADMIN_ROLE = "doctor"
python -m src.seed.seed_admin_user
```

Re-running `seed_service_config` is safe — it overwrites the items. Re-running `seed_admin_user` rotates the password for the same email.

## Local invocation

Each handler has a sample event in `events/`. With AWS creds and the env vars from `template.yaml` exported, you can run any of them:

```powershell
sam local invoke AvailabilityGetFn      --event events/availability_get.json
sam local invoke BookingsPostFn         --event events/bookings_post.json
sam local invoke ContactPostFn          --event events/contact_post.json
sam local invoke AdminLoginPostFn       --event events/admin_login_post.json
sam local invoke AdminBookingsGetFn     --event events/admin_bookings_get.json
sam local invoke AdminBookingByIdGetFn  --event events/admin_booking_by_id_get.json
sam local invoke AdminBookingByIdPatchFn --event events/admin_booking_by_id_patch.json
sam local invoke AdminStatsGetFn        --event events/admin_stats_get.json
sam local invoke RemindersFn            --event events/reminders.json
```

For admin handlers, run `admin_login_post` first and paste the returned `token` into the `authorization` header of the other events.

## End-to-end smoke test

After `sam deploy`, run the smoke test against the deployed stack:

```powershell
$env:API_URL = "https://<api-id>.execute-api.eu-west-1.amazonaws.com"
$env:ADMIN_EMAIL = "doctor@wellmed.org.za"
$env:ADMIN_PASSWORD = "use-a-strong-password"
python tests/booking_e2e.py
```

It books a slot, replays the same `Idempotency-Key` (expecting the same `WM-####`), logs in as admin, fetches the detail row, and marks the booking `completed`.

## DynamoDB shape

Single table `WellMed-<stage>`, keys `PK`/`SK` with `GSI1`/`GSI2`/`GSI3` (all `ProjectionType: ALL`), TTL on the `ttl` attribute (idempotency keys only). Item shapes and access patterns are documented in `BACKEND_BUILD_PROMPT.md` §8.

## Notes / known limitations

- No retries on the Google Calendar insert: if it fails, the booking row is left at `status = "pending"` and the API returns `500 { "error": "calendar_unavailable" }`. The admin dashboard can finalise or cancel manually.
- The `Idempotency-Key` header is persisted with a 24h TTL via DynamoDB.
- Patient `idOrPassport` and `medicalAid.memberNumber` are deliberately omitted from log statements (see §13 of the spec).
- v1 has no retries, no rate limits, no MFA, no refresh tokens — see the "Out of scope" list at the top of the spec.
