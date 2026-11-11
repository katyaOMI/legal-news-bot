import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from bot.delivery import delivery_day, next_window, send_daily


class DailyWindowTests(unittest.TestCase):
    def setUp(self):
        self.at=datetime(2026,11,11,10,tzinfo=timezone.utc)

    def test_moscow_window_every_day_and_exclusive_end(self):
        self.assertIsNone(delivery_day(self.at-timedelta(seconds=1)))
        self.assertEqual(delivery_day(self.at).isoformat(),'2026-11-11')
        self.assertEqual(delivery_day(self.at+timedelta(minutes=14,seconds=59)),self.at.date())
        self.assertIsNone(delivery_day(self.at+timedelta(minutes=15)))
        self.assertIsNotNone(delivery_day(self.at+timedelta(days=3)))
        self.assertEqual(next_window(self.at+timedelta(hours=8)).strftime('%Y-%m-%d %H:%M'),'2026-11-12 13:00')

    def test_no_database_or_telegram_access_outside_window(self):
        for delta in [-1,15,60,8*60]:
            conn,send=Mock(),Mock()
            send_daily(conn,send,Mock(),now=self.at+timedelta(minutes=delta))
            conn.execute.assert_not_called()
            send.assert_not_called()

    def test_parallel_sender_does_not_send(self):
        conn=Mock()
        conn.execute.return_value.fetchone.return_value=(False,)
        send=Mock()
        send_daily(conn,send,Mock(),now=self.at)
        send.assert_not_called()

    def test_waits_for_queue_reclassification(self):
        conn=Mock()
        conn.execute.return_value.fetchone.side_effect=[(True,),(True,)]
        send=Mock()
        send_daily(conn,send,Mock(),now=self.at)
        send.assert_not_called()
        self.assertIn('pg_advisory_unlock',conn.execute.call_args[0][0])


if __name__=='__main__': unittest.main()
