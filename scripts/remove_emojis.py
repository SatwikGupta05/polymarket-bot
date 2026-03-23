import os

# Emoji → safe text replacements
EMOJI_MAP = {
    "[OK]": "[OK]",
    "[FAIL]": "[FAIL]",
    "[TARGET]": "[TARGET]",
    "[MONEY]": "[MONEY]",
    "[SHIELD]": "[SHIELD]",
    "[SHIELD]": "[SHIELD]",
    "[STATS]": "[STATS]",
    "[WARN]": "[WARN]",
    "[WARN]": "[WARN]",
    "[ALERT]": "[ALERT]",
    "[RED]": "[RED]",
    "[GREEN]": "[GREEN]",
    "[YELLOW]": "[YELLOW]",
    "[START]": "[START]",
    "[UP]": "[UP]",
    "[DOWN]": "[DOWN]",
    "[END]": "[END]",
    "[SEARCH]": "[SEARCH]",
    "[DIAMOND]": "[DIAMOND]",
    "[SKIP]": "[SKIP]",
    "[SKIP]": "[SKIP]",
    "[CTRL]": "[CTRL]",
    "[CTRL]": "[CTRL]",
    "[PIN]": "[PIN]",
    "[STOP]": "[STOP]",
    "[INFO]": "[INFO]",
    "[HOT]": "[HOT]",
    "[FAST]": "[FAST]",
    "[WAVE]": "[WAVE]",
    "[WIN]": "[WIN]",
    "[TROPHY]": "[TROPHY]",
    "[LOG]": "[LOG]",
    "[FIX]": "[FIX]",
    "[BUILD]": "[BUILD]",
    "[PKG]": "[PKG]",
    "[DB]": "[DB]",
    "[DB]": "[DB]",
    "[PC]": "[PC]",
    "[PC]": "[PC]",
    "[PC]": "[PC]",
    "[STAR]": "[STAR]",
    "[STAR]": "[STAR]",
    "[!]": "[!]",
    "[?]": "[?]",
    "->": "->",
    "->": "->",
    "[UP]": "[UP]",
    "[DOWN]": "[DOWN]",
    "[BACK]": "[BACK]",
    "[REFRESH]": "[REFRESH]",
    "[OK]": "[OK]",
    "[OK]": "[OK]",
    "[X]": "[X]",
    "[X]": "[X]",
    "[INFO]": "[INFO]",
    "[INFO]": "[INFO]",
    "[FREE]": "[FREE]",
    "[NEW]": "[NEW]",
    "[KEY]": "[KEY]",
    "[LOCK]": "[LOCK]",
    "[UNLOCK]": "[UNLOCK]",
    "[SIGNAL]": "[SIGNAL]",
    "[WEB]": "[WEB]",
    "[MSG]": "[MSG]",
    "[ANNOUNCE]": "[ANNOUNCE]",
    "[BELL]": "[BELL]",
    "[TIMER]": "[TIMER]",
    "[TIMER]": "[TIMER]",
    "[TIMER]": "[TIMER]",
    "[TIME]": "[TIME]",
    "[DATE]": "[DATE]",
    "[LIST]": "[LIST]",
    "[FOLDER]": "[FOLDER]",
    "[FOLDER]": "[FOLDER]",
    "[SAVE]": "[SAVE]",
    "[DELETE]": "[DELETE]",
    "[DELETE]": "[DELETE]",
    "[SETTINGS]": "[SETTINGS]",
    "[SETTINGS]": "[SETTINGS]",
    "[LINK]": "[LINK]",
    "[ATTACH]": "[ATTACH]",
    "[EDIT]": "[EDIT]",
    "[EDIT]": "[EDIT]",
    "[EDIT]": "[EDIT]",
    "[SEND]": "[SEND]",
    "[RECV]": "[RECV]",
    "[SYNC]": "[SYNC]",
    "[RECYCLE]": "[RECYCLE]",
    "[RECYCLE]": "[RECYCLE]",
    "[BLOCK]": "[BLOCK]",
    "[STOP]": "[STOP]",
    "[RESTRICTED]": "[RESTRICTED]",
    "[100%]": "[100%]",
    "[RANDOM]": "[RANDOM]",
    "[CARD]": "[CARD]",
    "[SLOT]": "[SLOT]",
    "[RADIO]": "[RADIO]",
    "[SOUND]": "[SOUND]",
    "[MUTE]": "[MUTE]",
    "[SLEEP]": "[SLEEP]",
    "[HEART]": "[HEART]",
    "[HEART]": "[HEART]",
    "[BRAIN]": "[BRAIN]",
    "[EYES]": "[EYES]",
    "[WAVE]": "[WAVE]",
    "[GOOD]": "[GOOD]",
    "[BAD]": "[BAD]",
    "[BOT]": "[BOT]",
    "[BANK]": "[BANK]",
    "[USD]": "[USD]",
    "[MONEY]": "[MONEY]",
    "[NEWS]": "[NEWS]",
    "[NEWS]": "[NEWS]",
    "[NEWS]": "[NEWS]",
    "[DOC]": "[DOC]",
    "[BOOK]": "[BOOK]",
    "[RESEARCH]": "[RESEARCH]",
    "[TEST]": "[TEST]",
    "[CALC]": "[CALC]",
    "[MEASURE]": "[MEASURE]",
    "[RULER]": "[RULER]",
    "[LABEL]": "[LABEL]",
    "[LABEL]": "[LABEL]",
    "[BOOKMARK]": "[BOOKMARK]",
    "[WORLD]": "[WORLD]",
    "[WORLD]": "[WORLD]",
    "[WORLD]": "[WORLD]",
    "[US]": "[US]",
    "[FLAG]": "[FLAG]",
}


def clean_file(filepath: str) -> bool:
    """Replace emojis in a file. Returns True if modified."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        print(f"Could not read {filepath}: {e}")
        return False

    original = content

    # Replace emojis only (safe)
    for emoji, replacement in EMOJI_MAP.items():
        content = content.replace(emoji, replacement)

    if content != original:
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception as e:
            print(f"Could not write {filepath}: {e}")

    return False


def process_directory(directory: str):
    total = 0
    changed = 0

    for root, dirs, files in os.walk(directory):
        # Skip unnecessary dirs
        dirs[:] = [
            d for d in dirs
            if d not in ("venv", ".venv", ".git", "__pycache__", "node_modules")
        ]

        for fname in files:
            if not fname.endswith(".py"):
                continue

            filepath = os.path.join(root, fname)
            total += 1

            if clean_file(filepath):
                changed += 1
                print(f"Cleaned: {filepath}")

    print(f"\nProcessed {total} files | Cleaned {changed} files")


if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"Cleaning emojis in: {base_dir}\n")
    process_directory(base_dir)
    print("\nDone. Emoji issues eliminated safely.")