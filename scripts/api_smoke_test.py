"""Exercise every documented API route without external network or database calls."""

import mimetypes
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db.database import get_db
from app.db.models import EMRRecord
from app.main import app
from app.services.sarvam_service import batch_audio_filename


class FakeResult:
    def __init__(self, *, scalar=None, rows=None, scalars=None):
        self._scalar = scalar
        self._rows = rows or []
        self._scalars = scalars or []

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return self._rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._scalars)


class FakeSession:
    def __init__(self, results=None, generated_record_id=None):
        self.results = list(results or [])
        self.generated_record_id = generated_record_id
        self.added = []

    async def execute(self, _statement):
        if not self.results:
            raise AssertionError("Unexpected database execute call")
        return self.results.pop(0)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        return None

    async def refresh(self, value):
        if isinstance(value, EMRRecord) and value.id is None and self.generated_record_id:
            value.id = self.generated_record_id


def override_database(session):
    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override


def assert_status(response, expected):
    assert response.status_code == expected, f"{response.request.method} {response.url}: {response.text}"


def main() -> None:
    # The Sarvam batch SDK guesses MIME solely from the local extension.
    # Browsers send MP4 uploads as video/mp4, but Sarvam accepts audio/x-m4a.
    batch_mp4_name = batch_audio_filename("consultation.mp4", "video/mp4")
    assert batch_mp4_name == "consultation.m4a"
    assert mimetypes.guess_type(batch_mp4_name)[0] in {"audio/mp4", "audio/x-m4a"}

    hospital_id = uuid.uuid4()
    user_id = uuid.uuid4()
    encounter_id = uuid.uuid4()
    record_id = uuid.uuid4()
    access_token = create_access_token(
        user_id=str(user_id),
        hospital_id=str(hospital_id),
        role="doctor",
        permissions=["emr:create", "emr:read", "emr:review"],
    )
    auth_headers = {"Authorization": f"Bearer {access_token}"}

    with TestClient(app) as client:
        assert_status(client.get("/healthz"), 200)
        assert_status(client.get("/openapi.json"), 200)
        assert_status(client.get("/docs"), 200)

        override_database(FakeSession())
        with (
            patch("app.api.routes_auth.auth_service.authenticate_admin_user", new=AsyncMock(return_value=object())),
            patch(
                "app.api.routes_auth.auth_service.issue_tokens",
                new=AsyncMock(return_value=("access-token", "refresh-token")),
            ),
        ):
            response = client.post(
                "/api/v1/auth/login",
                json={
                    "email": "owner@example.com",
                    "password": "password",
                },
            )
            assert_status(response, 200)
            assert response.json()["token_type"] == "bearer"

        override_database(FakeSession())
        with patch(
            "app.api.routes_auth.auth_service.rotate_refresh_token",
            new=AsyncMock(return_value=("new-access", "new-refresh")),
        ):
            assert_status(
                client.post("/api/v1/auth/refresh", json={"refresh_token": "refresh-token"}),
                200,
            )

        override_database(FakeSession())
        with patch(
            "app.api.routes_auth.auth_service.revoke_refresh_token", new=AsyncMock(return_value=None)
        ):
            assert_status(
                client.post("/api/v1/auth/logout", json={"refresh_token": "refresh-token"}),
                204,
            )

        encounter = SimpleNamespace(id=encounter_id, hospital_id=hospital_id)
        encounter_summary = SimpleNamespace(
            id=encounter_id,
            hospital_id=hospital_id,
            patient_id=uuid.uuid4(),
            doctor_id=user_id,
            department="General Medicine",
            status="open",
            created_at=datetime.now(timezone.utc),
        )
        override_database(FakeSession(results=[FakeResult(scalars=[encounter_summary])]))
        response = client.get("/api/v1/emr/encounters", headers=auth_headers)
        assert_status(response, 200)
        assert response.json()[0]["id"] == str(encounter_id)

        override_database(
            FakeSession(
                results=[FakeResult(scalar=encounter)],
                generated_record_id=record_id,
            )
        )
        with (
            patch("app.api.routes_emr.audit_service.log_event", new=AsyncMock(return_value=None)),
            patch(
                "app.api.routes_emr.emr_service.process_uploaded_audio",
                new=AsyncMock(return_value=None),
            ),
        ):
            response = client.post(
                "/api/v1/emr/records/upload",
                headers=auth_headers,
                data={"encounter_id": str(encounter_id), "language_code": "hi-IN"},
                files={"audio_file": ("sample.wav", b"RIFF-test-audio", "audio/wav")},
            )
            assert_status(response, 201)
            assert response.json()["emr_record_id"] == str(record_id)

        structured_note = {
            "chief_complaint": "Fever",
            "subjective": "Fever for two days",
            "objective": "",
            "assessment": "Viral illness suspected",
            "plan": "Hydration and review",
            "diagnoses": [],
            "symptoms": ["fever"],
            "medications": [],
        }
        record = SimpleNamespace(
            id=record_id,
            hospital_id=hospital_id,
            encounter_id=encounter_id,
            source_language="hi-IN",
            raw_transcript="दो दिन से बुखार",
            translated_text="Fever for two days",
            structured_note=structured_note,
            status="pending_review",
            reviewed_by=None,
            reviewed_at=None,
        )
        override_database(
            FakeSession(results=[FakeResult(scalar=record), FakeResult(rows=[])])
        )
        with patch("app.api.routes_emr.audit_service.log_event", new=AsyncMock(return_value=None)):
            assert_status(
                client.get(f"/api/v1/emr/records/{record_id}", headers=auth_headers),
                200,
            )

        review_session = FakeSession(
            results=[
                FakeResult(scalar=record),
                FakeResult(scalar=record),
                FakeResult(rows=[]),
            ]
        )
        override_database(review_session)
        with patch("app.api.routes_emr.audit_service.log_event", new=AsyncMock(return_value=None)):
            response = client.post(
                f"/api/v1/emr/records/{record_id}/review",
                headers=auth_headers,
                json={"edited_structured_note": structured_note, "confirmed_code_ids": []},
            )
            assert_status(response, 200)
            assert response.json()["status"] == "approved"

        assert_status(client.get(f"/api/v1/emr/records/{record_id}"), 401)

    app.dependency_overrides.clear()
    print(
        "API smoke tests passed: Sarvam batch MIME, health, Swagger, auth, "
        "encounters, upload, read, review, and bearer auth"
    )


if __name__ == "__main__":
    main()
