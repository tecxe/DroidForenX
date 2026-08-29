# DroidForenX

A Django- and PostgreSQL-based **Android digital forensics** platform built across
16 development phases. It acquires logical data from Android devices (via ADB),
then analyzes, hashes, carves, timelines, searches, and reports on the recovered
artifacts — with a strict, honest status engine and an append-only chain of
custody and audit trail.

> **Honesty guarantee.** DroidForenX **never fabricates results**. Its
> `AcquisitionStatus` engine has 8 real states (`ACQUIRED`, `PARTIAL`,
> `RECOVERED`, `DELETED_RECOVERABLE`, `DELETED_UNRECOVERABLE`,
> `NOT_ACCESSIBLE`, `NOT_PRESENT`, `FAILED`) and it reports exactly what it
> could actually recover. If ADB isn't present, a device can't be reached, or a
> database can't be opened, it is recorded as such — never as success.

---

## Features by phase

| Phase | Deliverable | App(s) |
|-------|-------------|--------|
| 1 | Django 5.2 + PostgreSQL scaffold, env-driven settings, evidence/report storage layout | `droidforenx` |
| 2 | Device detection & ADB adapter abstraction | `devices` |
| 3 | Android API↔version / capability compatibility tables (API 35 / Android 15) | `devices` |
| 4 | Evidence & Case models, 8-state acquisition status engine, secure disk storage | `evidence`, `cases` |
| 5 | ADB logical acquisition (filesystem pulls), image import, honest status handling | `acquisition` |
| 6 | Streaming cryptographic hashing (MD5/SHA-1/SHA-256/SHA-512), integrity manifests | `hashing` |
| 7 | File type identification (pure-Python magic bytes) & EXIF metadata | `analysis` |
| 8 | SQLite forensic analysis: live schema/counts + deleted-file detection from page format | `database` |
| 9 | Social media extraction (WhatsApp msgstore.db / wa.db) | `social` |
| 10 | File carving (signature header/footer recovery), deleted-data status | `carving` |
| 11 | Timeline reconstruction (evidence, artifacts, EXIF datetimes) | `timeline` |
| 12 | Full-text search over indexed evidence (PostgreSQL FTS) | `searching` |
| 13 | Append-only chain of custody | `custody` |
| 14 | PDF (ReportLab) & HTML case reports | `reports` |
| 15 | Security audit logging (append-only) & hardening settings | `security` |
| 16 | Testing & documentation | — |

---

## Architecture

- **Backend:** Django 5.2, Django REST not used (server-rendered Django templates +
  Bootstrap for the dashboard).
- **Database:** PostgreSQL (native full-text search via
  `django.contrib.postgres`; `SearchVectorField` + `GinIndex` for ranking).
- **Storage:** evidence lives under `EVIDENCE_ROOT` (default `evidence/`) in
  subdirectories (`original/`, `acquired/`, `recovered/`, `carved/`,
  `working/`) — kept **outside** web-served static/media paths so case data can
  never be served to a browser. A marker file (`EVIDENCE_DIR.txt`) and a
  `safe_join` traversal guard protect the tree.
- **Status honesty:** the `AcquisitionStatus` engine (8 states) drives every
  record; nothing is ever marked recovered unless it actually was.
- **Append-only integrity:** both `CustodyEvent` (chain of custody) and
  `AuditLog` (security) forbid UPDATE and DELETE at the model layer.

### Module layout

```
droidforenx/
  manage.py
  droidforenx/settings.py        # env-driven config + security hardening
  devices/                       # ADB adapter + Android compatibility
  cases/                         # Case / Investigator
  evidence/                      # Evidence / Artifact, storage, status engine
  acquisition/                   # logical acquisition, ADB engine, image import
  hashing/                       # streams + HashRecord + manifests
  analysis/                      # magic bytes + EXIF
  database/                      # SQLite analysis + page-format deleted detection
  social/                        # WhatsApp extraction
  carving/                       # signature carving
  timeline/                      # timeline reconstruction
  searching/                     # PostgreSQL FTS
  custody/                       # chain of custody
  reports/                       # PDF/HTML reports
  security/                      # audit logging
  setup_adb.py                   # optional platform-tools bundler for ADB
```

---

## Setup

Requirements: **Python 3.11+**, **PostgreSQL 18+**, git.

```powershell
# 1. Create & activate a virtual environment
py -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure PostgreSQL connection (create a role + database, e.g.)
#    role `droidforenx` (CREATEDB so Django can build the test DB), db `droidforenx`
#    Set these in a `.env` file next to manage.py:
#      DB_NAME=droidforenx
#      DB_USER=droidforenx
#      DB_PASSWORD=droidforenx
#      DB_HOST=localhost
#      DB_PORT=5432

# 4. Apply migrations and run
python manage.py migrate
python manage.py runserver
```

A minimal Django `auth.User` (for admin + audit actors) is created as normal via
`python manage.py createsuperuser`.

---

## Management commands

| Command | Purpose |
|---------|---------|
| `detect_devices` | List Android devices reachable via ADB |
| `compat_report` | Show Android API↔version compatibility tables |
| `register_evidence` | Register an evidence container for a case |
| `hash_evidence` | Hash evidence streams (MD5/SHA-1/SHA-256/SHA-512) |
| `verify_hashes` | Re-verify evidence against stored hashes (tamper detection) |
| `analyze_evidence` | Detect file types & extract EXIF metadata |
| `analyze_sqlite` | Live SQLite schema/count + deleted-file detection |
| `analyze_social` | Extract WhatsApp messages/contacts |
| `carve_evidence` | Carve files by signature (recover deleted data) |
| `build_timeline` | Reconstruct timeline from recovered data |
| `search_evidence` | Full-text search (and `--rebuild` to index) |
| `custody <id> list\|log ACTION` | Inspect / append chain of custody |
| `generate_report <case> [--format pdf\|html]` | Render a case report |
| `audit` | Inspect / emit security audit events |

---

## What it does NOT do

DroidForenX is a forensics **analysis** platform. It explicitly excludes:
PIN/password cracking, FRP / lock-screen bypass, rooting, exploitation, spyware,
credential theft, and unauthorized access. These are outside scope by design.

---

## Testing

```powershell
python manage.py test
```

The suite covers all 14 apps (~120+ tests) and requires PostgreSQL (the `searching`
app and Django's test runner both need a Postgres test database).

---

## Optional: ADB platform-tools

`setup_adb.py` can bundle Google's `platform-tools` (~5 MB) to enable live device
acquisition via ADB. It is optional — all analysis, hashing, carving, timeline,
search, reporting, custody and audit features work standalone on imported evidence.
