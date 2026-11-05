"""Run: python3 -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daemon


def chat(**kw):
    base = {
        "who": "Ada",
        "chat_id": "111@c.us",
        "sender": "",
        "preview": "hi",
        "unread": 1,
        "muted": False,
        "time": "1:00 PM",
        "from_me": False,
        "is_reaction": False,
        "is_address_book_contact": 1,
        "is_business": False,
        "is_group": False,
    }
    base.update(kw)
    return base


CFG = {
    "denylist_ids": {"999@lid": "denied"},
    "client_allowlist": {
        "111@c.us": "Ada",
        "222@g.us": "Client Group",
    },
}


class PhoneTitle(unittest.TestCase):
    def test_phone_shapes(self):
        self.assertTrue(daemon.title_looks_like_phone("+20 11 45019791"))
        self.assertTrue(daemon.title_looks_like_phone("+962 7 7800 8747"))
        self.assertTrue(daemon.title_looks_like_phone("+1 (416) 358-5801"))
        self.assertFalse(daemon.title_looks_like_phone("Mountain View Resale"))
        self.assertFalse(daemon.title_looks_like_phone("Ali Raad"))
        self.assertFalse(daemon.title_looks_like_phone(""))


class WakeKind(unittest.TestCase):
    def test_allowlisted_client(self):
        self.assertEqual(daemon.wake_kind(chat(), CFG), "client")

    def test_allowlisted_group(self):
        c = chat(who="Client Group", chat_id="222@g.us", is_group=True, is_address_book_contact=None)
        self.assertEqual(daemon.wake_kind(c, CFG), "client")

    def test_saved_contact_not_allowlisted(self):
        c = chat(who="Friend", chat_id="333@lid", is_address_book_contact=1)
        self.assertIsNone(daemon.wake_kind(c, CFG))

    def test_unknown_ab0(self):
        c = chat(who="Mountain View Resale", chat_id="444@lid", is_address_book_contact=0, is_business=True)
        self.assertEqual(daemon.wake_kind(c, CFG), "unknown")

    def test_unknown_phone_title_fiber_miss(self):
        c = chat(who="+20 11 45019791", chat_id="555@lid", is_address_book_contact=None)
        self.assertEqual(daemon.wake_kind(c, CFG), "unknown")

    def test_named_fiber_miss_is_not_unknown(self):
        c = chat(who="Someone Named", chat_id="666@lid", is_address_book_contact=None)
        self.assertIsNone(daemon.wake_kind(c, CFG))

    def test_group_not_allowlisted_never_unknown(self):
        c = chat(who="Random Group", chat_id="777@g.us", is_group=True, is_address_book_contact=0)
        self.assertIsNone(daemon.wake_kind(c, CFG))

    def test_denylist_beats_allowlist_and_unknown(self):
        self.assertIsNone(daemon.wake_kind(chat(chat_id="999@lid", is_address_book_contact=0), CFG))
        self.assertIsNone(
            daemon.wake_kind(
                chat(chat_id="999@lid", is_address_book_contact=1),
                {**CFG, "client_allowlist": {"999@lid": "x"}},
            )
        )


class DiffAllowlist(unittest.TestCase):
    def diff(self, state, chats, cfg=None):
        return daemon.diff_incoming(state, chats, CFG if cfg is None else cfg, now=1_700_000_000, gate_sent={})

    def test_allowlisted_badge_gets_kind_client(self):
        state = {"seen": {}}
        hits = self.diff(state, [chat(preview="ping", unread=2)])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["kind"], "client")
        self.assertEqual(hits[0]["reason"], "badge")

    def test_saved_non_allowlist_baselined_no_hit(self):
        state = {"seen": {}}
        friend = chat(who="Friend", chat_id="333@lid", preview="hey", unread=3)
        hits = self.diff(state, [friend])
        self.assertEqual(hits, [])
        self.assertEqual(state["baseline"]["Friend"]["preview"], "hey")

    def test_unknown_noncontact_gets_kind_unknown(self):
        state = {"seen": {}}
        spam = chat(
            who="+20 11 45019791",
            chat_id="555@lid",
            preview="Mountain View iCity",
            unread=1,
            is_address_book_contact=0,
            is_business=True,
        )
        hits = self.diff(state, [spam])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["kind"], "unknown")
        self.assertTrue(hits[0].get("is_business"))
        self.assertEqual(hits[0]["is_address_book_contact"], 0)

    def test_empty_allowlist_still_wakes_unknown(self):
        cfg = {"denylist_ids": {}, "client_allowlist": {}}
        state = {"seen": {}}
        hits = self.diff(
            state,
            [chat(who="+20 10 23417084", chat_id="8@lid", unread=1, is_address_book_contact=0)],
            cfg=cfg,
        )
        self.assertEqual([h["kind"] for h in hits], ["unknown"])
        # saved contact with empty allowlist: quiet
        state = {"seen": {}}
        hits = self.diff(
            state,
            [chat(who="Friend", chat_id="9@lid", unread=1, is_address_book_contact=1)],
            cfg=cfg,
        )
        self.assertEqual(hits, [])



class BusinessAndArchived(unittest.TestCase):
    def test_business_flag_miss_is_unknown(self):
        # Named realtor API blast: fiber/store missed AB, but is_business set
        c = chat(
            who="Mountain View",
            chat_id="213077823905831@lid",
            is_address_book_contact=None,
            is_business=True,
            archived=True,
            unread=16,
        )
        self.assertEqual(daemon.wake_kind(c, CFG), "unknown")

    def test_named_non_business_flag_miss_stays_quiet(self):
        c = chat(who="Some Friend", chat_id="999@lid", is_address_book_contact=None, is_business=False)
        self.assertIsNone(daemon.wake_kind(c, CFG))


    def test_pushname_only_ab0_is_unknown(self):
        # Archived realtor: Store omitted isAddressBookContact; scrape infers 0
        c = chat(
            who="Home Finder",
            chat_id="39295410827333@lid",
            is_address_book_contact=0,
            is_business=False,
            archived=True,
            unread=1,
        )
        self.assertEqual(daemon.wake_kind(c, CFG), "unknown")

    def test_archived_unknown_badge_payload(self):
        state = {"seen": {}}
        spam = chat(
            who="Mountain View",
            chat_id="213077823905831@lid",
            preview="Last chance to own",
            unread=16,
            is_address_book_contact=0,
            is_business=True,
            archived=True,
        )
        hits = daemon.diff_incoming(state, [spam], CFG, now=1_700_000_000, gate_sent={})
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["kind"], "unknown")
        self.assertTrue(hits[0].get("archived"))
        self.assertTrue(hits[0].get("is_business"))

if __name__ == "__main__":
    unittest.main()
