import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

from models import flag_hash_candidates, sha256_hex


class FlagNormalizationTests(unittest.TestCase):
    def assert_accepts_same_flag(self, stored_flag, submitted_values):
        stored_hash = sha256_hex(stored_flag)
        for submitted in submitted_values:
            with self.subTest(stored_flag=stored_flag, submitted=submitted):
                self.assertIn(stored_hash, flag_hash_candidates(submitted))

    def test_accepts_text_flags_with_or_without_wrapper_and_case(self):
        self.assert_accepts_same_flag(
            "CTF{SATOSHINAKAMOTO}",
            [
                "CTF{SATOSHINAKAMOTO}",
                "SATOSHINAKAMOTO",
                "ctf{satoshinakamoto}",
                "satoshinakamoto",
            ],
        )

    def test_accepts_decimal_address_and_dash_flags_without_breaking_content(self):
        self.assert_accepts_same_flag("CTF{0.4011}", ["CTF{0.4011}", "0.4011"])
        self.assert_accepts_same_flag(
            "CTF{0x224Da1A29CF5C7021d75C77DD74425C2de32EEc7}",
            [
                "CTF{0x224Da1A29CF5C7021d75C77DD74425C2de32EEc7}",
                "0x224Da1A29CF5C7021d75C77DD74425C2de32EEc7",
            ],
        )
        self.assert_accepts_same_flag("CTF{BTC-ETH-BNB}", ["CTF{BTC-ETH-BNB}", "BTC-ETH-BNB"])

    def test_empty_input_is_never_valid(self):
        self.assertEqual(flag_hash_candidates(""), set())
        self.assertEqual(flag_hash_candidates("   "), set())
        self.assertEqual(flag_hash_candidates("CTF{}"), set())


if __name__ == "__main__":
    unittest.main()
