# DroidForenX

A Python desktop application for **logical acquisition and analysis of Android
devices** over ADB, built for authorised digital-forensic examination.

It pulls artifacts from a connected device, **preserves every byte it acquires**,
hashes each artifact at the moment of acquisition, parses from the preserved
copy, and records an append-only chain of custody for every action.

> ### Current verification status — read this first
>
> Verified against a real device on **14 October 2026**.
>
> | Claim | Status |
> |---|---|
> | Evidence preservation, hashing, custody, tamper detection | **Tested** — 22 automated tests, no device needed |
> | 24 acquisition loaders implemented | **Code complete** |
> | Device-verified loaders | **14 of 24** |
> | Root-gated, reported `NOT_ACCESSIBLE` | **10 of 24** |
>
> **Verified device:** TECNO TECNO KL6 (Tecno Spark 30), Android 14 (API 34),
> serial `13276374A7004073`, build `TECNO/KL6-OP/TECNO-KL6:14/UP1A.231005.007/260709V1745:user/release-keys`,
> **not rooted**, FBE `encrypted`, bootloader `green`.
>
> Every T1 claim above is stored in the case's `verification.jsonl` with that
> serial, build fingerprint, date and observed row count as its evidence. Nothing
> is marked verified without a record — `record_verification()` rejects a
> `T1_DEVICE_VERIFIED` claim that carries no evidence.
>
> `README.md` in earlier revisions of this repository described a Django +
> PostgreSQL platform with 16 phases and ~138 tests. **That software does not
> exist in this repository.** It was removed in commit `f761d7e`. This document
> describes only code that is present.

---

## The honesty guarantee

DroidForenX reports what it actually achieved, per loader, using an eight-state
engine (`desktop/core/evidence_store.py`):

| Status | Meaning |
|---|---|
| `ACQUIRED` | Data was pulled and items were recovered |
| `PARTIAL` | Some items recovered, some failed |
| `RECOVERED` | Deleted/previous-version content recovered |
| `DELETED_RECOVERABLE` | Deletion detected, recovery possible |
| `DELETED_UNRECOVERABLE` | Deletion detected, content beyond recovery |
| `NOT_ACCESSIBLE` | Blocked by permissions (usually root required) |
| `NOT_PRESENT` | Loader ran fine; the device held nothing of that type |
| `FAILED` | The loader raised an error |

Two rules enforce this:

1. **A loader that recovered nothing is never reported as success.** It gets
   `NOT_PRESENT`, not `ACQUIRED`. The old code marked every non-throwing call
   `"completed"` regardless of results.
2. **Verification tiers are earned, never declared.** There is no table of
   "verified" loaders. A loader is `T3_UNTESTED` until a run records evidence
   for a higher tier in the case's `verification.jsonl`. Claiming
   `T1_DEVICE_VERIFIED` without recording device serial, OS build and date is
   **rejected by the code** — see `record_verification()`. All 24 loaders
   currently hold an earned status recorded on the device above.

---

## Requirements

- **Python 3.11+**
- **Android platform-tools** (ADB) — bundled under `tools/platform-tools/`, or
  install Android SDK Platform Tools
- Device with **USB debugging** enabled
- Windows / Linux / macOS

### Windows

```powershell
# 1. Virtual environment
py -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Dependencies
pip install -r requirements.txt
```

Then run `desktop\run.bat`, or `python -m desktop.main`.

### Linux (Kali, Debian, Ubuntu, Fedora)

DroidForenX is developed on Windows and runs unchanged on Linux. Kali needs
three extra steps: the Qt6 system libraries, the udev rules, and a bundled adb.

```bash
# 1. System packages for Qt6
sudo apt update
sudo apt install -y python3-venv python3-pyqt6 libxcb-cursor0 libxkbcommon-x11-0

# 2. Virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Dependencies
pip install -r requirements.txt
```

Then run `./desktop/run.sh`, or `python3 -m desktop.main`.

#### udev rules (required on Linux)

Without these rules only root can talk to the device, and you would be forced
to run the whole application under `sudo` — which for a forensic tool means
writing case evidence as root and leaving root-owned files behind.

```bash
sudo cp tools/99-android-rules.rules /etc/udev/rules.d/99-android-rules.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Unplug and replug the device, then confirm it lists as `device` (not
`unauthorized`):

```bash
adb devices -l
```

#### adb on Linux

`run.sh` uses whichever it finds first:

1. `DROIDFORENX_ADB_PATH` environment variable, if set and present
2. the bundled copy in `tools/platform-tools/`
3. `adb` on `PATH` (Kali ships it as `android-tools-adb`)

To install the bundled copy explicitly:

```bash
python3 setup_adb.py          # download official platform-tools
python3 setup_adb.py --check  # report availability
```

If the bundle is missing, `sudo apt install android-tools-adb` also works — the
tool falls back to `PATH` automatically.

### macOS

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -m desktop.main
```

`requirements.txt` installs `PyQt6`, `fpdf2`, `openpyxl`, `Pillow`,
`pyinstaller`.

---

## Running

```powershell
python manage.py gui        # launch the desktop application
python manage.py version    # print version information
```

On Linux the equivalent is `python3 manage.py gui`, or simply `./desktop/run.sh`
from the repository root.

### Connecting a device

1. Enable **Developer options → USB debugging** on the phone.
2. Set USB mode to **File Transfer (MTP)** — charging-only mode hides content
   providers from ADB.
3. Connect by USB and accept the **Allow USB debugging** prompt on the phone.
4. Confirm the device appears:

```powershell
# Windows
.\tools\platform-tools\adb.exe devices -l
```

```bash
# Linux / macOS
adb devices -l
```

The phone must be **unlocked** for the authorisation prompt to appear.

---

## Architecture

A self-contained PyQt6 desktop application. No web server, no external
database, no network calls during acquisition.

```
desktop/
  main.py                     entry point + global crash handler
  core/
    adb_manager.py            ADB discovery, shell, pull, root detection
    evidence_store.py         case tree, hashing, custody, status, tiers
    forensic_engine.py        the 24 loaders
    database.py               SQLite helper
    doc_metadata.py           document metadata extraction
    email_extractor.py        email artifact extraction
    sim_forensics.py          SIM / network operator analysis
  ui/
    main_window.py            window shell, stage navigation
    theme.py                  palette and typography
    pages/                    home, identify, seize, case, acquire,
                              examine, artifacts, browse, device, report
    widgets/                  data table, log console, status bar, stage bar
  tests/                      unittest suites
tools/platform-tools/         bundled ADB
manage.py                     CLI wrapper (gui | version)
```

### Case storage layout

Per case, under `EVIDENCE_ROOT`, which defaults to:

- `%USERPROFILE%\.droidforenx\evidence` (Windows)
- `~/.droidforenx/evidence` (macOS / Linux)

Override with the `DROIDFORENX_EVIDENCE_ROOT` environment variable. Evidence is
deliberately kept out of the repository and out of any web-served path.

### Reports

Two outputs are generated from a case, and both contain **evidence only** — no
AI output:

```bash
# PDF evidence report
python -m desktop.report.evidence_report CASE-BASELINE-001

# XLSX verification workbook
python -m desktop.report.evidence_workbook CASE-BASELINE-001
```

The **PDF** is the narrative report: case header, device scope, the per-loader
status table, earned verification tiers, the manifest, and the chain of custody.

The **XLSX** is the examiner's working copy, with sortable and filterable
sheets: `Case Summary`, `Integrity`, `Sources`, `Artifacts`, `Custody`. Its
`Integrity` sheet independently re-hashes every artifact and reports `OK`,
`MISMATCH` or `MISSING` per file. Building it is strictly **read-only** — it
never writes to the case tree and never appends a custody event, so producing a
report cannot alter evidence. Use `evidence_store.verify()` when you *want* the
verification run recorded in the log.

```
<case_id>/
  case.json                  case metadata, written once at open
  acquired/                  IMMUTABLE — every byte pulled from the device
  parsed/                    derived output — regenerable, never evidence
  manifests/
    SHA256SUMS                "<sha256>  <path>" per acquired artifact
    custody.jsonl             append-only chain of custody
    artifacts.jsonl           append-only artifact register
    status.jsonl              append-only per-loader outcome log
    verification.jsonl        append-only record of earned verification tiers
```

**Invariants**

- Nothing in `acquired/` is ever modified or deleted by analysis code. Parsers
  read *from* it; results go to `parsed/`.
- Every acquired file is SHA-256 hashed at acquisition time.
- `custody.jsonl`, `artifacts.jsonl`, `status.jsonl` and `verification.jsonl`
  are append-only. This module contains no truncate, rewrite or delete of a
  log record.
- `verify(case_id)` re-hashes `acquired/` and returns
  `VERIFIED` / `INTEGRITY_FAILURE`, listing modified and missing files.
- Case identifiers are sanitised, so a crafted case ID cannot traverse out of
  the evidence root.

---

## The 24 loaders

**Device-verified — readable on this unrooted phone** (14). Each was confirmed by
a live read-only query on the TECNO KL6 above; observed volumes are in
`verification.jsonl`:

`call_log` (1403) · `sms_mms` (983) · `contacts` (694) · `calendar` (530) ·
`gps` · `wifi` · `bluetooth` · `apps` · `installed_apps` (423 packages) ·
`app_permissions` · `clipboard` · `notifications` · `usage_stats` · `notes`

**Root-gated — confirmed `NOT_ACCESSIBLE`** (10). They read app-private
`/data/data/<pkg>` or keystore `/data/misc/wifi` paths, and Android denies the
shell. The probe confirmed `permission denied` on every one; they are recorded
and surfaced, never silently dropped:

`whatsapp` · `telegram` · `instagram` · `snapchat` · `tiktok` · `facebook` ·
`transactions` · `browser` · `wifi_passwords` · `deleted`

`is_device_rooted()` and `device_root_summary()` in `adb_manager.py` perform
root detection so the tool knows its own ceiling before interpreting results.

### Reproducing the baseline

```powershell
python probe_device.py      # read-only capability probe; pulls nothing
python record_baseline.py   # opens a case, records statuses + earned tiers
```

Both are non-destructive. `probe_device.py` performs no writes and no pulls;
`record_baseline.py` deliberately pulls **no personal content** — it records
capability, integrity and chain of custody only.

---

## Limitations

State these in any report; they are properties of Android, not bugs to be
"fixed" by this tool.

- **App-private data needs root.** Since Android 7, `/data/data/<pkg>` is
  unreadable to the ADB shell. Without root, messaging, social media, browser
  history, saved WiFi keys and mobile-money data cannot be acquired.
- **Modern WhatsApp encrypts its database.** Even with root, current WhatsApp
  uses a keystore-derived key (crypt15). Recovery typically requires the
  crypt file and a pre-upgrade copy, or a full filesystem image.
- **Scoped storage** (Android 10+) limits shared-storage visibility per app.
- **Multiple Android users.** A device carrying a secondary user or work profile
  makes plain `pm list packages` abort with
  `SecurityException: Shell does not have permission to access user 999`,
  returning error text instead of a package list. All `pm list` calls go
  through `adb_manager.pm_list()`, which scopes to `--user 0` and falls back to
  the unscoped form on older builds. This bug silently produced **zero**
  packages on this device before the fix.
- **Modern social apps** network their content through private APIs. DroidForenX
  performs **no** account logins and **no** API calls to social platforms. It
  reads only local files that an authorised, properly unlocked device exposes.
- **Logical acquisition is not a full image.** Files not written to accessible
  storage are not recovered. A full flash image is a separate procedure with
  its own legal authorisation requirement.
- **Encryption**: a device with a locked FBE screen yields little.

---

## Testing

```powershell
python -m unittest desktop.tests.test_evidence_store -v
```

22 tests, no device required. They cover the properties that matter:

- an acquired artifact survives being read (the original bug was that 11
  loaders deleted evidence after parsing)
- acquired artifacts are hashed on write
- SHA-256 manifests and the custody log are written correctly
- custody is append-only across re-opens, and case metadata is not overwritten
- `verify()` detects both **tampering** and **deletion**
- a loader recovering nothing is `NOT_PRESENT`, not success
- verification tiers cannot be claimed without evidence
- case export refuses to write into the evidence tree (which would destroy the
  case being copied)
- derived output in `parsed/` does not alter the evidence count
- case IDs cannot traverse out of the evidence root

`desktop/tests/` also contains `test_quick.py`, `test_deep.py`,
`test_full_adb.py` and `test_integration.py`. These require a connected device
and are **not** covered by CI.

---

## Scope

DroidForenX is an **analysis and acquisition tool for authorised examination**.
It does not and will not include: credential or PIN cracking, FRP or lock-screen
bypass, rooting or unlocking of seized devices, exploitation, spyware, or
unauthorised access. Use only on devices you are lawfully authorised to examine.

---

## AI Assist — **not implemented**

`.env.example` contains keys for an optional AI assistance layer
(`OPENROUTER_API_KEY`, `DROIDFORENX_AI_*`). **None of it is built yet.** There
is no code in this repository that reads those variables, calls OpenRouter, or
produces AI output. They are listed so the intended design is on record.

If built, the rules it must follow are fixed in advance:

- AI output is **never** evidence and **never** enters the evidence chain. It
  lives in a separate store and is excluded from the evidentiary findings.
- Every AI call records model, prompt hash, parameters, timestamp and the
  artifact IDs it was given, so any AI-derived statement is traceable to source.
- PII (numbers, emails, names) is redacted locally before anything leaves the
  host, and AI features are off by default and opt-out per case.
- A human examiner confirms anything AI-derived before it appears in a report.

Deleting the `DROIDFORENX_AI_*` lines changes nothing about how the tool works.

---

## Configuration

Optional `.env` (gitignored; see `.env.example`). All acquisition, hashing,
custody and reporting features work **without** any `.env` file — configuration
is only needed for optional AI assistance, which is deliberately kept outside the
evidence chain and does not affect acquisition results.

```powershell
DROIDFORENX_EVIDENCE_ROOT=evidence
DROIDFORENX_AI_ENABLED=false
DROIDFORENX_AI_MODEL=meta-llama/llama-3.1-8b-instruct
DROIDFORENX_AI_REDACT=true
```