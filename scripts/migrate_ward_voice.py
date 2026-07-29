"""Create the isolated Ward Voice tables without changing existing tables."""
import asyncio

from app.db.database import engine
from app.db.ward_voice_models import (
    Bed, ChartClosure, ExtractedObservation, FluidEntry, IVInfusion, VoiceCapture,
    Ward, WardTask, WardVoiceAuditEvent,
)

TABLES = [
    Ward.__table__, Bed.__table__, WardTask.__table__, VoiceCapture.__table__,
    ExtractedObservation.__table__, FluidEntry.__table__, IVInfusion.__table__,
    ChartClosure.__table__, WardVoiceAuditEvent.__table__,
]


async def main() -> None:
    async with engine.begin() as connection:
        for table in TABLES:
            await connection.run_sync(lambda sync_connection, item=table: item.create(sync_connection, checkfirst=True))
    await engine.dispose()
    print("Ward Voice schema is ready.")


if __name__ == "__main__":
    asyncio.run(main())
