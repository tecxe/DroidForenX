"""A defensible sample of images from an acquisition, for the PDF report.

The Artifact Viewer shows the examiner photographs and screenshots one at a
time, read live off the handset. A report cannot do that: it has to reproduce
some of them, on paper, with enough provenance that a reader can find the
original in the case store. This module chooses that sample and describes it.

The rules are deliberately boring, because a selection that cannot be
explained is a selection an advocate can attack:

  * real artifacts recorded against the session win, since they carry a hash
    the case already holds;
  * failing that, images already acquired into the store for that handset are
    used, camera and screenshots first, then application media, then the rest;
  * the sample is drawn evenly across the sorted list rather than taking the
    first N, so a plate set is not eighteen photographs of the same afternoon;
  * nothing is read from the device at report time. A report describes
    evidence that was acquired, not evidence that happens to be reachable now.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

#: Below this the file is an icon or a thumbnail, not something worth a plate.
MIN_BYTES = 12 * 1024

#: Scanning a store with tens of thousands of images should not stall the UI.
MAX_PER_FOLDER = 600

#: A quick acquisition only has to contribute a taste of its application media.
SMALL_BUDGET = 60

#: Directory names that hold the images a reader cares about, walked first.
_DIR_PRIORITY = {
    "camera": 0, "screenshots": 1, "dcim": 2, "pictures": 3,
    "deleted_files": 4, "social_apps": 5, "filesystem": 6,
    "logical": 7, "app_data": 8,
}

#: Categories in the order the artifact viewer presents them, which is also the
#: order a reader expects to find them in the appendix.
CATEGORY_ORDER = ("Camera", "Screenshots", "Application media", "Pictures", "Other")


def _category_of(relative: str) -> str:
    lowered = relative.lower().replace("\\", "/")
    if "/dcim/camera/" in lowered:
        return "Camera"
    if "screenshot" in lowered:
        return "Screenshots"
    if any(token in lowered for token in ("whatsapp", "telegram", "instagram", "snapchat",
                                          "com.", "android/media", "social_apps")):
        return "Application media"
    if "/pictures/" in lowered or "/dcim/" in lowered:
        return "Pictures"
    return "Other"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path, roots) -> str:
    for root in roots:
        try:
            return str(path.relative_to(root)).replace("\\", "/")
        except ValueError:
            continue
    return path.name


def _acquisition_dirs(serial: str | None) -> list[Path]:
    """Acquisition folders in the evidence store, newest first.

    The store names a folder ``acq_<timestamp>_<first eight of the serial>``,
    so a serial narrows the search without opening anything.
    """
    from desktop.core.evidence_store import EVIDENCE_ROOT

    root = Path(EVIDENCE_ROOT)
    if not root.is_dir():
        return []
    token = "".join(ch for ch in str(serial or ""))[:8]
    folders = [p for p in root.iterdir() if p.is_dir() and p.name.startswith("acq_")]
    if token:
        matching = [p for p in folders if p.name.endswith(token)]
        folders = matching or folders
    return sorted(folders, key=lambda p: p.name, reverse=True)


def _from_database(session_id: str | None, limit: int) -> list[dict]:
    if not session_id:
        return []
    try:
        from desktop.core.database import get_db

        conn = get_db()
        rows = conn.execute(
            "SELECT name, source, category, file_path, sha256_hash, file_size "
            "FROM evidence_artifacts WHERE session_id=? ORDER BY source",
            (session_id,),
        ).fetchall()
        conn.close()
    except Exception:
        return []

    found = []
    for row in rows:
        record = dict(row) if not isinstance(row, dict) else row
        local = record.get("file_path") or ""
        suffix = Path(str(record.get("source") or local)).suffix.lower()
        if suffix not in IMAGE_SUFFIXES or not local:
            continue
        path = Path(local)
        try:
            if not path.is_file() or path.stat().st_size < MIN_BYTES:
                continue
        except OSError:
            continue
        found.append({
            "path": path,
            "name": Path(str(record.get("name") or path.name)).name,
            "source": str(record.get("source") or path.name),
            "category": record.get("category") or "Other",
            "sha256": record.get("sha256_hash") or "",
            "size": int(record.get("file_size") or 0),
        })
        if len(found) >= limit:
            break
    return found


def _walk_images(folder: Path, budget: int, stop_camera: int = 0) -> list[dict]:
    """Images in one acquisition folder, camera and screenshots first.

    A handset acquisition is mostly application caches, and a blind recursive
    glob fills its budget with Telegram thumbnails before it ever reaches
    DCIM -- which is exactly the bug this ordering exists to prevent. Walking
    with the interesting directories first costs one pass and no surprises.
    """
    import os

    found: list[dict] = []
    camera = 0
    for root, dirnames, filenames in os.walk(folder):
        dirnames.sort(key=_priority)
        for name in filenames:
            path = Path(root) / name
            if path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size < MIN_BYTES:
                continue
            relative = _relative(path, [folder])
            category = _category_of(relative)
            found.append({
                "path": path,
                "name": name,
                "source": relative,
                "category": category,
                "sha256": "",
                "size": size,
            })
            if category == "Camera":
                camera += 1
                if stop_camera and camera >= stop_camera:
                    return found
            if len(found) >= budget:
                return found
    return found


def _priority(name: str) -> int:
    return _DIR_PRIORITY.get(name.lower(), 50)


def _from_store(serial: str | None, limit: int) -> list[dict]:
    """Images already acquired for this handset, across recent acquisitions.

    A single acquisition is often partial -- an operator may run a quick pass
    for application data and a fuller one the next day -- so the candidates are
    drawn from the newest few and merged by file name.

    Only the acquisitions that carry a filesystem tree can hold the camera
    roll, and walking every folder on a workstation with twenty of them costs
    seconds. The ones that can are read first and abandoned as soon as enough
    photographs have turned up; the rest only contribute a handful of images so
    the plate set is not all camera roll either.
    """
    folders = _acquisition_dirs(serial)
    if not folders:
        return []

    candidates: list[dict] = []
    seen: set[tuple[str, int]] = set()

    def add(items):
        for item in items:
            key = (item["name"], item["size"])
            if key in seen:
                continue
            seen.add(key)
            candidates.append(item)

    target_camera = max(4, limit // 3)
    for folder in folders:
        if not (folder / "filesystem").is_dir():
            continue
        add(_walk_images(folder, MAX_PER_FOLDER, stop_camera=target_camera))
        camera = sum(1 for item in candidates if item["category"] == "Camera")
        screenshots = sum(1 for item in candidates if item["category"] == "Screenshots")
        if camera >= target_camera and screenshots >= 2:
            break

    for folder in folders[:8]:
        if len(candidates) >= limit * 6:
            break
        if not (folder / "filesystem").is_dir():
            add(_walk_images(folder, SMALL_BUDGET))
    return _select(candidates, limit)


def acquisition_for(serial: str | None) -> Path | None:
    """Newest acquisition folder for this handset that actually holds files.

    The newest folder is not always the fullest -- an operator may abort a run
    seconds after it starts, leaving an empty shell -- so folders are checked
    newest-first and the first one with content wins.
    """
    for folder in _acquisition_dirs(serial):
        try:
            for _ in folder.rglob("*"):
                return folder
        except OSError:
            continue
    return None


def _select(candidates: list[dict], limit: int) -> list[dict]:
    """Round-robin across categories, evenly within each one.

    A camera roll of four hundred photographs and a dozen screenshots should
    not produce eighteen photographs: the appendix exists to show the kinds of
    image the handset held, not to pad with whichever folder is largest.
    """
    buckets: dict[str, list[dict]] = {}
    for item in candidates:
        buckets.setdefault(item["category"], []).append(item)
    for bucket in buckets.values():
        bucket.sort(key=lambda item: item["source"])

    orders = {category: _spread(bucket) for category, bucket in buckets.items()}
    picked: list[dict] = []
    while len(picked) < limit:
        added = False
        for category in CATEGORY_ORDER:
            queue = orders.get(category)
            if not queue:
                continue
            picked.append(queue.pop(0))
            added = True
            if len(picked) >= limit:
                break
        if not added:
            break
    return picked


def _spread(items: list[dict]) -> list[dict]:
    """Reorder a list so that taking its head still spans the whole set.

    A low-discrepancy walk (golden-ratio stride) rather than a sort: taking the
    first three of a thousand photographs should not mean the first three
    files in the folder.
    """
    total = len(items)
    if total <= 2:
        return list(items)
    ordered, seen = [], set()
    position = 0.0
    for _ in range(total):
        index = int(position * total) % total
        while index in seen:
            index = (index + 1) % total
        seen.add(index)
        ordered.append(items[index])
        position = (position + 0.6180339887498949) % 1.0
    return ordered


def collect(session_id: str | None = None, serial: str | None = None,
            limit: int = 18) -> list[dict]:
    """Return up to ``limit`` images with the provenance the report prints."""
    # Ask for a few spare: an image recovered both live and from the deleted
    # list is one picture, and the duplicate is dropped once the hashes are in.
    want = limit + 6
    images = _from_database(session_id, want)
    seen = {str(item["path"]) for item in images}
    for item in _from_store(serial, want):
        if str(item["path"]) in seen:
            continue
        seen.add(str(item["path"]))
        images.append(item)

    for item in images:
        if not item.get("sha256"):
            try:
                item["sha256"] = _sha256(item["path"])
            except OSError:
                item["sha256"] = ""
        if not item.get("size"):
            try:
                item["size"] = item["path"].stat().st_size
            except OSError:
                item["size"] = 0

    unique, digests = [], set()
    for item in images:
        digest = item.get("sha256")
        if digest:
            if digest in digests:
                continue
            digests.add(digest)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique
