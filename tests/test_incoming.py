"""Run: python3 -m unittest discover tests"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import daemon


def chat(**kw):
    base = {
        "who": "Ada",
        "chat_id": "111@c.us",
        "sender": "",
        "preview": "hi",
        "unread": 0,
        "muted": False,
        "time": "1:00 PM",
        "from_me": False,
        "is_reaction": False,
    }
    base.update(kw)
    return base


class Activity(unittest.TestCase):
    def test_own_markers(self):
        self.assertTrue(daemon.is_own_activity(chat(from_me=True, preview="Hello")))
        self.assertTrue(daemon.is_own_activity(chat(from_me=False, preview="You: Hello")))
        self.assertTrue(daemon.is_own_activity(chat(from_me=False, preview="You reacted 👍 to hi")))
        self.assertTrue(daemon.is_own_activity(chat(from_me=False, preview="You deleted this message")))
        self.assertTrue(
            daemon.is_own_activity(chat(from_me=False, preview="Hello", status_icon="wds-ic-delivered"))
        )
        self.assertFalse(daemon.is_own_activity(chat(preview="you are late")))
        self.assertFalse(daemon.is_own_activity(chat(preview="You there?")))

    def test_reaction_markers(self):
        self.assertTrue(daemon.is_reaction_activity(chat(is_reaction=True, preview="❤️")))
        self.assertTrue(daemon.is_reaction_activity(chat(preview='Reacted ❤️ to "hi"')))
        self.assertTrue(daemon.is_reaction_activity(chat(preview="Alice reacted 👍 to hi")))
        self.assertFalse(daemon.is_reaction_activity(chat(preview="I reacted badly")))

    def test_clock_vs_day_label(self):
        self.assertTrue(daemon.is_clock_time("1:02 PM"))
        self.assertTrue(daemon.is_clock_time("13:02"))
        self.assertFalse(daemon.is_clock_time("Yesterday"))
        self.assertFalse(daemon.is_clock_time("Monday"))


class DiffIncoming(unittest.TestCase):
    cfg = {"denylist": [], "denylist_ids": {}}

    def diff(self, state, chats, gate_sent=None, cfg=None):
        return daemon.diff_incoming(
            state,
            chats,
            self.cfg if cfg is None else cfg,
            now=1_700_000_000,
            gate_sent={} if gate_sent is None else gate_sent,
        )

    def test_first_sight_seeds_baseline_without_alert(self):
        state = {"seen": {}}
        hits = self.diff(state, [chat(preview="old thread", unread=0)])
        self.assertEqual(hits, [])
        self.assertEqual(state["baseline"]["Ada"]["preview"], "old thread")

    def test_open_chat_reply_alerts_when_unread_stays_zero(self):
        state = {"seen": {}, "baseline": {"Ada": daemon.chat_snapshot(chat(preview="Hello", time="1:00 PM", from_me=True))}}
        reply = chat(preview="On my way", time="1:01 PM", unread=0, from_me=False)
        hits = self.diff(state, [reply])
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit["reason"], "preview")
        self.assertEqual(hit["who"], "Ada")
        self.assertEqual(hit["chat_id"], "111@c.us")
        self.assertEqual(hit["sender"], "")
        self.assertEqual(hit["preview"], "On my way")
        self.assertEqual(hit["unread"], 0)
        self.assertIs(hit["muted"], False)
        # next poll of the same row is not a second alert
        self.assertEqual(self.diff(state, [reply]), [])

    def test_same_text_new_clock_alerts_once(self):
        state = {"seen": {}, "baseline": {"Ada": daemon.chat_snapshot(chat(preview="ok", time="1:00 PM"))}}
        nxt = chat(preview="ok", time="1:02 PM", unread=0)
        hits = self.diff(state, [nxt])
        self.assertEqual([h["reason"] for h in hits], ["preview"])
        self.assertEqual(self.diff(state, [nxt]), [])

    def test_day_rollover_is_not_a_message(self):
        state = {"seen": {}, "baseline": {"Ada": daemon.chat_snapshot(chat(preview="ok", time="11:51 PM"))}}
        hits = self.diff(state, [chat(preview="ok", time="Yesterday", unread=0)])
        self.assertEqual(hits, [])

    def test_yesterday_then_new_clock_same_text_alerts(self):
        state = {"seen": {}, "baseline": {"Ada": daemon.chat_snapshot(chat(preview="ok", time="Yesterday"))}}
        hits = self.diff(state, [chat(preview="ok", time="9:00 AM", unread=0)])
        self.assertEqual([h["reason"] for h in hits], ["preview"])

    def test_own_send_does_not_alert(self):
        state = {"seen": {}, "baseline": {"Ada": daemon.chat_snapshot(chat(preview="old", time="1:00 PM"))}}
        sent = chat(preview="Hello", time="1:01 PM", from_me=True, unread=0)
        self.assertEqual(self.diff(state, [sent]), [])
        self.assertEqual(state["baseline"]["Ada"]["preview"], "Hello")
        # a real reply after that still alerts
        hits = self.diff(state, [chat(preview="back", time="1:02 PM", from_me=False, unread=0)])
        self.assertEqual([h["reason"] for h in hits], ["preview"])

    def test_you_prefix_and_status_tick_are_not_inbound(self):
        state = {"seen": {}, "baseline": {"Ada": daemon.chat_snapshot(chat(preview="old"))}}
        self.assertEqual(self.diff(state, [chat(preview="You: Hello", from_me=False)]), [])
        state = {"seen": {}, "baseline": {"Ada": daemon.chat_snapshot(chat(preview="old"))}}
        self.assertEqual(
            self.diff(state, [chat(preview="Hello", from_me=False, status_icon="msg-dblcheck")]),
            [],
        )

    def test_reaction_rows_do_not_alert_even_with_a_badge(self):
        state = {"seen": {}, "baseline": {"Ada": daemon.chat_snapshot(chat(preview="Hello", from_me=True))}}
        reacted = chat(preview='Reacted ❤️ to "Hello"', unread=0, is_reaction=True)
        self.assertEqual(self.diff(state, [reacted]), [])
        # badge + reaction preview still not an alert, and does not stick in seen
        state = {"seen": {}}
        badge = chat(preview="Alice reacted 👍 to hi", unread=1, time="1:03 PM")
        self.assertEqual(self.diff(state, [badge]), [])
        self.assertNotIn("Ada", state["seen"])

    def test_sentence_containing_reacted_still_alerts(self):
        state = {"seen": {}, "baseline": {"Ada": daemon.chat_snapshot(chat(preview="Hello"))}}
        hits = self.diff(state, [chat(preview="I reacted badly", time="1:05 PM", unread=0)])
        self.assertEqual([h["preview"] for h in hits], ["I reacted badly"])

    def test_gate_sent_hides_our_text_landing_not_a_later_reply(self):
        stamp = {"Ada": {"preview": "Hello", "until": 1_700_000_100}}
        state = {"seen": {}, "baseline": {"Ada": daemon.chat_snapshot(chat(preview="old", time="1:00 PM"))}}
        # ticks lagged: row shows our text but from_me is still false
        landed = chat(preview="Hello", time="1:01 PM", from_me=False, unread=0)
        self.assertEqual(self.diff(state, [landed], gate_sent=stamp), [])
        self.assertEqual(state["baseline"]["Ada"]["preview"], "Hello")
        # different reply text is not the stamp
        hits = self.diff(
            state,
            [chat(preview="Hello there", time="1:02 PM", from_me=False, unread=0)],
            gate_sent=stamp,
        )
        self.assertEqual([h["reason"] for h in hits], ["preview"])
        # same stamp text again, new clock: they replied with the same words
        state = {"seen": {}, "baseline": {"Ada": daemon.chat_snapshot(chat(preview="Hello", time="1:01 PM", from_me=True))}}
        hits = self.diff(
            state,
            [chat(preview="Hello", time="1:03 PM", from_me=False, unread=0)],
            gate_sent=stamp,
        )
        self.assertEqual([h["reason"] for h in hits], ["preview"])

    def test_whitespace_only_preview_change_is_ignored(self):
        state = {"seen": {}, "baseline": {"Ada": daemon.chat_snapshot(chat(preview="on my way", time="1:00 PM"))}}
        hits = self.diff(state, [chat(preview="on   my\nway", time="1:00 PM", unread=0)])
        self.assertEqual(hits, [])

    def test_badge_payload_and_dedup(self):
        state = {"seen": {}}
        row = chat(preview="ping", unread=2, time="1:00 PM", sender="Bob", chat_id="9@g.us")
        hits = self.diff(state, [row])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["reason"], "badge")
        self.assertEqual(hits[0]["unread"], 2)
        self.assertEqual(hits[0]["sender"], "Bob")
        self.assertEqual(hits[0]["chat_id"], "9@g.us")
        self.assertEqual(hits[0]["preview"], "ping")
        self.assertEqual(self.diff(state, [row]), [])
        # unread count moved: another message, even with the same preview
        again = chat(preview="ping", unread=3, time="1:00 PM", sender="Bob", chat_id="9@g.us")
        hits = self.diff(state, [again])
        self.assertEqual([h["reason"] for h in hits], ["badge"])

    def test_badge_then_read_does_not_preview_alert_the_same_text(self):
        state = {"seen": {}}
        self.diff(state, [chat(preview="ping", unread=1, time="1:00 PM")])
        cleared = chat(preview="ping", unread=0, time="1:00 PM")
        self.assertEqual(self.diff(state, [cleared]), [])
        self.assertNotIn("Ada", state["seen"])
        # a later distinct inbound still alerts, and a new badge is not blocked
        hits = self.diff(state, [chat(preview="ping", unread=1, time="1:04 PM")])
        self.assertEqual([h["reason"] for h in hits], ["badge"])

    def test_muted_and_denied_are_baselined_but_not_alerted(self):
        state = {"seen": {}}
        muted = chat(preview="secret", unread=4, muted=True)
        self.assertEqual(self.diff(state, [muted]), [])
        self.assertNotIn("Ada", state["seen"])
        self.assertEqual(state["baseline"]["Ada"]["preview"], "secret")
        # unmuting with the badge still up alerts
        hits = self.diff(state, [chat(preview="secret", unread=4, muted=False)])
        self.assertEqual([h["reason"] for h in hits], ["badge"])

        cfg = {"denylist": ["Ada"], "denylist_ids": {}}
        state = {"seen": {}, "baseline": {"Ada": daemon.chat_snapshot(chat(preview="old"))}}
        denied = chat(preview="new note", unread=0, time="2:00 PM")
        self.assertEqual(self.diff(state, [denied], cfg=cfg), [])
        self.assertEqual(state["baseline"]["Ada"]["preview"], "new note")

    def test_from_me_flip_alone_is_not_activity(self):
        state = {"seen": {}, "baseline": {"Ada": daemon.chat_snapshot(chat(preview="hi", time="1:00 PM", from_me=True))}}
        flipped = chat(preview="hi", time="1:00 PM", from_me=False, unread=0)
        self.assertFalse(daemon.inbound_read_activity(state["baseline"]["Ada"], flipped))
        self.assertEqual(self.diff(state, [flipped]), [])

    def test_empty_who_skipped(self):
        state = {"seen": {}}
        self.assertEqual(self.diff(state, [chat(who="")]), [])
        self.assertEqual(state["baseline"], {})

    def test_two_chats_badge_and_preview(self):
        state = {
            "seen": {},
            "baseline": {"Ada": daemon.chat_snapshot(chat(preview="Hello", time="1:00 PM"))},
        }
        rows = [
            chat(preview="reply", time="1:01 PM", unread=0),
            chat(who="Bea", preview="yo", unread=1, time="1:01 PM", chat_id="2@c.us"),
        ]
        hits = self.diff(state, rows)
        self.assertEqual([(h["who"], h["reason"]) for h in hits], [("Ada", "preview"), ("Bea", "badge")])


class TickWebhook(unittest.TestCase):
    def test_preview_hit_uses_unread_event(self):
        calls = []

        def fake_scrape(cfg=None):
            return {
                "chats": [
                    chat(preview="On my way", time="1:01 PM", unread=0, from_me=False),
                ]
            }

        def fake_post(cfg, payload):
            calls.append(payload)
            return True

        state = {
            "seen": {},
            "baseline": {"Ada": daemon.chat_snapshot(chat(preview="Hello", time="1:00 PM", from_me=True))},
        }
        cfg = {"webhook_url": "http://example", "webhook_key": "k", "denylist": [], "denylist_ids": {}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(daemon, "STATE", root / "state.json"),
                mock.patch.object(daemon, "OPEN_CASES", root / "open_cases.json"),
                mock.patch.object(daemon, "GATE_SENT", root / "gate_sent.json"),
                mock.patch.object(daemon, "scrape", fake_scrape),
                mock.patch.object(daemon, "post_webhook", fake_post),
                mock.patch.object(daemon, "log"),
            ):
                daemon.tick(cfg, state)
            saved = json.loads((root / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(calls[0]["event"], "unread")
        self.assertEqual(calls[0]["chats"][0]["reason"], "preview")
        self.assertEqual(calls[0]["chats"][0]["unread"], 0)
        self.assertEqual(saved["baseline"]["Ada"]["preview"], "On my way")
        # no second post for the same row
        calls.clear()

        def same_scrape(cfg=None):
            return {"chats": [chat(preview="On my way", time="1:01 PM", unread=0, from_me=False)]}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(daemon, "STATE", root / "state.json"),
                mock.patch.object(daemon, "OPEN_CASES", root / "open_cases.json"),
                mock.patch.object(daemon, "GATE_SENT", root / "gate_sent.json"),
                mock.patch.object(daemon, "scrape", same_scrape),
                mock.patch.object(daemon, "post_webhook", fake_post),
                mock.patch.object(daemon, "log"),
            ):
                daemon.tick(cfg, state)
        self.assertEqual(calls, [])


class GateSent(unittest.TestCase):
    def test_prefix_match_unchanged_for_outbound(self):
        gs = {"Ada": {"preview": "Hello", "until": 50}}
        self.assertTrue(daemon.gate_sent_suppresses("Ada", "Hello there", 10, gs))
        self.assertTrue(daemon.stamped_outbound("Ada", "Hello", 10, gs))
        self.assertFalse(daemon.stamped_outbound("Ada", "Hello there", 10, gs))
        self.assertFalse(daemon.gate_sent_suppresses("Ada", "Hello", 50, gs))


if __name__ == "__main__":
    unittest.main()
