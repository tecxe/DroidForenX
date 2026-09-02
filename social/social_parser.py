"""Social Media Extraction — WhatsApp, Telegram, Instagram, Twitter cache parsing."""

import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


SOCIAL_PACKAGES = {
    "com.whatsapp": "WhatsApp",
    "org.telegram.messenger": "Telegram",
    "com.instagram.android": "Instagram",
    "com.twitter.android": "Twitter/X",
    "com.facebook.katana": "Facebook",
    "com.snapchat.android": "Snapchat",
    "com.zhiliaoapp.musically": "TikTok",
}


class SocialParser:
    """Extracts and parses social media app cache and data files."""

    def __init__(self, adb_path: str = "adb", serial: str = ""):
        self.adb_path = adb_path
        self.serial = serial

    def list_social_files(self, package: str, max_files: int = 5000) -> list:
        try:
            result = subprocess.run(
                [self.adb_path, "-s", self.serial, "shell",
                 "find", f"/sdcard/Android/data/{package}/", "-type", "f"],
                capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            files = []
            for line in result.stdout.splitlines():
                line = line.strip()
                if line and "Permission denied" not in line:
                    fname = Path(line).name
                    ext = Path(line).suffix.lower()
                    files.append({"filename": fname, "path": line, "extension": ext})
                    if len(files) >= max_files:
                        break
            return files
        except Exception as e:
            logger.warning(f"Social file listing failed for {package}: {e}")
            return []

    def get_file_type(self, ext: str) -> str:
        image_exts = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}
        video_exts = {'.mp4', '.3gp', '.mkv', '.avi'}
        audio_exts = {'.opus', '.aac', '.mp3', '.m4a', '.wav'}
        doc_exts = {'.pdf', '.doc', '.docx', '.txt'}
        if ext in image_exts: return "Image"
        if ext in video_exts: return "Video"
        if ext in audio_exts: return "Audio"
        if ext in doc_exts: return "Document"
        return "Cache File"

    def get_all_social_files(self) -> dict:
        results = {}
        for package, name in SOCIAL_PACKAGES.items():
            files = self.list_social_files(package)
            results[name] = files
        return results
