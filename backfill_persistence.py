from __future__ import annotations

import json

from app import CURRENT_FILE, fetch_sector_persistence, load_local_env


def main() -> None:
    load_local_env()
    persistence = fetch_sector_persistence(10, force=True)
    current = json.loads(CURRENT_FILE.read_text(encoding="utf-8"))
    current["sector_persistence"] = persistence["rows"]
    current["sector_persistence_meta"] = {
        key: persistence.get(key)
        for key in ("source", "updated_at", "universe_count", "failure_count")
    }
    CURRENT_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Backfilled {len(persistence['rows'])} trading days from "
        f"{persistence['universe_count']} sectors; failures={persistence['failure_count']}"
    )


if __name__ == "__main__":
    main()
