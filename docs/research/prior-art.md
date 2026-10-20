# Prior art: Android acquisition and analysis tools

Reviewed 2026-10-20. Every claim below comes from the project's own repository,
documentation or vendor page, fetched that day. Where something could not be
verified it says so instead of guessing.

Search engines returned unusable results during this review (poisoned result
sets, and a search integration returning nothing), so all sources were fetched
directly by URL.

---

## 1. AndroidQF

| | |
|---|---|
| Project | `mvt-project/androidqf` — fork of `botherder/androidqf`, maintained by Amnesty International's Security Lab |
| Language | Go, single portable binary for Linux / Windows / macOS |
| Licence | **MVT License 1.1** (a custom licence — GitHub reports `NOASSERTION`, not an OSI identifier) |
| Activity | v1.8.3 released 2026-05-12; last push 2026-10-13; 338 stars |
| Device requirement | ADB authorisation only. Most modules work unrooted; a few require *existing* root |

### Acquisition model

Connects over ADB, prompts (or takes flags) for optional collections, and writes
each acquisition as a single archive. Designed to be run by non-technical users;
output is intended for later analysis in MVT.

### Supported artifacts

| Data | Output |
|---|---|
| Full backup, or SMS/MMS backup (optional) | `backup.ab` |
| Build/config properties (`getprop`) | `getprop.txt` |
| System settings | `settings_*.txt` |
| Running processes | `processes.json`, else `processes.txt` |
| System services | `services.txt` |
| System logs | `logs/`, `logcat.txt` |
| `dumpsys` diagnostics | `dumpsys.txt` |
| Installed packages + distribution files | `packages.json` |
| Installed APKs (optional) | `apks/*` |
| APEX inventory and containers | `apex/*` |
| Intrusion logs (optional, private data) | `intrusion_logs/*` |
| Magisk module metadata (root only) | `magisk_modules/*` |
| `/system/etc/init.d` scripts (root only) | `init_scripts/*` |
| File listing, optionally with on-device hashes | `files.json` |
| Temp folder copies | `tmp/*` |
| Bug report | `bugreport.zip` |
| Existing bugreports | `bugreports/` |

### Output format

One archive per acquisition, named from the acquisition UUID:

- `<UUID>.zip` normally
- `<UUID>.zip.age` when `key.txt` lists age recipients — the ZIP stream is
  encrypted on the fly, so no plaintext archive or directory is ever written

Archive-level entries beyond the module outputs:

| Entry | Purpose |
|---|---|
| `acquisition.json` | UUID, timestamps, version, device information, per-module outcomes |
| `adb_host_key.pub` | Public half of the ADB host key used, so the host identity is documented |
| `command.log` | Debug-level command/acquisition log |
| `hashes.csv` | SHA-256 integrity records for preceding plaintext entries |

### Hashing

- `hashes.csv` holds two fields per row: ZIP entry name and **lowercase SHA-256**
  of the **plaintext, uncompressed** entry content. It carries no record of itself.
- Optional on-device hashing (`-hash-files`) adds MD5, SHA-1, SHA-256 and SHA-512
  to `files.json` entries where the device supports it. This is off by default
  because it is expensive and can stall collection on some handsets.
- `collector.sha256` records the digest of the deployed collector binary, and can
  be compared against the optional on-device hash in `files.json`.

### Acquisition status handling

This is the strongest part of the tool's design and the most relevant to us.

- `acquisition.json` contains `module_results` — for every module that ran:
  status, start and completion timestamps, and any error.
- Status is one of **`completed`**, **`partial`** (some requested evidence could
  not be collected) or **`failed`**.
- A partial or failed module does **not** abort the archive: it is still
  finalised so the successfully collected evidence survives, but the process
  exits unsuccessfully and says plainly that modules are incomplete.
- Externally produced streams (backups, bug reports, device file pulls) are
  staged and validated *before* their ZIP entries are created, so a failed
  command never leaves an empty or truncated entry that looks valid in
  `hashes.csv`.
- Archive finalisation (writing the manifest, log and hash list, closing the ZIP
  and encryption stream) is itself part of the success condition. A failure
  there is reported as a failed acquisition and the normal success message is
  suppressed.
- Unattended mode (`-non-interactive`) fails before starting if a required flag
  is missing, and fails if multiple devices are attached without `-serial`.

### Relevance to DroidForenX

AndroidQF is acquisition-only and analysis-agnostic: it collects, hashes and
records status, then hands off to MVT. Its status vocabulary (`completed` /
`partial` / `failed`) is coarser than our eight-state engine, but its two
structural ideas are worth copying outright:

1. **Validate a stream before committing an entry**, so a failure can never
   masquerade as evidence.
2. **Finalisation is part of success** — if the manifest or hash list cannot be
   written, the acquisition did not succeed.

The MVT License 1.1 is not an OSI-approved licence, which matters if any code
were to be reused rather than the ideas.

---

## 2. OpenMF

| | |
|---|---|
| Project | `scorelab/OpenMF` — Sustainable Computing Research Group, University of Moratuwa |
| Licence | Apache-2.0 |
| Language | JavaScript (Electron/React frontend), Python (collector and converter), Flask backend |
| Activity | **Last commit on `master` 2022-09-05**; last push to any branch 2023-03-31; 186 stars, 101 forks, **59 open issues**, not archived |
| Device requirement | **Rooted Android only** — every collection path uses `su` |

### Implementation

Three pieces: a Python CLI collector, a Python converter (databases → JSON/TSV),
and a Flask + React web UI for browsing the session. The collector shells out
through `su`, `dd`s each target database to `/data/local/tmp`, `adb pull`s it,
then removes it from the device.

### Supported artifacts

Hard-coded database paths rather than discovery:

- call log (`calllog.db`), dialer, contacts (`contacts2.db`)
- SMS/MMS (`mmssms.db`)
- settings, lock settings
- WhatsApp (`wa.db`, `msgstore.db`), Facebook, Kik
- browser history, Firefox
- Bluetooth, Google Maps, media on external storage

Collector options: `all`, `general_info`, `phone`, `whatsapp`, `facebook`,
`message`, `browser`, `bluetooth`, `location`, `media`.

### Outputs

`data/<session>/` containing the pulled `.db` files, then converted to JSON/TSV
for the UI, plus a generated PDF report. No container format, no image.

### Hashing

**MD5 only.** `scripts/dbm.py` and `scripts/extract_all.py` write a
tab-separated `md5sums` / `md5sums_all` file (`digest<TAB>filename`) after each
pull. There is no SHA-256, no signature, and no hash recorded for the session as
a whole.

### Acquisition status handling

**None.** There is no manifest, no per-module status, no failure accounting and
no exit-code semantics for partially successful runs. A database that could not
be pulled is simply absent, which is indistinguishable from a database the
device did not have.

### Verdict

Effectively abandoned (four years without a commit), root-only, MD5-only, and
with no evidence-integrity model. It is useful as a catalogue of which Android
database paths matter, and as an illustration of the anti-pattern our
`NOT_ACCESSIBLE` / `NOT_PRESENT` distinction exists to prevent: silence
presented as a result.

---

## 3. Autopsy

| | |
|---|---|
| Project | `sleuthkit/autopsy` (Basis Technology / Sleuth Kit Labs) |
| Licence | Apache-2.0 (confirmed from the repository licence file and module headers) |
| Version | **4.23.1**, released 2026-05-07; 3.4k stars, 405 open issues, actively maintained |
| Role | **Analysis**, not acquisition — it ingests a data source that has already been acquired |

### The Android Analyzer module

Located at `InternalPythonModules/android/`. It is a Python
`DataSourceIngestModule`, i.e. it runs once per data source (a filesystem image
or a directory of extracted files) and reports findings to the blackboard.

`module.py` instantiates **22 analyzers**, each parsing one app or system
database and emitting its own artifact types:

| Group | Analyzers |
|---|---|
| System / comms | `contact`, `calllog`, `textmessage`, `installedapps` |
| Messaging apps | `whatsapp`, `viber`, `skype`, `line`, `fbmessenger`, `imo`, `textnow`, `tangomessage`, `wwfmessage` |
| Location | `googlemaplocation`, `browserlocation`, `cachelocation`, `oruxmaps` |
| Browsers | `sbrowser` (Samsung), `operabrowser` |
| File-transfer apps | `shareit`, `xender`, `zapya` |

Supporting `Tsk*Parser.py` helpers convert rows into Autopsy's communications
and message artifact types, so call logs, contacts and messages surface in the
standard views rather than as raw files.

### Behaviour worth noting

- Analyzers are located by path via `FileManager`; a file that is not present is
  skipped silently.
- Each analyzer runs inside its own `try/except`; failures are accumulated into
  an ingest *message* ("N errors found") while the module as a whole still
  returns `ProcessResult.OK`. Per-analyzer failure is therefore advisory, not
  fatal — the opposite of the status semantics we want.
- It never assigns provenance or verification tiers to a loader; there is no
  equivalent of `verification.jsonl`.

### Overlap with our scope

Shared: call log, SMS/MMS, contacts, WhatsApp, browser history, location,
installed applications.

Not covered by Autopsy's Android module: **Telegram**, Instagram, Snapchat,
TikTok, financial transactions, WiFi passwords, clipboard, notification
history, usage statistics, deleted-data recovery, and MD5/SHA hash-set
correlation of acquired artifacts.

### Relevance to DroidForenX

Autopsy demonstrates the split we already make: acquisition and analysis are
different problems with different evidence obligations. Two things are worth
borrowing:

1. **Reporting parsed rows through a shared artifact schema** rather than
   ad-hoc per-loader output — Autopsy's communications artifacts make call logs
   and messages directly comparable.
2. Its silent-skip behaviour is the failure mode we deliberately avoid: a
   missing database and an inaccessible database look identical there, which is
   precisely what `NOT_PRESENT` versus `NOT_ACCESSIBLE` exists to separate.

---

## 4. Commercial alternatives

Licensing for these vendors is by quotation; none publishes list prices.
Academic access is published by some, not by others.

| Vendor / product | Published licensing | Academic access | Source |
|---|---|---|---|
| **Magnet Forensics** — Axiom, Axiom Cyber, Griffeye | Commercial, quote only | **Magnet Academic License Program.** Axiom/Axiom Cyber Academic Package: **30-seat** network or cloud licence for class/lab use, one instructor dongle for course development, lecture-case evidence (iPhone and Samsung Android images, RAM/process captures, cloud extractions, drone), pre-populated question pools (content package, QTI, Markup), curriculum manuals, one AX200 training voucher, and eligibility to sit **MCFE**. Griffeye Academic Package: 30-seat lab licence plus **60-seat** cloud licence for homework. Eligibility: 501(c)(3) or equivalent, accredited 2- or 4-year degree-granting institution with a board of trustees, and **explicitly not** part of a military, police or government entity. | magnetforensics.com/academic-license |
| **Belkasoft** — Belkasoft X | Commercial, quote only (public offer / terms on site) | **Academic licence at up to 98% off** the commercial price; the vendor states 1,100+ universities and colleges use its tools. Also: free self-paced course access for classes, research awards worth up to **$6,500** in product, and CTF exercises available for course use. | belkasoft.com/academic |
| **Cellebrite** — Inseyets UFED, Physical Analyzer, Genesis, Pathfinder | Commercial, quote only | **Cellebrite Academics** — academic partnership for students, educators and institutions: structured curriculum, hands-on learning and certification pathways, "updated for the Fall 2026 academic year". | cellebrite.com/training/academics |
| **MSAB** — XRY | Commercial, quote only | **No academic licensing programme found.** "MSAB Academy" on the vendor site refers to training webinars, not academic licences. | msab.com |
| **Oxygen Forensics** — Oxygen Forensic Detective | Commercial, quote only | **No academic wording on the public homepage**; no programme verified. | oxygenforensics.com |
| **OpenText** — EnCase Forensic | Commercial, quote only | **Not verified** — the product page could not be retrieved (bot-blocked, 100-byte response). | opentext.com |

### Reading of this table

The pattern is consistent: acquisition/analysis suites are sold per seat on
quotation, and the ones that court universities do it through a *bundle*
(seats + curriculum + evidence + certification eligibility) rather than a
discount on a single licence. Magnet's eligibility clause is the most explicit —
academic pricing is unavailable to police and military academies, so a
public-sector training institution should expect commercial terms.

For an open-source tool the practical consequence is that **no published
licence permits redistributing vendor decoders or artifact definitions**, so
parsers have to be written from the database schemas themselves.

---

## 5. What this means for DroidForenX

| Question | Answer from the prior art |
|---|---|
| Is our acquisition status model unusual? | No — it is stricter than AndroidQF's three states and far stricter than OpenMF or Autopsy, which have none |
| Is our hashing adequate? | Yes, and stronger than OpenMF (MD5) and aligned with AndroidQF (SHA-256 over plaintext content) |
| Do we need a container format? | AndroidQF ships a ZIP; OpenMF ships loose files; Autopsy ingests images. Our ZIP output matches the AndroidQF end, and our RAW writer matches the image end |
| Where are we behind? | Autopsy's shared artifact schema for parsed rows, and its breadth of app coverage (LINE, Viber, Skype, Signal-adjacent apps) |
| Where are we ahead? | Per-loader status, verification tiers with mandatory evidence, custody log, tamper detection, gateway-free single binary |

The concrete gaps worth closing, in order:

1. Telegram and Signal-style app coverage (Autopsy has Telegram absent too, so
   this is a differentiator rather than catch-up).
2. A shared parsed-artifact schema so call logs, messages and contacts are
   comparable across loaders.
3. Publishing a `SHA256SUMS` manifest in every case, so integrity verification
   is demonstrable rather than theoretical.
