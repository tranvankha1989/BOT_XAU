"""Ô 'Số luồng AI': chọn số luồng, không vượt mức an toàn min(4, CPU logic // 2)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mt5_scalping_bot as b  # noqa: E402


class TestResolveAiThreads(unittest.TestCase):
    def test_auto_la_muc_an_toan(self):
        self.assertEqual(b.resolve_ai_threads("Auto", 4), (2, False))   # i5-4300U: 4 luồng logic -> 2
        self.assertEqual(b.resolve_ai_threads("Auto", 16), (4, False))  # tối đa 4
        self.assertEqual(b.resolve_ai_threads("", 8), (4, False))
        self.assertEqual(b.resolve_ai_threads(None, 2), (1, False))

    def test_chon_trong_muc_an_toan(self):
        self.assertEqual(b.resolve_ai_threads("2", 16), (2, False))
        self.assertEqual(b.resolve_ai_threads("4", 8), (4, False))

    def test_vuot_muc_an_toan_bi_ha(self):
        self.assertEqual(b.resolve_ai_threads("4", 4), (2, True))
        self.assertEqual(b.resolve_ai_threads("8", 16), (4, True))
        self.assertEqual(b.resolve_ai_threads("6", 4), (2, True))

    def test_gia_tri_sai(self):
        self.assertEqual(b.resolve_ai_threads("abc", 8), (4, False))
        self.assertEqual(b.resolve_ai_threads("0", 8), (4, False))

    def test_co_trong_cau_hinh_mac_dinh_va_muc_1(self):
        for pk in ("XAUUSD", "BTCUSD"):
            self.assertEqual(b.DEFAULT_PROFILE_SETTINGS[pk]["ai_threads"], "Auto")
        self.assertIn("ai_threads", b.PARAM_HELP_GUIDE)
        self.assertEqual(b.AI_THREAD_CHOICES, ["Auto", "2", "4", "6", "8"])


if __name__ == "__main__":
    unittest.main()
