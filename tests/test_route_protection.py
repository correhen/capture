import os
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

_tmp = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_tmp.name) / "ctf-test.sqlite")

from database import db
from server import app


class RouteProtectionTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        with db() as conn:
            team = conn.execute("SELECT token FROM teams ORDER BY id LIMIT 1").fetchone()
        self.team_token = team["token"]

    def login(self):
        with self.client.session_transaction() as sess:
            sess["team_token"] = self.team_token

    def test_public_pages_stay_public(self):
        for path in ["/", "/scoreboard", "/scoreboard/islands", "/challenges"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_downloads_and_detail_require_team_login(self):
        protected_paths = [
            "/challenge/ctf01-voorbeeldvraag",
            "/download-bundle/ctf01-voorbeeldvraag",
            "/download-all",
            "/static/challenges/test",
            "/static/challenges/1%20-%20Easy/CTF01%20-%20Voorbeeldvraag/challenge.md",
            "/challenge/ctf01-voorbeeldvraag/asset/challenge.md",
        ]
        for path in protected_paths:
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/?login_required=challenges", response.location)

    def test_logged_in_team_can_open_challenge_assets(self):
        self.login()
        self.assertEqual(self.client.get("/challenges").status_code, 200)
        self.assertEqual(self.client.get("/challenge/ctf01-voorbeeldvraag").status_code, 200)
        response = self.client.get("/static/challenges/test")
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_submit_requires_team_login(self):
        response = self.client.get("/submit", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/?login_required=submit", response.location)

    def test_challenge_detail_renders_markdown_and_logo(self):
        self.login()
        response = self.client.get("/challenge/ctf01-voorbeeldvraag")
        body = response.data.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn("<h1>CTF01 - Voorbeeldvraag</h1>", body)
        self.assertIn("Welkom bij de Crypto CTF.", body)
        self.assertIn('alt="Crypto-Carib"', body)

    def test_challenge_detail_shows_fallback_without_markdown(self):
        self.login()
        response = self.client.get("/challenge/ctf02-exchanges")
        body = response.data.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Voor deze opdracht is nog geen webtekst beschikbaar", body)

    def test_image_only_challenge_has_no_broken_pdf_button(self):
        self.login()
        response = self.client.get("/challenge/ctf37-rechtspraak")
        body = response.data.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Download PDF-versie van de opdracht", body)
        self.assertIn("<img", body)

    def test_logo_is_public(self):
        response = self.client.get("/static/img/crypto-carib-logo.png")
        self.assertEqual(response.status_code, 200)
        response.close()


if __name__ == "__main__":
    unittest.main()
