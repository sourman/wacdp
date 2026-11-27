"""Wedge alerts: prompt on each outage, repeat while down, reset on recovery."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import daemon


class Wedge(unittest.TestCase):
    def run_ticks(self, results, times):
        calls = []
        state = {"seen": {}}
        cfg = {"webhook_url": "http://example", "webhook_key": "k", "denylist_ids": {}}
        it = iter(results)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(daemon, "STATE", root / "state.json"),
                mock.patch.object(daemon, "OPEN_CASES", root / "open_cases.json"),
                mock.patch.object(daemon, "GATE_SENT", root / "gate_sent.json"),
                mock.patch.object(daemon, "scrape", lambda cfg=None: next(it)),
                mock.patch.object(daemon, "post_webhook", lambda c, p: calls.append((now[0], p)) or True),
                mock.patch.object(daemon, "log"),
                mock.patch.object(daemon.time, "time", lambda: now[0]),
            ):
                now = [0.0]
                for t in times:
                    now[0] = t
                    daemon.tick(cfg, state)
        return [(t, p) for t, p in calls if p["event"] == "wedge"]

    def test_new_outage_within_hour_alerts(self):
        down = {"error": "exception"}
        up = {"chats": []}
        # down at 1000, up at 1020, down again at 2000 (old code: suppressed for 1h)
        w = self.run_ticks([down, up, down], [1000, 1020, 2000])
        self.assertEqual([t for t, _ in w], [1000, 2000])

    def test_repeats_every_30_min_while_down(self):
        down = {"error": "exception"}
        times = [0, 20, 1799, 1800, 1820, 3600]
        w = self.run_ticks([down] * len(times), times)
        self.assertEqual([t for t, _ in w], [0, 1800, 3600])
        self.assertEqual(w[-1][1]["down_for_s"], 3600)

    def test_resume_after_pause_alerts_with_gap(self):
        down = {"error": "exception"}
        # alert at 0, process stalled until 5h later: repeat is due, gap reported
        w = self.run_ticks([down, down], [0, 18000])
        self.assertEqual(len(w), 2)
        self.assertEqual(w[1][1]["tick_gap_s"], 18000)


if __name__ == "__main__":
    unittest.main()
