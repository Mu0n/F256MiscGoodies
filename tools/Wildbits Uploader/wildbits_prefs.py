"""Tiny JSON-backed preferences store for the Wildbits uploader GUI."""

import json
from pathlib import Path

PREFS_PATH = Path.home() / ".wildbits_uploader_prefs.json"

def get_pgz2flash_dir():
    return get_pref("pgz2flash_dir", "")

def set_pgz2flash_dir(path):
    set_pref("pgz2flash_dir", path)
    

def load_prefs():
    try:
        with open(PREFS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_prefs(prefs):
    try:
        with open(PREFS_PATH, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
        return True
    except OSError:
        return False


def get_pref(key, default=None):
    return load_prefs().get(key, default)


def set_pref(key, value):
    prefs = load_prefs()
    prefs[key] = value
    save_prefs(prefs)
