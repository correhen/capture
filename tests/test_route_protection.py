import os
import shutil
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
from models import sha256_hex


class RouteProtectionTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.temp_challenge_dirs = []
        with db() as conn:
            team = conn.execute("SELECT token FROM teams ORDER BY id LIMIT 1").fetchone()
            self.team_id = conn.execute("SELECT id FROM teams ORDER BY id LIMIT 1").fetchone()["id"]
            conn.execute("DELETE FROM solves")
            conn.execute("DELETE FROM challenges WHERE title=?", ("CTF01 - Voorbeeldvraag",))
            conn.execute(
                "INSERT INTO challenges(title, difficulty, flag_hash, points, is_active) VALUES(?,?,?,?,1)",
                ("CTF01 - Voorbeeldvraag", "makkelijk", sha256_hex("CTF{TESTANSWER}"), 1),
            )
            self.ctf01_id = conn.execute(
                "SELECT id FROM challenges WHERE title=? ORDER BY id DESC LIMIT 1",
                ("CTF01 - Voorbeeldvraag",),
            ).fetchone()["id"]
        self.team_token = team["token"]

    def tearDown(self):
        for path in self.temp_challenge_dirs:
            shutil.rmtree(path, ignore_errors=True)

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
        self.assertIn("Tijdens een doorzoeking werd een mnemonic phrase", body)
        self.assertIn('alt="Crypto-Carib"', body)
        self.assertNotIn("Kraak de code, claim de vlag", body)
        self.assertIn('id="detail-submit-form"', body)
        self.assertEqual(body.count('id="detail-submit-form"'), 1)
        self.assertNotIn("Open algemene submitpagina", body)

    def test_challenge_detail_shows_fallback_without_markdown(self):
        challenge_dir = APP_DIR / "static" / "challenges" / "1 - Easy" / "__Test Geen Markdown"
        challenge_dir.mkdir(parents=True, exist_ok=True)
        self.temp_challenge_dirs.append(challenge_dir)
        (challenge_dir / "opdracht.txt").write_text("Testbijlage zonder webtekst.", encoding="utf-8")
        with db() as conn:
            conn.execute(
                "INSERT INTO challenges(title, difficulty, flag_hash, points, is_active) VALUES(?,?,?,?,1)",
                ("__Test Geen Markdown", "makkelijk", "testhash", 1),
            )

        self.login()
        response = self.client.get("/challenge/test-geen-markdown")
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

    def test_inline_submit_accepts_correct_and_rejects_wrong_flags(self):
        self.login()
        wrong = self.client.post(
            "/api/submit",
            data={"challenge_id": str(self.ctf01_id), "flag": "verkeerd"},
        )
        self.assertFalse(wrong.get_json()["correct"])
        self.assertIn("CTF{ } is niet verplicht", wrong.get_json()["message"])

        correct = self.client.post(
            "/api/submit",
            data={"challenge_id": str(self.ctf01_id), "flag": "testanswer"},
        )
        self.assertTrue(correct.get_json()["correct"])

    def test_submit_page_still_works(self):
        self.login()
        response = self.client.get(f"/submit?challenge_id={self.ctf01_id}")
        body = response.data.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Flag indienen", body)
        self.assertIn(f'value="{self.ctf01_id}" selected', body)

    def test_logout_clears_team_session(self):
        self.login()
        nav = self.client.get("/challenges").data.decode("utf-8")
        self.assertIn("Uitloggen", nav)
        self.assertNotIn("Wissel team", nav)
        response = self.client.get("/logout", follow_redirects=True)
        body = response.data.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Je bent uitgelogd. Kies opnieuw je team om verder te gaan.", body)
        with self.client.session_transaction() as sess:
            self.assertNotIn("team_token", sess)

    def test_scoreboard_shows_podium_and_table(self):
        response = self.client.get("/scoreboard")
        body = response.data.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn('aria-label="Podium"', body)
        self.assertIn("<table>", body)
        self.assertIn('href="/live"', body)

    def test_live_board_is_public_and_safe(self):
        with db() as conn:
            join_code = conn.execute(
                "SELECT join_code FROM teams WHERE id=?",
                (self.team_id,),
            ).fetchone()["join_code"]
        response = self.client.get("/live")
        body = response.data.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Crypto-Carib Live", body)
        self.assertIn("Podium", body)
        self.assertNotIn(join_code, body)
        self.assertNotIn("CTF{", body)

    def test_random_challenge_requires_login(self):
        response = self.client.get("/random-challenge", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/?login_required=challenges", response.location)

    def test_random_challenge_redirects_to_unsolved_challenge(self):
        self.login()
        response = self.client.get("/random-challenge", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/challenge/ctf01-voorbeeldvraag", response.location)

    def test_random_challenge_all_done_message(self):
        self.login()
        with db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO solves(team_id, challenge_id) VALUES(?,?)",
                (self.team_id, self.ctf01_id),
            )
        response = self.client.get("/random-challenge")
        body = response.data.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Jullie hebben alle opdrachten opgelost!", body)
        self.assertIn("Scoreboard bekijken", body)

    def test_challenges_overview_keeps_pdf_secondary(self):
        self.login()
        response = self.client.get("/challenges")
        body = response.data.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Download PDF", body)
        self.assertNotIn("Flag indienen</a>", body)
        self.assertIn("PDF beschikbaar", body)
        self.assertIn("Open opdracht", body)
        self.assertIn("Random opdracht", body)
        self.assertIn('data-filter="open"', body)
        self.assertIn("Jullie voortgang:", body)

    def test_detail_success_feedback_and_random_action_render(self):
        self.login()
        response = self.client.get("/challenge/ctf01-voorbeeldvraag")
        body = response.data.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn("success-pulse", body)
        self.assertIn('href="/random-challenge"', body)

    def test_home_team_cards_render_with_icon_and_color(self):
        response = self.client.get("/")
        body = response.data.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn("team-icon", body)
        self.assertIn("--team-color:", body)


if __name__ == "__main__":
    unittest.main()
