"""Offline checks for the manual deployment guardrails."""

import os
from pathlib import Path
import json
import re
import shutil
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).with_name("deploy.sh")
GUIDE = SOURCE.parent.parent / "docs/DEVELOPMENT.md"


class DeployScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        (self.root / "backend").mkdir()
        (self.root / "frontend").mkdir()
        (self.root / "infra").mkdir()
        if SOURCE.exists():
            shutil.copy(SOURCE, self.root / "scripts/deploy.sh")
        (self.root / ".env.deploy").write_text(
            "AWS_PROFILE=test-profile\nAWS_REGION=us-east-1\n"
            "STACK_NAME=app-tracker-dev\nBUDGET_EMAIL=test@example.invalid\n"
            "GOOGLE_SECRET_NAME=test/google-oauth\n"
            "GMAIL_SECRET_NAME=test/gmail-oauth\n"
        )
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "aws"
        fake.write_text(
            "#!/bin/sh\n"
            'echo "aws $*" >> "$CALLS"\n'
            'case "$1 $2" in\n'
            '  "sts get-caller-identity") [ "${FAIL_IDENTITY:-}" ] && exit 1; echo 123456789012;;\n'
            '  "secretsmanager describe-secret")\n'
            '    case "$*" in *test/gmail-oauth*) [ "${FAIL_GMAIL_SECRET:-}" ] && exit 1;; esac\n'
            '    [ "${FAIL_SECRET:-}" ] && exit 1; echo arn:aws:secretsmanager:us-east-1:123456789012:secret:test;;\n'
            '  "cloudformation describe-stacks")\n'
            '    case "$*" in\n'
            '      *ApiUrl*) key=ApiUrl;; *CognitoAuthority*) key=CognitoAuthority;;\n'
            '      *CognitoClientId*) key=CognitoClientId;; *CognitoDomain*) key=CognitoDomain;;\n'
            '      *AmplifyAppId*) key=AmplifyAppId;; *AmplifyUrl*) key=AmplifyUrl;;\n'
            '    esac\n'
            '    [ "$key" = "${MISSING_OUTPUT:-}" ] && { echo None; exit 0; }\n'
            '    case "$key" in\n'
            '      ApiUrl) echo https://api.example.invalid;;\n'
            '      CognitoAuthority) echo https://cognito.example.invalid/pool;;\n'
            '      CognitoClientId) echo client-123;;\n'
            '      CognitoDomain) echo https://login.example.invalid;;\n'
            '      AmplifyAppId) echo app-123;;\n'
            '      AmplifyUrl) echo https://main.example.invalid;;\n'
            '    esac;;\n'
            '  "amplify create-deployment") echo \'{"zipUploadUrl":"https://upload.example.invalid","jobId":"job-123"}\';;\n'
            '  "amplify get-job") echo SUCCEED;;\n'
            'esac\n'
        )
        fake.chmod(0o755)
        for name, body in {
            "sam": 'echo "sam $*" >> "$CALLS"',
            "uv": 'echo "uv $*" >> "$CALLS"',
            "docker": 'echo "docker $*" >> "$CALLS"',
            "npm": 'echo "npm $*" >> "$CALLS"; mkdir -p dist; touch dist/index.html',
            "zip": 'echo "zip $*" >> "$CALLS"; touch "$2"',
            "curl": 'echo "curl $*" >> "$CALLS"',
            "sleep": ":",
        }.items():
            path = self.bin / name
            path.write_text("#!/bin/sh\n" + body + "\n")
            path.chmod(0o755)
        self.calls = self.root / "calls"

    def run_deploy(self, **extra):
        env = os.environ.copy()
        env.update(PATH=f"{self.bin}:{env['PATH']}", CALLS=str(self.calls))
        env.update(extra)
        return subprocess.run(
            ["bash", str(self.root / "scripts/deploy.sh")],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
        )

    def assert_no_deploy(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls.read_text() if self.calls.exists() else ""
        self.assertNotIn("sam deploy", calls)

    def test_wrong_region_stops_before_deploy(self):
        config = self.root / ".env.deploy"
        config.write_text(config.read_text().replace("AWS_REGION=us-east-1", "AWS_REGION=us-east-2"))
        result = self.run_deploy()
        self.assert_no_deploy(result)
        self.assertIn("us-east-1", result.stderr)

    def test_missing_identity_stops_before_deploy(self):
        result = self.run_deploy(FAIL_IDENTITY="1")
        self.assert_no_deploy(result)
        self.assertIn("AWS identity", result.stderr)

    def test_missing_secret_stops_before_deploy(self):
        result = self.run_deploy(FAIL_SECRET="1")
        self.assert_no_deploy(result)
        self.assertIn("Google OAuth secret", result.stderr)

    def test_missing_gmail_secret_stops_before_deploy(self):
        result = self.run_deploy(FAIL_GMAIL_SECRET="1")
        self.assert_no_deploy(result)
        self.assertIn("Gmail OAuth secret", result.stderr)

    def test_missing_output_stops_before_frontend_upload(self):
        result = self.run_deploy(MISSING_OUTPUT="CognitoClientId")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CognitoClientId", result.stderr)
        calls = self.calls.read_text()
        self.assertIn("sam deploy", calls)
        self.assertNotIn("amplify create-deployment", calls)

    def test_complete_dry_run_uploads_built_files(self):
        result = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls.read_text()
        self.assertIn("sam build --use-container", calls)
        self.assertIn("amplify create-deployment", calls)
        self.assertIn("amplify start-deployment", calls)
        self.assertIn("GmailOAuthSecretName=test/gmail-oauth", calls)
        self.assertIn("GoogleOAuthSecretName=test/google-oauth", calls)
        self.assertNotIn("get-secret-value", calls)
        self.assertIn("https://main.example.invalid", result.stdout)
        self.assertNotIn("test/gmail-oauth", result.stdout)

    def deploy_call(self):
        return next(
            line for line in self.calls.read_text().splitlines()
            if line.startswith("sam deploy")
        )

    def test_absent_model_ids_are_omitted_so_the_blank_default_applies(self):
        result = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("ModelId", self.deploy_call())

    def test_blank_model_id_is_omitted(self):
        config = self.root / ".env.deploy"
        config.write_text(config.read_text() + "OPENAI_MODEL_ID=\n")
        result = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("OpenAiModelId", self.deploy_call())

    def test_present_model_ids_are_passed_as_parameters(self):
        config = self.root / ".env.deploy"
        config.write_text(
            config.read_text()
            + "OPENAI_MODEL_ID=synthetic-openai\n"
            + "ANTHROPIC_MODEL_ID=synthetic-anthropic\n"
            + "GEMINI_MODEL_ID=synthetic-gemini\n"
        )
        result = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        call = self.deploy_call()
        self.assertIn("OpenAiModelId=synthetic-openai", call)
        self.assertIn("AnthropicModelId=synthetic-anthropic", call)
        self.assertIn("GeminiModelId=synthetic-gemini", call)
        self.assertNotIn("synthetic-openai", result.stdout + result.stderr)


class DeploymentGuideTests(unittest.TestCase):
    def test_google_secret_command_outputs_clean_json(self):
        guide = GUIDE.read_text()
        match = re.search(r"python3 -c '([^']+)' \|", guide)
        self.assertIsNotNone(match)
        result = subprocess.run(
            ["python3", "-c", match.group(1)],
            input="synthetic-client\nsynthetic-secret\n",
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"client_id": "synthetic-client", "client_secret": "synthetic-secret"},
        )


if __name__ == "__main__":
    unittest.main()
