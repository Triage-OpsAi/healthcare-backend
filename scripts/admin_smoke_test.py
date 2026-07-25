"""Compatibility entry point for the rollback-isolated administration smoke test."""

import asyncio

from scripts.admin_write_smoke_test import main


if __name__ == "__main__":
    asyncio.run(main())
