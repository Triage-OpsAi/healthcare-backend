import contextlib
import io
import os
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class InvitationConfigurationTests(unittest.TestCase):
    def test_sibling_smtp_fallback_and_backend_environment_precedence(self):
        source = (ROOT / "app/core/config.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            backend = parent / "healthcare-backend"
            frontend = parent / "EMR-NextApp"
            backend.mkdir()
            frontend.mkdir()
            (frontend / ".env").write_text("SMTP_HOST=smtp.frontend.test\nSMTP_PASSWORD=fallback\n")
            backend_env = backend / ".env"
            backend_env.write_text("DATABASE_URL=postgresql://test/db\nJWT_SECRET_KEY=test\nSARVAM_API_KEY=test\n")

            def load():
                namespace = {"__file__": str(backend / "app/core/config.py")}
                exec(compile(source, namespace["__file__"], "exec"), namespace)
                return namespace["settings"]

            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load().SMTP_HOST, "smtp.frontend.test")
                with backend_env.open("a") as output:
                    output.write("SMTP_HOST=smtp.backend.test\nSMTP_PASSWORD=backend\n")
                self.assertEqual(load().SMTP_HOST, "smtp.backend.test")
                self.assertEqual(load().SMTP_PASSWORD, "backend")
                os.environ["SMTP_HOST"] = "smtp.process.test"
                self.assertEqual(load().SMTP_HOST, "smtp.process.test")

    def deployment_env(self, overrides=None):
        workflow = (ROOT / ".github/workflows/deploy-ec2.yml").read_text(encoding="utf-8")
        source = textwrap.dedent(workflow.split("python3 - <<'PY'\n", 1)[1].split("          PY", 1)[0])
        values = dict.fromkeys((
            "JWT_SECRET_KEY", "SARVAM_API_KEY", "OPENAI_API_KEY", "REDIS_URL",
            "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "AWS_S3_BUCKET",
        ), "test")
        values.update(
            DATABASE_URL="postgresql://user:password@db.example.com/test",
            SMTP_HOST="smtp.example.com", SMTP_USERNAME="sender@example.com",
            SMTP_PASSWORD="test-app-password", SMTP_FROM_EMAIL="sender@example.com",
            FRONTEND_URL="https://admin.example.com", DOCTOR_FRONTEND_URL="https://clinical.example.com",
        )
        values.update(overrides or {})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "production.env"
            source = source.replace('Path("/tmp/meridian-production.env")', f"Path({str(output)!r})")
            with patch.dict(os.environ, values, clear=True), contextlib.redirect_stdout(io.StringIO()):
                exec(compile(source, "deployment-env", "exec"), {})
            return output.read_text(encoding="utf-8")

    def test_deployment_includes_smtp_and_link_settings(self):
        output = self.deployment_env()
        self.assertIn("SMTP_PASSWORD=test-app-password\n", output)
        self.assertIn("SMTP_HOST=smtp.example.com\n", output)
        self.assertIn("SMTP_USE_TLS=true\n", output)
        self.assertIn("SMTP_USE_SSL=false\n", output)
        self.assertIn("FRONTEND_URL=https://admin.example.com\n", output)
        self.assertIn("DOCTOR_FRONTEND_URL=https://clinical.example.com\n", output)

    def test_deployment_rejects_missing_smtp(self):
        with self.assertRaisesRegex(SystemExit, "SMTP_HOST"):
            self.deployment_env({"SMTP_HOST": ""})

    def test_deployment_rejects_missing_password(self):
        with self.assertRaisesRegex(SystemExit, "SMTP_PASSWORD"):
            self.deployment_env({"SMTP_PASSWORD": ""})

    def test_deployment_rejects_local_invitation_urls(self):
        for name in ("FRONTEND_URL", "DOCTOR_FRONTEND_URL"):
            with self.subTest(name=name), self.assertRaisesRegex(SystemExit, name):
                self.deployment_env({name: "http://localhost:3000"})


if __name__ == "__main__":
    unittest.main()
