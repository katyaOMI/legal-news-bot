"""Start a legal-news service in Coolify or locally."""
import importlib
import os
import sys

BLOCKS = {"collector": "collector.main", "worker": "worker.main", "bot": "bot.main"}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BLOCK") or os.environ.get("RAILWAY_SERVICE_NAME", "")
    if name not in BLOCKS:
        sys.exit("Choose a service: collector, worker, bot")
    sys.exit(importlib.import_module(BLOCKS[name]).main())
