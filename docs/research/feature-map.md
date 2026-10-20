# Feature map: README claims vs source and tests

Audited 2026-10-20 against `main` at `v2.0.15`. Every row was checked by reading
the source and, where possible, by running or inspecting something. "Confirmed"
means the code does what the README says; it does not mean the feature has been
proven on a device unless the row says so.

Test counts are measured, not quoted.

---

## Summary

| Claim (README) | Source | Tests | Verdict |
|---|---|---|---|
| Five forensic stages | `desktop/ui/theme.py:101` — `STAGE_NAMES = [Seizure, Identification, Acquisition, Examination, Reporting]` | `test_main_window.py` (7) | **Confirmed** |
| 24 acquisition loaders | `desktop/core/forensic_engine.py:901-926` — `ANALYSIS_MODULES`, **24 entries** | `test_forensic_engine.py` (8) | **Confirmed** |
| 10 root-gated loaders reported `NOT_ACCESSIBLE` | `forensic_engine.py:933-936` — `ROOT_REQUIRED_MODULES`, exactly **10** | `test_forensic_engine.py` | **Confirmed** |
| 14 of 24 device-verified | `CASE-BASELINE-001/manifests/verification.jsonl` — **14** `T1_DEVICE_VERIFIED` records, each with serial, build fingerprint, date and observed row count | — | **Confirmed as recorded**, but not re-derivable (see Finding 2) |
| Evidence preservation, hashing, custody, tamper detection | `evidence_store.py:204` `hash_file`, `:337` `record_acquired`, `:293` `log_custody`, `:479` `compute_integrity`, `:531` `verify` | `test_evidence_store.py` (**22**) | **Code confirmed**, end-to-end unproven (Finding 3) |
| RAW / ZIP / AFF4-L image output | `desktop/core/imaging/` — `raw.py`, `zip.py`, `aff4.py`, `registry.py`, `sources.py` | `test_imaging.py` (**43**), `test_create_image_dialog.py` (**24**), `test_image_registry.py` (8) | **Confirmed** |
| Four report formats | `desktop/ui/pages/report_page.py:80-84` — HTML, PDF, JSON, XLSX | `test_ui_export.py` (9), `test_evidence_workbook.py` (5) | **Confirmed** |
| Dark / Light / Follow System themes | `desktop/ui/theme.py` | `test_theme.py` (**27**) | **Confirmed** |
| Reproducible build | `build.py` — `SOURCE_DATE_EPOCH` from HEAD, `PYTHONHASHSEED=0`, drift check against `requirements-lock.txt` | `test_reproducible_build.py` (12) | **Confirmed by experiment** — two clean builds of `69de6a2` produced identical `ded26a43…` |
| Logical (`/sdcard`) imaging for unrooted devices | `imaging/sources.py` — tar over `adb exec-out` through the RAW writer, `source_type=logical` | `test_imaging.py` | **Confirmed** (new in v2.0.15) |
| Suite size | — | **165** test functions across 10 modules; 164 run + 1 opt-in skip | **Confirmed** |

Measured suite, per module:

```
test_imaging.py                 43    test_create_image_dialog.py    24
test_theme.py                   27    test_evidence_store.py         22
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

### 1. A source comment contradicts both the README and the case records

`forensic_engine.py:938-943` states:

> "A loader with no such record is T3_UNTESTED - which, today, is all of them.
> The old hardcoded table claimed T1_DEVICE_VERIFIED for fourteen loaders with no
> device ever connected; that was a fabricated claim and it is gone."

That comment is stale. `CASE-BASELINE-001` holds 14 `T1_DEVICE_VERIFIED`
records, written 2026-10-14, each carrying device serial, Android build
fingerprint, date and an observed count. The README's "14 of 24" is therefore
backed; the comment claiming "all of them" are untested is not, and it
misleads any reader who trusts source comments over manifests.

**Severity:** documentation defect. No behavioural impact.
**Fix:** delete the second sentence, or reword to describe the mechanism rather
than the current state, which changes with every case.

### 2. The 14 T1 claims cannot be independently re-derived

`CASE-BASELINE-001` contains only:

```
case.json
manifests/custody.jsonl
manifests/status.jsonl
manifests/verification.jsonl
```

There is **no `acquired/` directory and no artifact**. The verification records
assert outcomes such as "observed 1403 rows" for call log and "observed 983
rows" for SMS/MMS, but the data those counts were taken from is not in the case,
so a third party cannot confirm the numbers, re-parse the source, or check a
hash.

The evidence string is also free text: `record_verification()` rejects an empty
evidence field for `T1_DEVICE_VERIFIED` (`evidence_store.py:162-166`), but it
does not require a digest, an artifact id or a path. "Observed 1403 rows" passes.

**Severity:** integrity gap in the strongest claim the project makes.
**Fix:** make `T1_DEVICE_VERIFIED` require at least one artifact id or content
digest from the same case, and reject a record whose artifacts are absent.

### 3. No case on this machine has a `SHA256SUMS` manifest

Four cases exist under `~/.droidforenx/evidence/`:

| Case | Size | Files | `SHA256SUMS` |
|---|---|---|---|
| `acq_20261014_225348_13276374` | 8.91 GB | 17,547 | **none** |
| `acq_20261015_005025_13276374` | 9.27 GB | 23,369 | **none** |
| `acq_20261017_174920_13276374` | 4.48 GB | 399 | **none** |
| `CASE-BASELINE-001` | manifests only | 4 | **none** |

`compute_integrity()` and `verify()` are implemented and unit-tested against
synthetic trees, so the *mechanism* is real. But the tamper-detection claim has
never been exercised on acquired evidence, because no acquisition wrote the
manifest it verifies against.

Run against the real cases, `verify()` returns cleanly:

```
CASE-BASELINE-001             verdict=NO_MANIFEST checked=0 ok=0 mismatched=0 missing=0
acq_20261017_174920_13276374  verdict=NO_MANIFEST checked=0 ok=0 mismatched=0 missing=0
acq_20261014_225348_13276374  verdict=NO_MANIFEST checked=0 ok=0 mismatched=0 missing=0
```

That is the right failure mode — it refuses to claim verification rather than
reporting thousands of false mismatches — but it also means the integrity path
has never once completed on real acquired output. `NO_MANIFEST` is a
distinguishable state in the verdict vocabulary, which is worth keeping.

**Severity:** the gap between "tested" and "demonstrated".
**Fix:** have the acquisition path write `SHA256SUMS` at close, and add an
integration test that acquires to a temp case, mutates one byte, and asserts
`verify()` returns `INTEGRITY_FAILURE`.

### 4. Report formats match; the count is honest

`report_page.py:80-84` offers exactly four: HTML (recommended), PDF
(court-ready), JSON (machine readable) and the XLSX verification workbook. The
workbook builder lives in `report/evidence_workbook.py` and is unit-tested
independently of the UI, which is the right split.

### 5. Imaging claims hold, including the new logical path

`imaging/` is standard-library only — no third-party binary, no `libewf`. RAW
splits into segments with `.md5`/`.sha1`/`.sha256` sidecars and `.imageinfo.txt`;
ZIP produces a `MANIFEST.json`; AFF4 is a hand-written AFF4-L ZIP64 container.
Source/format gating is deliberate and tested: a block device can only be written
as RAW, a folder only as ZIP or AFF4.

The one claim that remains unproven is **AFF4 interoperability**: the tests
verify ZIP64 integrity, root-segment order and the volume URN, not that a
reference reader can open the container.

---

## Verdict

The README is unusually accurate for a project of this size. The loaders, status
vocabulary, root gating, report formats, imaging formats and reproducible build
all check out against source and tests.

Three things should change before the next release notes claim evidence
integrity:

1. Fix the stale comment in `forensic_engine.py`.
2. Make `T1_DEVICE_VERIFIED` require a digest or artifact reference.
3. Write `SHA256SUMS` during acquisition, and prove `verify()` catches a
   one-byte mutation on real acquired output.

Until (3), the honest wording is "integrity verification is implemented and
unit-tested" rather than "tamper detection is tested", which currently invites a
reader to assume it has been run against an acquisition.
