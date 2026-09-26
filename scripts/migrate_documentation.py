"""Additive, idempotent migration: python -m scripts.migrate_documentation."""
import asyncio
from app.db import models, documentation
from app.db.database import engine, Base

async def main():
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: Base.metadata.create_all(conn,tables=[documentation.WardVoiceDocument.__table__,documentation.WardVoiceDocumentRevision.__table__]))
    await engine.dispose()
    print("WARDVOICE documentation tables verified (2). Existing clinical records unchanged.")

if __name__=="__main__": asyncio.run(main())
