"""Initialize fresh local files without network access or model training."""
import json
from pathlib import Path
import shutil

import bot

ROOT = Path(__file__).resolve().parent


def main():
    config_path = ROOT / "config.json"
    if not config_path.exists():
        shutil.copyfile(ROOT / "config.example.json", config_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    connection = bot.connect(ROOT / "research.sqlite")
    try:
        bot.report(connection, config)
    finally:
        connection.close()
    print("Local configuration, database and report are ready. No collector was started.")


if __name__ == "__main__":
    main()
