"""Text adaptation of the brand mark for interactive terminals."""
import os
import sys
import unicodedata

from . import __version__


def display_width(text):
    return sum(2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
               for char in text)


def show_banner():
    stream = sys.stdout
    if not stream.isatty():
        return
    if os.environ.get("TERM") == "dumb":
        print(f"YANTRA v{__version__}", file=stream)
        return
    mark = [
        "           ▄▄▄▄▄▄▄▄▄",
        "        ▄██▀▀▀      ▀▀",
        "      ▄██▀",
        "    ▄███                 █▀",
        "    ███                     ▄",
        "   ███                     ▀▀",
        "   ███",
        "   ███                      ⬤",
        "   ███                     ▄▄",
        "    ███                     ▀",
        "    ▀███                 █▄",
        "      ▀██▄",
        "        ▀██▄▄▄      ▄▄",
        "           ▀▀▀▀▀▀▀▀▀",
    ]
    # Four terminal rows form a seven-pixel-high uppercase wordmark.
    labels = {
        5: "█   █ ▄▀▀▀▄ █▄  █ ▀▀█▀▀ █▀▀▀▄ ▄▀▀▀▄",
        6: " ▀▄▀  █▄▄▄█ █▀▄ █   █   █▄▄▄▀ █▄▄▄█",
        7: "  █   █   █ █  ██   █   █ ▀▄  █   █",
        8: "  ▀   ▀   ▀ ▀   ▀   ▀   ▀   ▀ ▀   ▀",
        10: f"v{__version__}",
    }
    column_width = max(display_width(label) for label in labels.values())
    for index, row in enumerate(mark):
        padding = " " * (30 - display_width(row))
        label = labels.get(index, "")
        centered = " " * ((column_width - display_width(label)) // 2) + label
        if "NO_COLOR" not in os.environ:
            row = row.replace("⬤", "\033[38;2;225;43;34m⬤\033[0m")
        print(row + padding + "  " + centered, file=stream)
    print(file=stream)
