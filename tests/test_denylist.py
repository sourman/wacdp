"""Run: python3 -m unittest discover tests"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daemon


class Denylist(unittest.TestCase):
    cfg = {"denylist_ids": {"111@g.us": "AF"}, "denylist": ["Aquabros", "Famillia"]}

    def d(self, who, cid=""):
        return daemon.is_denied({"who": who, "chat_id": cid}, self.cfg)

    def test_id_exact(self):
        self.assertTrue(self.d("renamed group", "111@g.us"))
        self.assertFalse(self.d("AF", "222@g.us"))  # same title, different chat id: not denied via id

    def test_no_substring(self):
        for who in ["Afaf Mousa", "Safwat Prime", "Hafez Library", "A", "Aqua"]:
            self.assertFalse(self.d(who, "9@lid"), who)

    def test_title_normalized(self):
        self.assertTrue(self.d("Aquabros \U0001fa73\U0001f93f"))
        self.assertTrue(self.d("Famillia \ue415"))
        self.assertTrue(self.d("  aquabros "))


if __name__ == "__main__":
    unittest.main()
