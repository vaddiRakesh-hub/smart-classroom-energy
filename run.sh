#!/usr/bin/env bash
# One-command start for Linux / macOS
cd "$(dirname "$0")/backend" || exit 1
python3 -m pip install -r ../requirements.txt -q
python3 app.py
