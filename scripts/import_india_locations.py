"""Idempotently import India's state/district/city master from India Post OGD data."""

import argparse
import asyncio
import csv
import io
import re
import urllib.request
import uuid
from collections import defaultdict
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.db.database import AsyncSessionLocal, engine
from app.db.models import City, District, State

# Source page: https://www.data.gov.in/resource/all-india-pincode-directory-till-last-month
OFFICIAL_PINCODE_CSV = (
    "https://www.data.gov.in/files/ogdpv2dms/s3fs-public/"
    "dataurl03122020/pincode.csv"
)

STATE_CODES = {
    "Andaman And Nicobar Islands": "AN", "Andhra Pradesh": "AP",
    "Arunachal Pradesh": "AR", "Assam": "AS", "Bihar": "BR",
    "Chandigarh": "CH", "Chhattisgarh": "CG",
    "Dadra And Nagar Haveli And Daman And Diu": "DN", "Delhi": "DL",
    "Goa": "GA", "Gujarat": "GJ", "Haryana": "HR",
    "Himachal Pradesh": "HP", "Jammu And Kashmir": "JK", "Jharkhand": "JH",
    "Karnataka": "KA", "Kerala": "KL", "Ladakh": "LA", "Lakshadweep": "LD",
    "Madhya Pradesh": "MP", "Maharashtra": "MH", "Manipur": "MN",
    "Meghalaya": "ML", "Mizoram": "MZ", "Nagaland": "NL", "Odisha": "OD",
    "Puducherry": "PY", "Punjab": "PB", "Rajasthan": "RJ",
    "Sikkim": "SK",
    "Tamil Nadu": "TN", "Telangana": "TS", "Tripura": "TR",
    "Uttar Pradesh": "UP", "Uttarakhand": "UK", "West Bengal": "WB",
}
STATE_ALIASES = {
    "Andaman & Nicobar Islands": "Andaman And Nicobar Islands",
    "Dadra & Nagar Haveli": "Dadra And Nagar Haveli And Daman And Diu",
    "Daman & Diu": "Dadra And Nagar Haveli And Daman And Diu",
    "Chattisgarh": "Chhattisgarh",
    "Jammu & Kashmir": "Jammu And Kashmir",
    "Orissa": "Odisha",
    "Pondicherry": "Puducherry",
    "The Dadra And Nagar Haveli And Daman And Diu": "Dadra And Nagar Haveli And Daman And Diu",
}


def clean_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).title()


def clean_state(value: str) -> str:
    name = clean_name(value)
    return STATE_ALIASES.get(name, name)


def clean_city(value: str) -> str:
    name = clean_name(value)
    return re.sub(r"\s+(B|S|H)\.?\s*O\.?$", "", name, flags=re.IGNORECASE).strip()


def read_source(path: Path | None) -> str:
    if path is not None:
        return path.read_text(encoding="utf-8-sig")
    request = urllib.request.Request(
        OFFICIAL_PINCODE_CSV,
        headers={"User-Agent": "MeridianHealthAI-LocationImporter/1.0"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8-sig")


def parse_hierarchy(csv_text: str) -> dict[str, dict[str, set[str]]]:
    hierarchy: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    reader = csv.DictReader(io.StringIO(csv_text))
    add_rows(hierarchy, reader)
    return hierarchy


def add_rows(hierarchy, reader: csv.DictReader) -> None:
    fields = {name.lower().strip(): name for name in (reader.fieldnames or [])}
    district_field = "district" if "district" in fields else "districtname"
    required = {"statename", "officename", district_field}
    if not required.issubset(fields):
        raise RuntimeError(f"CSV must contain {sorted(required)}; found {reader.fieldnames}")
    for row in reader:
        state = clean_state(row.get(fields["statename"], ""))
        district = clean_name(row.get(fields[district_field], ""))
        city = clean_city(row.get(fields["officename"], ""))
        if state and district and city and state not in {"Na", "N/A"}:
            hierarchy[state][district].add(city)


def parse_directory(path: Path) -> dict[str, dict[str, set[str]]]:
    hierarchy: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    files = sorted(path.glob("*.csv"))
    if not files:
        raise RuntimeError(f"No CSV files found in {path}")
    for file_path in files:
        with file_path.open(encoding="utf-8-sig", newline="") as handle:
            add_rows(hierarchy, csv.DictReader(handle))
    return hierarchy


async def chunked_insert(session, statement, rows: list[dict], size: int = 2000) -> None:
    for start in range(0, len(rows), size):
        await session.execute(statement.values(rows[start:start + size]))


async def import_hierarchy(hierarchy: dict[str, dict[str, set[str]]]) -> None:
    async with AsyncSessionLocal() as session:
        state_rows = [
            {
                "id": uuid.uuid4(),
                "name": name,
                "code": STATE_CODES.get(name, f"X{index:02d}"),
                "is_active": True,
            }
            for index, name in enumerate(sorted(hierarchy), start=1)
        ]
        await chunked_insert(
            session,
            insert(State).on_conflict_do_update(
                index_elements=[State.name], set_={"is_active": True}
            ),
            state_rows,
        )
        await session.flush()
        state_ids = dict((await session.execute(select(State.name, State.id))).all())

        district_rows = [
            {
                "id": uuid.uuid4(), "state_id": state_ids[state],
                "name": district, "is_active": True,
            }
            for state, districts in hierarchy.items() for district in districts
        ]
        await chunked_insert(
            session,
            insert(District).on_conflict_do_update(
                constraint="uq_district_per_state", set_={"is_active": True}
            ),
            district_rows,
        )
        await session.flush()
        district_ids = {
            (state_id, name): district_id
            for district_id, state_id, name in (
                await session.execute(select(District.id, District.state_id, District.name))
            ).all()
        }

        city_rows = [
            {
                "id": uuid.uuid4(),
                "district_id": district_ids[(state_ids[state], district)],
                "name": city,
                "is_active": True,
            }
            for state, districts in hierarchy.items()
            for district, cities in districts.items()
            for city in cities
        ]
        await chunked_insert(
            session,
            insert(City).on_conflict_do_update(
                constraint="uq_city_per_district", set_={"is_active": True}
            ),
            city_rows,
        )
        # Remove old alias branches only after their canonical hierarchy has
        # been fully upserted by this same transaction.
        for alias, canonical in STATE_ALIASES.items():
            if alias == canonical:
                continue
            alias_state_id = await session.scalar(select(State.id).where(State.name == alias))
            canonical_state_id = await session.scalar(
                select(State.id).where(State.name == canonical)
            )
            if alias_state_id and canonical_state_id:
                district_subquery = select(District.id).where(
                    District.state_id == alias_state_id
                )
                await session.execute(
                    delete(City).where(City.district_id.in_(district_subquery))
                )
                await session.execute(
                    delete(District).where(District.state_id == alias_state_id)
                )
                await session.execute(delete(State).where(State.id == alias_state_id))
        await session.commit()
    print(
        f"Imported {len(state_rows)} states/UTs, {len(district_rows)} districts, "
        f"and {len(city_rows)} cities/localities"
    )


async def main(path: Path | None, directory: Path | None) -> None:
    if directory is not None:
        hierarchy = await asyncio.to_thread(parse_directory, directory)
    else:
        hierarchy = parse_hierarchy(await asyncio.to_thread(read_source, path))
    await import_hierarchy(hierarchy)
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--csv", type=Path, help="Optional local India Post OGD CSV")
    source.add_argument(
        "--directory",
        type=Path,
        help="Directory of per-pincode CSV files from https://github.com/IndiaPost/pin",
    )
    arguments = parser.parse_args()
    asyncio.run(main(arguments.csv, arguments.directory))
