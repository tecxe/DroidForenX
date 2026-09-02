"""Cryptographic Hashing — SHA-256, MD5, and file integrity verification."""

import hashlib
from pathlib import Path


def compute_sha256(file_path: str) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def compute_md5(file_path: str) -> str:
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5.update(chunk)
    return md5.hexdigest()


def compute_sha1(file_path: str) -> str:
    sha1 = hashlib.sha1()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha1.update(chunk)
    return sha1.hexdigest()


def verify_file_integrity(file_path: str, expected_hash: str,
                          algorithm: str = "sha256") -> bool:
    algorithms = {"sha256": compute_sha256, "md5": compute_md5, "sha1": compute_sha1}
    if algorithm not in algorithms:
        raise ValueError(f"Unsupported algorithm: {algorithm}")
    actual = algorithms[algorithm](file_path)
    return actual.lower() == expected_hash.lower()


def hash_directory(directory: str, algorithm: str = "sha256") -> dict:
    results = {}
    for fp in Path(directory).rglob("*"):
        if fp.is_file():
            try:
                results[str(fp)] = compute_sha256(str(fp)) if algorithm == "sha256" else compute_md5(str(fp))
            except Exception:
                results[str(fp)] = "error"
    return results
