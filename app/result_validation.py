"""Structural checks for reusable subtitle results, including legacy generated files."""
import math


def number(value, low=0):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= low
    except OverflowError:
        return False


def valid_result(data):
    if not isinstance(data, dict) or data.get("status", "complete") != "complete":
        return False
    video, cues = data.get("video"), data.get("cues")
    if not isinstance(video, dict) or not isinstance(video.get("id"), str) or not video["id"]:
        return False
    if not isinstance(video.get("title"), str) or not number(video.get("duration")):
        return False
    if not isinstance(cues, list) or len(cues) > 50000:
        return False
    previous = -1
    for cue in cues:
        if not isinstance(cue, dict) or not number(cue.get("s")) or not number(cue.get("e")) or cue["s"] < previous or cue["e"] <= cue["s"]:
            return False
        previous = cue["s"]
        text, notes = cue.get("text"), cue.get("notes")
        if not isinstance(text, str) or len(text) > 10000 or not isinstance(cue.get("zh", ""), str):
            return False
        if not isinstance(notes, list) or len(notes) > 200:
            return False
        end = 0
        for note in notes:
            if not isinstance(note, dict) or type(note.get("s")) is not int or type(note.get("e")) is not int:
                return False
            if not end <= note["s"] < note["e"] <= len(text):
                return False
            if note.get("kind") not in ("word", "phrase", "sense") or not number(note.get("lv"), 1):
                return False
            if any(not isinstance(note.get(k), str) or not note[k].strip() or len(note[k]) > 500 for k in ("key", "zh")):
                return False
            if note.get("offsets", "codepoint") != "codepoint":
                return False  # Persisted positions use Python code points, never browser indices.
            end = note["e"]
    return True
