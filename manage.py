#!/usr/bin/env python
"""DroidForenX — command-line entry point for forensic operations."""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def main():
    parser = argparse.ArgumentParser(
        prog="droidforenx",
        description="DroidForenX Professional Digital Forensics Platform v2.0.17",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("gui", help="Launch the desktop GUI application")
    sub.add_parser("version", help="Show version information")

    args = parser.parse_args()

    if args.command == "gui":
        from desktop.main import main as gui_main
        gui_main()
    elif args.command == "version":
        print("DroidForenX v2.0.17 — Professional Digital Forensics Platform")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
