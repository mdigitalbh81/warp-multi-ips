import base64
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


def load_server(tmpdir, admin_user=None, admin_password=None):
    env = {
        "ADMIN_CONFIG_FILE": str(tmpdir / "admin-config.json"),
        "ADMIN_CREDENTIALS_FILE": str(tmpdir / "admin-credentials.json"),
        "WARP_ENV_FILE": str(tmpdir / "warp-env"),
    }
    if admin_user is not None:
        env["ADMIN_USER"] = admin_user
    if admin_password is not None:
        env["ADMIN_PASSWORD"] = admin_password
    old_env = os.environ.copy()
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location("admin_server", Path("admin/server.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        os.environ.clear()
        os.environ.update(old_env)


class AdminCredentialTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tempdir.name)
        self.server = load_server(self.tmp, "admin", "Str0ng!Passw0rd")
        ok, error = self.server.ensure_admin_credentials()
        self.assertTrue(ok, error)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_initialization_hashes_env_password(self):
        data = json.loads(self.server.CREDENTIALS_FILE.read_text())
        self.assertEqual(data["username"], "admin")
        self.assertNotIn("Str0ng!Passw0rd", self.server.CREDENTIALS_FILE.read_text())
        self.assertTrue(data["password_hash"].startswith("pbkdf2_sha256$"))

    def test_correct_current_password(self):
        self.assertTrue(self.server.authenticate_admin("admin", "Str0ng!Passw0rd"))

    def test_incorrect_current_password(self):
        response, status = self.server.update_admin_credentials({
            "current_password": "wrong",
            "new_password": "N3w!PasswordLong",
            "confirm_password": "N3w!PasswordLong",
        })
        self.assertEqual(status, 400)
        self.assertIn("current password is incorrect", response["errors"])

    def test_password_confirmation_mismatch(self):
        response, status = self.server.update_admin_credentials({
            "current_password": "Str0ng!Passw0rd",
            "new_password": "N3w!PasswordLong",
            "confirm_password": "Different!Pass1",
        })
        self.assertEqual(status, 400)
        self.assertIn("do not match", response["errors"][0])

    def test_invalid_new_password(self):
        response, status = self.server.update_admin_credentials({
            "current_password": "Str0ng!Passw0rd",
            "new_password": "short",
            "confirm_password": "short",
        })
        self.assertEqual(status, 400)
        self.assertTrue(any("at least 12" in item for item in response["errors"]))

    def test_change_password_only_invalidates_old_password(self):
        response, status = self.server.update_admin_credentials({
            "current_password": "Str0ng!Passw0rd",
            "new_password": "N3w!PasswordLong",
            "confirm_password": "N3w!PasswordLong",
        })
        self.assertEqual(status, 200)
        self.assertFalse(self.server.authenticate_admin("admin", "Str0ng!Passw0rd"))
        self.assertTrue(self.server.authenticate_admin("admin", "N3w!PasswordLong"))

    def test_change_username_only(self):
        response, status = self.server.update_admin_credentials({
            "current_password": "Str0ng!Passw0rd",
            "new_username": "felipe",
        })
        self.assertEqual(status, 200)
        self.assertFalse(self.server.authenticate_admin("admin", "Str0ng!Passw0rd"))
        self.assertTrue(self.server.authenticate_admin("felipe", "Str0ng!Passw0rd"))

    def test_change_username_and_password(self):
        response, status = self.server.update_admin_credentials({
            "current_password": "Str0ng!Passw0rd",
            "new_username": "felipe",
            "new_password": "N3w!PasswordLong",
            "confirm_password": "N3w!PasswordLong",
        })
        self.assertEqual(status, 200)
        self.assertTrue(self.server.authenticate_admin("felipe", "N3w!PasswordLong"))

    def test_persistence_after_restart(self):
        self.server.update_admin_credentials({
            "current_password": "Str0ng!Passw0rd",
            "new_username": "felipe",
            "new_password": "N3w!PasswordLong",
            "confirm_password": "N3w!PasswordLong",
        })
        restarted = load_server(self.tmp)
        self.assertTrue(restarted.authenticate_admin("felipe", "N3w!PasswordLong"))

    def test_public_apis_do_not_return_password_or_hash(self):
        config_json = json.dumps(self.server.public_config())
        account_json = json.dumps(self.server.public_admin_account())
        self.assertNotIn("Str0ng!Passw0rd", config_json)
        self.assertNotIn("password_hash", config_json)
        self.assertNotIn("password_hash", account_json)

    def test_no_password_in_response(self):
        response, status = self.server.update_admin_credentials({
            "current_password": "Str0ng!Passw0rd",
            "new_password": "N3w!PasswordLong",
            "confirm_password": "N3w!PasswordLong",
        })
        self.assertEqual(status, 200)
        self.assertNotIn("N3w!PasswordLong", json.dumps(response))
        self.assertNotIn("password_hash", json.dumps(response))

    def test_missing_initial_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_server(Path(tmp), "admin", "")
            ok, error = module.ensure_admin_credentials()
            self.assertFalse(ok)
            self.assertIn("ADMIN_USER and ADMIN_PASSWORD", error)


if __name__ == "__main__":
    unittest.main()
