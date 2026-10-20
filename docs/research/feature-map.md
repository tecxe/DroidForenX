# Feature map: README claims vs source and tests

Audited 2026-10-20 against `main` at `v2.0.15`. Every row was checked by reading
the source and, where possible, by running or inspecting something. "Confirmed"
means the code does what the README says; it does not mean the feature has been
proven on a device unless the row says so.

Three defects were found and fixed in the same pass; each finding carries a
**Status** line. Test counts are measured, not quoted.

---

## Summary

| Claim (README) | Source | Tests | Verdict |
|---|---|---|---|
| Five forensic stages | `desktop/ui/theme.py:101` — `STAGE_NAMES = [Seizure, Identification, Acquisition, Examination, Reporting]` | `test_main_window.py` (7) | **Confirmed** |
| 24 acquisition loaders | `desktop/core/forensic_engine.py` — `ANALYSIS_MODULES`, **24 entries** | `test_forensic_engine.py` (8) | **Confirmed** |
| 10 root-gated loaders reported `NOT_ACCESSIBLE` | `forensic_engine.py` — `ROOT_REQUIRED_MODULES`, exactly **10** | `test_forensic_engine.py` | **Confirmed** |
| 14 of 24 device-verified | `CASE-BASELINE-001/manifests/verification.jsonl` — 14 `T1_DEVICE_VERIFIED` records with serial, build, date, observed count | — | **Confirmed as recorded**; the claims are reachability, not preservation (Finding 2) |
| Evidence preservation, hashing, custody, tamper detection | `evidence_store.py` — `hash_file`, `record_acquired`, `log_custody`, `compute_integrity`, `verify`, `audit_claims` | `test_evidence_store.py` (**27**) | **Code confirmed**; end-to-end only since Finding 3 was fixed |
| RAW / ZIP image output | `desktop/core/imaging/` — `raw.py`, `zip.py`, `registry.py`, `sources.py` | `test_imaging.py` (38), `test_create_image_dialog.py` (24), `test_image_registry.py` (8) | **Confirmed** |
| Four report formats | `desktop/ui/pages/report_page.py:80-84` — HTML, PDF, JSON, XLSX | `test_ui_export.py` (9), `test_evidence_workbook.py` (5) | **Confirmed** |
| Dark / Light / Follow System themes | `desktop/ui/theme.py` | `test_theme.py` (27) | **Confirmed** |
| Reproducible build | `build.py` — `SOURCE_DATE_EPOCH` from HEAD, `PYTHONHASHSEED=0`, drift check against `requirements-lock.txt` | `test_reproducible_build.py` (12) | **Confirmed by experiment** — two clean builds of `69de6a2` produced identical `ded26a43…` |
| Logical (`/sdcard`) imaging for unrooted devices | `imaging/sources.py` — tar over `adb exec-out` through the RAW writer, `source_type=logical` | `test_imaging.py` | **Confirmed** (new in v2.0.15) |
| Suite size | — | **165** test functions; 164 run + 1 opt-in skip | **Confirmed** |

Measured suite, per module:

```
test_imaging.py                 38    test_evidence_store.py         27
test_theme.py                   27    test_create_image_dialog.py    24
test_reproducible_build.py      12    test_ui_export.py               9
test_forensic_engine.py          8    test_image_registry.py          8
test_main_window.py              7    test_evidence_workbook.py       5
                                                              total 165
```

Four files in `desktop/tests/` contain **zero** test functions and are stubs:
`test_deep.py`, `test_full_adb.py`, `test_integration.py`, `test_quick.py`. They
inflate a naive `test_*.py` glob count by four filenames while contributing
nothing. The documented command list correctly excludes them.

---

## Findings

### 1. A source comment contradicted both the README and the case records

`forensic_engine.py` said every loader was `T3_UNTESTED` "today", while
`CASE-BASELINE-001` held 14 `T1_DEVICE_VERIFIED` records written 2026-10-14. The
README's "14 of 24" was backed; the comment was not.

**Status: Fixed.** The comment now describes the mechanism instead of asserting a
current state, and points a reader at `audit_claims()` for the real answer.
**Severity:** documentation defect, no behavioural impact.

### 2. T1 claims had nothing behind them but free text

`record_verification()` rejected an *empty* evidence string and nothing else, so
"observed 1403 rows" passed as proof of a device claim. A case could be fully
marked device-verified while holding no artifact at all — which is precisely what
`CASE-BASELINE-001` is.

The subtlety: `record_baseline.py` *deliberately* pulls no content, because the
baseline exists to prove the chain works without reading the examiner's own
messages. Those 14 claims were honest about what the run did, but the tier name
implies preserved evidence.

**Status: Fixed.** T1 now requires one of two explicit stances:

- `artifact_id=` — must already exist in the case's artifact log, so the claim
  points at a file whose SHA-256 is in the manifest. Stored `basis="artifact"`.
- `capability_only=True` — stored `basis="capability"`, for a run that proved
  reachability and preserved nothing.

Neither is accepted silently; passing only prose raises `ValueError`.
`record_baseline.py` now passes `capability_only=True`, and a new
`audit_claims(case_id)` splits claims into `backed`, `capability` and `unbacked`.
`verify()` carries that audit in its result.

Against the real case:

```
CASE-BASELINE-001  verdict=NO_MANIFEST checked=0
   backed=0  capability=0  unbacked=14
   unbacked loaders: app_permissions, apps, bluetooth, calendar, call_log,
                     clipboard, contacts, gps, installed_apps, notes,
                     notifications, sms_mms, usage_stats, wifi
custody: VERIFY: NO_MANIFEST: 0 ok, 0 mismatched, 0 missing;
         14 T1 claims hold no artifact
```

The 14 records predate the requirement, so they are correctly classified as
legacy `unbacked` rather than silently blessed or silently deleted.

### 3. Integrity was never exercised on real acquired output

The original wording of this finding was wrong, and the correction matters:
`record_acquired()` **does** write `manifests/SHA256SUMS`
(`evidence_store.py:385-396`), and the tests exercise it. What the machine
actually showed:

| Directory | What it is | `case.json` | `manifests/SHA256SUMS` |
|---|---|---|---|
| `acq_20261014_225348_13276374` (8.91 GB) | a pull target, not a case | no | no |
| `acq_20261015_005025_13276374` (9.27 GB) | a pull target, not a case | no | no |
| `acq_20261017_174920_13276374` (4.48 GB) | a pull target, not a case | no | no |
| `CASE-BASELINE-001` | a real case, deliberately empty | yes | no |

So the accurate statement is: **no case on this machine contains evidence that
went through `record_acquired()`**. The three large directories are adb pull
destinations with a stray `custody.jsonl`; they never opened a case or recorded
an artifact, so `verify()` returns `NO_MANIFEST` for them by design rather than
by failure.

**Status: Fixed (coverage, not behaviour).** Added
`test_verify_catches_a_single_flipped_byte`, which acquires a 4 KiB artifact
through `record_acquired()`, flips one bit in the middle, and asserts
`INTEGRITY_FAILURE` with exactly one mismatch. That is the end-to-end proof the
claim needed; it had been resting on the mechanism being tested rather than the
path being walked.

**Still open:** no *real* acquisition has been through `record_acquired()` on this
machine, so the claim remains demonstrated on synthetic evidence. The fix is to
route the acquisition UI through `record_acquired()` for every pulled artifact —
which the loaders already do, but the large adb pulls apparently did not.

### 4. Report formats match; the count is honest

`report_page.py` offers exactly four: HTML (recommended), PDF (court-ready), JSON
(machine readable) and the XLSX verification workbook. The workbook builder lives
in `report/evidence_workbook.py` and is unit-tested independently of the UI,
which is the right split.

**Status: Confirmed, no change needed.**

### 5. Imaging claims hold; the unvalidated container was removed

`imaging/` is standard-library only — no third-party binary, no `libewf`. RAW
splits into segments with `.md5`/`.sha1`/`.sha256` sidecars and `.imageinfo.txt`;
ZIP produces a `MANIFEST.json`. Source/format gating is deliberate and tested: a
block device can only be written as RAW, a folder only as ZIP.

The audit's one caveat was AFF4-L interoperability: its tests verified ZIP64
integrity, root-segment order and the volume URN, never that a reference reader
could open the container. Rather than keep shipping a format that could only be
described as "AFF4-L-shaped", the writer was **removed on 2026-10-20**, along
with its registry entry, format label, dialog gate, tests and documentation. The
module is now two writers, `raw.py` and `zip.py`, and the suite dropped from 170
tests to 165 with nothing left pointing at a missing module.

**Status: Confirmed for RAW and ZIP. AFF4-L removed, so the unproven claim is
gone rather than carried.**

---

## Verdict

The README is unusually accurate for a project of this size. Loaders, status
vocabulary, root gating, report formats, imaging formats and the reproducible
build all check out against source and tests.

What the audit changed:

1. The stale comment no longer asserts a state that belongs to a case.
2. A `T1_DEVICE_VERIFIED` claim can no longer be prose and nothing else.
3. `verify()` reports claim integrity alongside file integrity, and a one-byte
   mutation through the real acquisition path is now proven to be caught.

What remains honest to say: integrity verification is exercised on synthetic
cases, not on a real acquisition that went through `record_acquired()`. That is
stated in the README rather than papered over.
