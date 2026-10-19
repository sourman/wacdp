"""Run: python3 -m unittest discover tests"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daemon


class Denylist(unittest.TestCase):
    cfg = {"denylist_ids": {"111@g.us": "group label"}, "denylist": ["ignored title"]}

    def d(self, who, cid=""):
        return daemon.is_denied({"who": who, "chat_id": cid}, self.cfg)

    def test_id_exact(self):
        self.assertTrue(self.d("renamed group", "111@g.us"))
        self.assertFalse(self.d("group label", "222@g.us"))
        self.assertFalse(self.d("no id", ""))

    def test_title_list_is_ignored(self):
        self.assertFalse(self.d("ignored title", "9@lid"))


if __name__ == "__main__":
    unittest.main()
