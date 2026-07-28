"""Configure the private AWS S3 bucket for direct browser PUT uploads."""

import asyncio

from app.core.config import settings
from app.services.s3_storage_service import configure_browser_cors


async def main() -> None:
    origins = list(dict.fromkeys(settings.CORS_ORIGINS))
    await configure_browser_cors(origins)
    print(f"Configured direct-upload CORS for {len(origins)} origin(s)")


if __name__ == "__main__":
    asyncio.run(main())
