"""Check GitHub Releases for a newer DroidForenX installer."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.request import Request, urlopen
from urllib.parse import urlsplit


RELEASES_API_URL = "https://api.github.com/repos/tecxe/DroidForenX/releases/latest"
_VERSION_RE = re.compile(r"v?(\d+(?:\.\d+){1,3})\Z", re.IGNORECASE)
_INSTALLER_PATH = "/tecxe/DroidForenX/releases/download/"
_RELEASE_PATH = "/tecxe/DroidForenX/releases/tag/"


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    title: str
    notes: str
    release_url: str
    installer_url: str


def _version_parts(version: str) -> tuple[int, ...]:
    match = _VERSION_RE.fullmatch((version or "").strip())
    if not match:
        raise ValueError(f"Invalid DroidForenX release version: {version!r}")
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer_version(candidate: str, current: str) -> bool:
    """Compare numeric release versions, treating omitted trailing zeroes equally."""
    candidate_parts = _version_parts(candidate)
    current_parts = _version_parts(current)
    width = max(len(candidate_parts), len(current_parts))
    candidate_parts += (0,) * (width - len(candidate_parts))
    current_parts += (0,) * (width - len(current_parts))
    return candidate_parts > current_parts


def _github_release_url(url: str, path_prefix: str) -> bool:
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and parsed.hostname == "github.com"
            and parsed.port in (None, 443)
            and parsed.path.startswith(path_prefix) and not parsed.username
            and not parsed.password)


def parse_latest_release(payload: dict, current_version: str) -> UpdateInfo | None:
    """Return an installable update only for a newer release with a trusted asset."""
    tag = payload.get("tag_name", "")
    if not is_newer_version(tag, current_version):
        return None

    release_url = payload.get("html_url", "")
    if not _github_release_url(release_url, _RELEASE_PATH):
        raise ValueError("GitHub returned an unexpected release page URL")

    installer = next((asset for asset in payload.get("assets", [])
                      if asset.get("name") == "DroidForenX_Installer.exe"
                      and asset.get("state", "uploaded") == "uploaded"), None)
    if installer is None:
        raise ValueError("The latest release does not include the Windows installer")
    installer_url = installer.get("browser_download_url", "")
    if not _github_release_url(installer_url, _INSTALLER_PATH):
        raise ValueError("GitHub returned an unexpected installer URL")
    if urlsplit(installer_url).path.rsplit("/", 1)[-1] != "DroidForenX_Installer.exe":
        raise ValueError("GitHub returned an unexpected installer asset")

    version = ".".join(str(part) for part in _version_parts(tag))
    return UpdateInfo(
        version=version,
        title=payload.get("name") or f"DroidForenX v{version}",
        notes=(payload.get("body") or "").strip()[:6000],
        release_url=release_url,
        installer_url=installer_url,
    )


def fetch_latest_release(current_version: str) -> UpdateInfo | None:
    """Fetch GitHub's latest published release using the standard HTTPS API."""
    request = Request(
        RELEASES_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "DroidForenX-Update-Checker",
        },
    )
    with urlopen(request, timeout=8) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("GitHub release response exceeded the expected size")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub returned an invalid release response")
    return parse_latest_release(payload, current_version)
