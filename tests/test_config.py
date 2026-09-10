from pathlib import Path
import tempfile
import unittest

from galactitalk.config import (
    AppConfig, ServerSettings, read_config, remove_deprecated_media_controls,
    validate_config, write_config,
)


class ConfigTests(unittest.TestCase):
    def test_round_trip_and_legacy_sections_survive(self):
        config = AppConfig(servers=[ServerSettings("one", "localhost", password="secret")])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text("[legacy-account:old]\npassword = old-secret\n", encoding="utf-8")
            write_config(path, config)
            loaded = read_config(path)
            text = path.read_text(encoding="utf-8")
        self.assertEqual(loaded.servers[0].password, "secret")
        self.assertIn("[legacy-account:old]", text)
        self.assertEqual(validate_config(loaded), [])

    def test_duplicate_names_and_bad_ports_are_rejected(self):
        config = AppConfig(servers=[ServerSettings("same", "one"), ServerSettings("same", "two", tcp_port=0)])
        errors = validate_config(config)
        self.assertTrue(any("Duplicate" in error for error in errors))
        self.assertTrue(any("invalid port" in error for error in errors))

    def test_retired_media_options_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text(
                "[media]\ncache_file=old.json\n"
                "[server:one]\nhost=x\nbanned_users=one\nblocked_media_commands=p, u\nadmins=core-admin\n",
                encoding="utf-8",
            )
            self.assertTrue(remove_deprecated_media_controls(path))
            text = path.read_text(encoding="utf-8")
        self.assertNotIn("banned_users", text)
        self.assertNotIn("blocked_media_commands", text)
        self.assertNotIn("admins", text)
        self.assertNotIn("cache_file", text)


if __name__ == "__main__":
    unittest.main()
