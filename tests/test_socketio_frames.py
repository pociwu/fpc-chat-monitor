import importlib.util
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / "fpc_watch_ui_login_telegram_v2026.08.31.2.py"
SPEC = importlib.util.spec_from_file_location("watcher", SOURCE)
watcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watcher)


class SocketIoFrameTests(unittest.TestCase):
    def test_local_verification_can_force_disable_telegram(self):
        cfg = {"telegram": {"enabled": True, "bot_token": "secret", "chat_id": "123"}}

        with patch.dict(os.environ, {"FPC_DISABLE_TELEGRAM": "1"}):
            forwarder = watcher.TelegramForwarder(cfg)

        self.assertFalse(forwarder.enabled)

    def test_telegram_read_timeout_is_not_replayed(self):
        import requests

        forwarder = watcher.TelegramForwarder({
            "telegram": {
                "enabled": True,
                "bot_token": "test-token",
                "chat_id": "test-chat",
                "retry": 3,
                "rate_limit_ms": 0,
            }
        })

        with patch(
            "requests.post",
            side_effect=requests.exceptions.ReadTimeout("response was lost"),
        ) as post:
            self.assertTrue(forwarder.send_text("one message"))

        self.assertEqual(post.call_count, 1)

    def test_telegram_preconnect_failure_is_not_reported_as_sent(self):
        import requests

        forwarder = watcher.TelegramForwarder({
            "telegram": {
                "enabled": True,
                "bot_token": "test-token",
                "chat_id": "test-chat",
                "retry": 1,
                "rate_limit_ms": 0,
            }
        })

        with patch(
            "requests.post",
            side_effect=requests.exceptions.ConnectionError(
                "Failed to establish a new connection: connection refused"
            ),
        ) as post, patch.object(watcher.time, "sleep"):
            self.assertFalse(forwarder.send_text("one message"))

        self.assertEqual(post.call_count, 1)

    def test_telegram_chunked_response_error_is_not_replayed(self):
        import requests

        forwarder = watcher.TelegramForwarder({
            "telegram": {
                "enabled": True, "bot_token": "test-token",
                "chat_id": "test-chat", "retry": 3, "rate_limit_ms": 0,
            }
        })
        with patch(
            "requests.post",
            side_effect=requests.exceptions.ChunkedEncodingError("response truncated"),
        ) as post:
            self.assertTrue(forwarder.send_text("one message"))
        self.assertEqual(post.call_count, 1)

    def test_telegram_file_chunked_response_error_is_not_replayed(self):
        import requests

        forwarder = watcher.TelegramForwarder({
            "telegram": {
                "enabled": True, "bot_token": "test-token",
                "chat_id": "test-chat", "retry": 3, "rate_limit_ms": 0,
            }
        })
        with tempfile.TemporaryDirectory() as temp_dir:
            attachment = Path(temp_dir) / "photo.png"
            attachment.write_bytes(b"photo")
            with patch(
                "requests.post",
                side_effect=requests.exceptions.ChunkedEncodingError("response truncated"),
            ) as post:
                self.assertTrue(forwarder.send_file(attachment))
        self.assertEqual(post.call_count, 1)

    def test_telegram_http_500_is_not_replayed(self):
        forwarder = watcher.TelegramForwarder({
            "telegram": {
                "enabled": True, "bot_token": "test-token",
                "chat_id": "test-chat", "retry": 3, "rate_limit_ms": 0,
            }
        })
        response = Mock(ok=False, status_code=500, text="server error")
        with patch("requests.post", return_value=response) as post:
            self.assertTrue(forwarder.send_text("one message"))
        self.assertEqual(post.call_count, 1)

    def test_telegram_file_http_500_is_not_replayed(self):
        forwarder = watcher.TelegramForwarder({
            "telegram": {
                "enabled": True, "bot_token": "test-token",
                "chat_id": "test-chat", "retry": 3, "rate_limit_ms": 0,
            }
        })
        response = Mock(ok=False, status_code=500, text="server error")
        with tempfile.TemporaryDirectory() as temp_dir:
            attachment = Path(temp_dir) / "photo.png"
            attachment.write_bytes(b"photo")
            with patch("requests.post", return_value=response) as post:
                self.assertTrue(forwarder.send_file(attachment))
        self.assertEqual(post.call_count, 1)

    def test_dense_identical_photo_events_remain_distinct(self):
        response = {
            "data": {
                "cid": "channel-42",
                "messages": [
                    {"messageId": "photo-1", "message": "黃紹瑄傳送了照片。"},
                    {"messageId": "photo-2", "message": "黃紹瑄傳送了照片。"},
                ],
            }
        }

        candidates = watcher._network_message_candidates(
            response, "ws:test", {"channel-42": "台塑群組網"}
        )

        self.assertEqual([item["message_id"] for item in candidates], ["photo-1", "photo-2"])
        keys = [watcher._message_delivery_key(item) for item in candidates]
        self.assertEqual(len(set(keys)), 2)

    def test_same_message_id_ignores_transport_specific_metadata(self):
        passive = {
            "group": "CZ2拉晶工程 (34)",
            "message_id": "message-42",
            "text": "主管好: 8/31 早班5員加班對應生產作業",
            "time": "上午 11:32",
            "event_token": "sidebar-event",
            "badge": "1",
        }
        backfill = {
            "group": "CZ2拉晶工程 (34)",
            "message_id": "message-42",
            "text": passive["text"],
            "time": passive["time"],
        }

        self.assertEqual(
            watcher._message_delivery_key(passive),
            watcher._message_delivery_key(backfill),
        )

    def test_same_message_id_ignores_display_member_count(self):
        without_count = {"group": "CZ2拉晶工程", "message_id": "message-42"}
        with_count = {"group": "CZ2拉晶工程 (34)", "message_id": "message-42"}

        self.assertEqual(
            watcher._message_delivery_key(without_count),
            watcher._message_delivery_key(with_count),
        )

    def test_backfill_candidate_matches_sidebar_member_count_suffix(self):
        candidate = {
            "group": "CZ2拉晶工程",
            "message_id": "message-42",
            "text": "完整訊息",
        }

        matched = watcher._candidate_for_group(
            [candidate], "CZ2拉晶工程 (34)"
        )

        self.assertIs(matched, candidate)

    def test_same_message_id_in_different_groups_remains_distinct(self):
        first = {"group": "CZ2拉晶工程", "message_id": "message-42"}
        second = {"group": "結晶二課拉晶領班聯絡", "message_id": "message-42"}

        self.assertNotEqual(
            watcher._message_delivery_key(first),
            watcher._message_delivery_key(second),
        )

    def test_no_id_event_token_is_not_a_second_delivery_identity(self):
        first = {
            "group": "CZ2拉晶工程 (34)",
            "text": "主管好: 8/31 早班5員加班對應生產作業",
            "time": "上午 11:32",
            "badge": "1",
            "event_token": "observer-a",
        }
        second = dict(first, event_token="observer-b")

        self.assertEqual(
            watcher._message_delivery_key(first),
            watcher._message_delivery_key(second),
        )

    def test_no_id_dense_identical_messages_use_badge_as_identity(self):
        first = {
            "group": "CZ2拉晶工程 (34)",
            "text": "黃紹瑄傳送了照片。",
            "time": "上午 11:32",
            "badge": "1",
        }
        second = dict(first, badge="2")

        self.assertNotEqual(
            watcher._message_delivery_key(first),
            watcher._message_delivery_key(second),
        )

    def test_telegram_delivery_seam_calls_send_text_once_for_same_identity(self):
        tg = Mock()
        tg.send_text.return_value = True
        sent = set()

        def already_sent(kind, group, delivery_key):
            return (kind, group, delivery_key) in sent

        def mark_sent(kind, group, delivery_key):
            sent.add((kind, group, delivery_key))

        item = {"group": "CZ2拉晶工程 (34)", "message_id": "message-42"}
        delivery_key = watcher._message_delivery_key(item)
        args = {
            "tg": tg,
            "group": item["group"],
            "delivery_key": delivery_key,
            "styled": "styled",
            "plain": "plain",
            "should_send_text": True,
            "saved_attachments": [],
            "already_sent": already_sent,
            "mark_sent": mark_sent,
        }

        self.assertEqual(watcher._send_telegram_bundle_once(**args)[0], "sent")
        self.assertEqual(watcher._send_telegram_bundle_once(**args)[0], "duplicate")
        self.assertEqual(tg.send_text.call_count, 1)

    def test_attachment_retry_does_not_resend_successful_text(self):
        tg = Mock()
        tg.send_text.return_value = True
        tg.send_file.side_effect = [False, True]
        sent = set()

        def already_sent(kind, group, delivery_key):
            return (kind, group, delivery_key) in sent

        def mark_sent(kind, group, delivery_key):
            sent.add((kind, group, delivery_key))

        with tempfile.TemporaryDirectory() as temp_dir:
            attachment = Path(temp_dir) / "photo-1.png"
            attachment.write_bytes(b"photo")
            args = {
                "tg": tg,
                "group": "台塑群組網 (13)",
                "delivery_key": "photo-delivery",
                "styled": "styled",
                "plain": "plain",
                "should_send_text": True,
                "saved_attachments": [attachment],
                "already_sent": already_sent,
                "mark_sent": mark_sent,
            }

            self.assertEqual(watcher._send_telegram_bundle_once(**args)[0], "failed")
            self.assertEqual(watcher._send_telegram_bundle_once(**args)[0], "sent")
            self.assertEqual(watcher._send_telegram_bundle_once(**args)[0], "duplicate")
        self.assertEqual(tg.send_text.call_count, 1)
        self.assertEqual(tg.send_file.call_count, 2)

    def test_attachment_component_uses_content_not_temporary_path(self):
        tg = Mock()
        tg.send_file.return_value = True
        sent = set()

        def already_sent(kind, group, delivery_key):
            return (kind, group, delivery_key) in sent

        def mark_sent(kind, group, delivery_key):
            sent.add((kind, group, delivery_key))

        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "day-one.png"
            second = Path(temp_dir) / "day-two.png"
            first.write_bytes(b"same-photo")
            second.write_bytes(b"same-photo")
            common = {
                "tg": tg,
                "group": "台塑群組網",
                "delivery_key": "same-delivery",
                "styled": "",
                "plain": "",
                "should_send_text": False,
                "already_sent": already_sent,
                "mark_sent": mark_sent,
            }
            self.assertEqual(watcher._send_telegram_bundle_once(
                **common, saved_attachments=[first], attachments_complete=False
            )[0], "failed")
            self.assertEqual(watcher._send_telegram_bundle_once(
                **common, saved_attachments=[second], attachments_complete=True
            )[0], "sent")

        self.assertEqual(tg.send_file.call_count, 1)

    def test_failed_attachment_download_does_not_mark_bundle_complete(self):
        tg = Mock()
        tg.send_text.return_value = True
        sent = set()

        def already_sent(kind, group, delivery_key):
            return (kind, group, delivery_key) in sent

        def mark_sent(kind, group, delivery_key):
            sent.add((kind, group, delivery_key))

        status, _ = watcher._send_telegram_bundle_once(
            tg=tg,
            group="台塑群組網 (13)",
            delivery_key="attachment-download",
            styled="styled",
            plain="plain",
            should_send_text=True,
            saved_attachments=[],
            already_sent=already_sent,
            mark_sent=mark_sent,
            attachments_complete=False,
        )

        self.assertEqual(status, "failed")
        self.assertNotIn(("tg", "台塑群組網 (13)", "attachment-download"), sent)

    def test_backfill_cannot_claim_pending_after_passive_delivery(self):
        pending = watcher.PendingPreviewBuffer()
        event = pending.add({
            "group": "CZ2拉晶工程 (34)",
            "group_key": "CZ2拉晶工程",
            "preview": "主管好: 8/31 早班5員加班對應生產作業",
            "time": "上午 11:32",
            "event_token": "sidebar-event",
            "badge": "1",
        }, now=0)

        self.assertIs(pending.pop_for_group("CZ2拉晶工程"), event)
        claimed = watcher._claim_backfill_candidate(
            pending,
            event,
            {"group": event["group"], "message_id": "message-42", "text": event["preview"]},
        )

        self.assertIsNone(claimed)

    def test_backfill_claim_inherits_sidebar_delivery_metadata(self):
        pending = watcher.PendingPreviewBuffer()
        event = pending.add({
            "group": "CZ2拉晶工程 (34)",
            "group_key": "CZ2拉晶工程",
            "preview": "主管好: 8/31 早班5員加班對應生產作業",
            "time": "上午 11:32",
            "event_token": "sidebar-event",
            "badge": "1",
        }, now=0)

        claimed = watcher._claim_backfill_candidate(
            pending,
            event,
            {"group": event["group"], "message_id": "message-42", "text": event["preview"]},
        )

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["event_token"], "sidebar-event")
        self.assertEqual(claimed["badge"], "1")
        self.assertFalse(pending)

    def test_duplicate_server_message_does_not_consume_next_pending_event(self):
        pending = watcher.PendingPreviewBuffer()
        first = pending.add({
            "group": "台塑群組網 (13)",
            "group_key": "台塑群組網",
            "preview": "黃紹瑄傳送了照片。",
            "time": "上午 11:32",
            "badge": "1",
        }, now=0)
        second = pending.add({
            "group": "台塑群組網 (13)",
            "group_key": "台塑群組網",
            "preview": "黃紹瑄傳送了照片。",
            "time": "上午 11:32",
            "badge": "2",
        }, now=0.1)
        reservations = watcher.MessageIngressReservations()
        message_one = {
            "group": "台塑群組網",
            "cid": "channel-42",
            "message_id": "photo-1",
            "text": "黃紹瑄傳送了照片。",
        }

        claimed = watcher._claim_backfill_candidate(
            pending, first, message_one, reservations
        )
        duplicate = watcher._claim_passive_candidate(
            pending, "台塑群組網", message_one, reservations
        )

        self.assertEqual(claimed["message_id"], "photo-1")
        self.assertIsNone(duplicate)
        self.assertTrue(pending.contains(second))

        message_two = dict(message_one, message_id="photo-2")
        next_claim = watcher._claim_passive_candidate(
            pending, "台塑群組網", message_two, reservations
        )
        self.assertEqual(next_claim["message_id"], "photo-2")
        self.assertFalse(pending)

    def test_backfill_batch_pairs_two_dense_pending_events_in_order(self):
        pending = watcher.PendingPreviewBuffer()
        for badge in ("1", "2"):
            pending.add({
                "group": "台塑群組網 (13)",
                "group_key": "台塑群組網",
                "preview": "黃紹瑄傳送了照片。",
                "time": "上午 11:32",
                "badge": badge,
            }, now=float(badge) / 10)
        candidates = [
            {
                "group": "台塑群組網",
                "cid": "channel-42",
                "message_id": message_id,
                "text": "黃紹瑄傳送了照片。",
            }
            for message_id in ("photo-1", "photo-2")
        ]

        claimed = watcher._claim_backfill_batch(
            pending,
            "台塑群組網",
            candidates,
            watcher.MessageIngressReservations(),
        )

        self.assertEqual(
            [item["message_id"] for item in claimed], ["photo-1", "photo-2"]
        )
        self.assertFalse(pending)

    def test_backfill_batch_uses_newest_candidate_not_history(self):
        pending = watcher.PendingPreviewBuffer()
        target = pending.add({
            "group": "台塑群組網 (13)",
            "group_key": "台塑群組網",
            "preview": "最新訊息",
            "time": "上午 11:32",
            "badge": "1",
        }, now=0)
        candidates = [
            {"group": "台塑群組網", "message_id": "old", "text": "歷史訊息"},
            {"group": "台塑群組網", "message_id": "new", "text": "最新訊息"},
        ]

        claimed = watcher._claim_backfill_batch(
            pending,
            "台塑群組網",
            candidates,
            watcher.MessageIngressReservations(),
            eligible_pending=[target],
        )

        self.assertEqual([item["message_id"] for item in claimed], ["new"])

    def test_backfill_snapshot_does_not_consume_pending_added_during_fetch(self):
        pending = watcher.PendingPreviewBuffer()
        first = pending.add({
            "group": "台塑群組網 (13)",
            "group_key": "台塑群組網",
            "preview": "第一則",
            "time": "上午 11:31",
            "badge": "1",
        }, now=0)
        eligible = pending.snapshot_for_group("台塑群組網")
        second = pending.add({
            "group": "台塑群組網 (13)",
            "group_key": "台塑群組網",
            "preview": "第二則",
            "time": "上午 11:32",
            "badge": "2",
        }, now=0.1)

        claimed = watcher._claim_backfill_batch(
            pending,
            "台塑群組網",
            [{"group": "台塑群組網", "message_id": "m1", "text": "第一則"}],
            watcher.MessageIngressReservations(),
            eligible_pending=eligible,
        )

        self.assertEqual([item["message_id"] for item in claimed], ["m1"])
        self.assertFalse(pending.contains(first))
        self.assertTrue(pending.contains(second))

    def test_backfill_does_not_bind_newer_response_to_older_snapshot(self):
        pending = watcher.PendingPreviewBuffer()
        first = pending.add({
            "group": "台塑群組網 (13)",
            "group_key": "台塑群組網",
            "preview": "第一則",
            "time": "上午 11:31",
            "badge": "1",
        }, now=0)
        eligible = pending.snapshot_for_group("台塑群組網")
        second = pending.add({
            "group": "台塑群組網 (13)",
            "group_key": "台塑群組網",
            "preview": "第二則",
            "time": "上午 11:32",
            "badge": "2",
        }, now=0.1)

        claimed = watcher._claim_backfill_batch(
            pending,
            "台塑群組網",
            [{"group": "台塑群組網", "message_id": "m2", "text": "第二則"}],
            watcher.MessageIngressReservations(),
            eligible_pending=eligible,
        )

        self.assertEqual(claimed, [])
        self.assertTrue(pending.contains(first))
        self.assertTrue(pending.contains(second))

    def test_backfill_tail_alignment_claims_old_event_when_response_has_both(self):
        pending = watcher.PendingPreviewBuffer()
        first = pending.add({
            "group": "台塑群組網 (13)", "group_key": "台塑群組網",
            "preview": "第一則", "time": "上午 11:31", "badge": "1",
        }, now=0)
        eligible = pending.snapshot_for_group("台塑群組網")
        second = pending.add({
            "group": "台塑群組網 (13)", "group_key": "台塑群組網",
            "preview": "第二則", "time": "上午 11:32", "badge": "2",
        }, now=0.1)

        claimed = watcher._claim_backfill_batch(
            pending,
            "台塑群組網",
            [
                {"group": "台塑群組網", "message_id": "m1", "text": "第一則"},
                {"group": "台塑群組網", "message_id": "m2", "text": "第二則"},
            ],
            watcher.MessageIngressReservations(),
            eligible_pending=eligible,
        )

        self.assertEqual([item["message_id"] for item in claimed], ["m1"])
        self.assertFalse(pending.contains(first))
        self.assertTrue(pending.contains(second))

    def test_backfill_batch_deduplicates_mixed_id_transport_copies_before_alignment(self):
        for id_first in (True, False):
            with self.subTest(id_first=id_first):
                pending = watcher.PendingPreviewBuffer()
                first = pending.add({
                    "group": "群組", "group_key": "群組", "preview": "C",
                    "badge": "2", "preview_uncertain": True,
                }, now=0)
                second = pending.add({
                    "group": "群組", "group_key": "群組", "preview": "C",
                    "badge": "3",
                }, now=0.1)
                with_id = {
                    "group": "群組", "cid": "channel-42",
                    "message_id": "m-c", "text": "C",
                }
                without_id = {
                    "group": "群組", "cid": "channel-42", "text": "C",
                }
                candidates = (
                    [with_id, without_id] if id_first else [without_id, with_id]
                )

                claimed = watcher._claim_backfill_batch(
                    pending, "群組", candidates,
                    watcher.MessageIngressReservations(),
                )

                self.assertEqual(len(claimed), 1)
                self.assertTrue(pending.contains(first))
                self.assertFalse(pending.contains(second))
                self.assertEqual(
                    watcher._preview_fallback_message(first)["text"], ""
                )

    def test_deferred_candidate_dedup_keeps_distinct_ids(self):
        duplicate_a = {
            "group": "channel-42", "cid": "channel-42",
            "message_id": "m-c", "text": "C",
        }
        duplicate_b = {
            "group": "channel-42", "cid": "channel-42", "text": "C",
        }
        distinct = dict(duplicate_a, message_id="m-d")

        unique = watcher._unique_message_candidates([
            duplicate_a, duplicate_b, distinct
        ])

        self.assertEqual([item.get("message_id") for item in unique], ["m-c", "m-d"])

    def test_attachment_only_backfill_claims_matching_photo_preview(self):
        pending = watcher.PendingPreviewBuffer()
        event = pending.add({
            "group": "台塑群組網 (13)", "group_key": "台塑群組網",
            "preview": "黃紹瑄傳送了照片。", "time": "上午 11:32", "badge": "1",
        }, now=0)
        candidate = {
            "group": "台塑群組網",
            "message_id": "photo-1",
            "text": "",
            "attachments": ["https://example.test/photo-1.png"],
        }

        claimed = watcher._claim_backfill_batch(
            pending,
            "台塑群組網",
            [candidate],
            watcher.MessageIngressReservations(),
            eligible_pending=[event],
        )

        self.assertEqual([item["message_id"] for item in claimed], ["photo-1"])
        self.assertEqual(claimed[0]["attachments"], candidate["attachments"])

    def test_newer_identical_photo_does_not_slide_into_older_fetch(self):
        pending = watcher.PendingPreviewBuffer()
        first = pending.add({
            "group": "台塑群組網", "group_key": "台塑群組網",
            "preview": "黃紹瑄傳送了照片。", "badge": "1",
        }, now=0)
        eligible = [first]
        second = pending.add({
            "group": "台塑群組網", "group_key": "台塑群組網",
            "preview": "黃紹瑄傳送了照片。", "badge": "2",
        }, now=0.1)

        claimed = watcher._claim_backfill_batch(
            pending,
            "台塑群組網",
            [{
                "group": "台塑群組網", "message_id": "photo-2", "text": "",
                "attachments": ["https://example.test/photo-2.png"],
            }],
            watcher.MessageIngressReservations(),
            eligible_pending=eligible,
        )

        self.assertEqual(claimed, [])
        self.assertTrue(pending.contains(first))
        self.assertTrue(pending.contains(second))

    def test_same_no_id_candidate_cannot_claim_two_pending_events(self):
        pending = watcher.PendingPreviewBuffer()
        for badge in ("1", "2"):
            pending.add({
                "group": "台塑群組網 (13)",
                "group_key": "台塑群組網",
                "preview": "同一則完整訊息",
                "time": "上午 11:32",
                "badge": badge,
            }, now=float(badge) / 10)
        reservations = watcher.MessageIngressReservations()
        websocket_copy = {
            "group": "台塑群組網",
            "cid": "channel-42",
            "text": "同一則完整訊息",
            "sender": "王小明",
            "time": "上午 11:32",
        }
        http_copy = dict(websocket_copy)
        http_copy.pop("cid")
        http_copy.pop("sender")

        first = watcher._claim_backfill_batch(
            pending, "台塑群組網", [websocket_copy], reservations
        )
        duplicate = watcher._claim_backfill_batch(
            pending, "台塑群組網", [http_copy], reservations
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(duplicate, [])
        self.assertEqual(pending.count_for_group("台塑群組網"), 1)

    def test_message_id_and_no_id_transport_copy_share_reservation(self):
        for id_first in (True, False):
            with self.subTest(id_first=id_first):
                pending = watcher.PendingPreviewBuffer()
                for badge in ("1", "2"):
                    pending.add({
                        "group": "台塑群組網", "group_key": "台塑群組網",
                        "preview": "同一則完整訊息", "badge": badge,
                    }, now=float(badge))
                reservations = watcher.MessageIngressReservations()
                with_id = {
                    "group": "台塑群組網", "cid": "channel-42",
                    "message_id": "message-42", "text": "同一則完整訊息",
                    "time": "上午 11:32",
                }
                without_id = {
                    "group": "台塑群組網", "cid": "channel-42",
                    "text": "同一則完整訊息", "time": "",
                }
                first, duplicate = (
                    (with_id, without_id) if id_first else (without_id, with_id)
                )

                self.assertIsNotNone(watcher._claim_passive_candidate(
                    pending, "台塑群組網", first, reservations
                ))
                self.assertIsNone(watcher._claim_passive_candidate(
                    pending, "台塑群組網", duplicate, reservations
                ))
                self.assertEqual(pending.count_for_group("台塑群組網"), 1)

    def test_transport_whitespace_variants_share_identity_and_reservation(self):
        multiline = {
            "group": "台塑群組網", "cid": "channel-42",
            "message_id": "message-42", "text": "同一則\n完整訊息",
            "time": "上午 11:32", "badge": "1",
        }
        single_line = {
            "group": "台塑群組網", "cid": "channel-42",
            "text": "同一則 完整訊息", "time": "", "badge": "1",
        }
        reservations = watcher.MessageIngressReservations()

        self.assertTrue(reservations.reserve(multiline))
        self.assertTrue(reservations.contains(single_line))
        self.assertEqual(
            watcher._message_delivery_key(dict(multiline, message_id="")),
            watcher._message_delivery_key(dict(single_line, time="上午 11:32")),
        )

    def test_failed_no_id_delivery_keeps_candidate_from_consuming_next_badge(self):
        pending = watcher.PendingPreviewBuffer()
        for badge in ("1", "2"):
            pending.add({
                "group": "台塑群組網", "group_key": "台塑群組網",
                "preview": "照片", "badge": badge,
            }, now=float(badge))
        reservations = watcher.MessageIngressReservations()
        candidate = {
            "group": "台塑群組網", "text": "照片", "time": "上午 11:32",
        }

        self.assertIsNotNone(watcher._claim_passive_candidate(
            pending, "台塑群組網", candidate, reservations
        ))
        # Production deliberately retains this reservation after a failed
        # Telegram bundle so successful components cannot replay under badge 2.
        self.assertIsNone(watcher._claim_passive_candidate(
            pending, "台塑群組網", candidate, reservations
        ))
        self.assertEqual(pending.count_for_group("台塑群組網"), 1)

    def test_no_id_attachment_identity_ignores_transport_filename(self):
        url = "https://example.test/files/photo-1.png"
        websocket_copy = {
            "group": "台塑群組網", "text": "", "time": "上午 11:32",
            "badge": "1", "attachments": [url],
        }
        http_copy = dict(
            websocket_copy,
            attachments=[{"url": url, "name": "original-photo.png"}],
        )

        self.assertEqual(
            watcher.MessageIngressReservations._aliases(websocket_copy),
            watcher.MessageIngressReservations._aliases(http_copy),
        )
        self.assertEqual(
            watcher._message_delivery_key(websocket_copy),
            watcher._message_delivery_key(http_copy),
        )

    def test_no_id_attachment_identity_matches_relative_and_absolute_url(self):
        relative = {
            "group": "台塑群組網", "text": "", "time": "上午 11:32",
            "badge": "1", "attachments": ["/files/photo-1.png?token=old"],
        }
        absolute = dict(
            relative,
            attachments=[{
                "url": "https://chat.example.test/files/photo-1.png?token=new",
                "name": "photo-1.png",
            }],
        )

        self.assertEqual(
            watcher.MessageIngressReservations._aliases(relative),
            watcher.MessageIngressReservations._aliases(absolute),
        )
        self.assertEqual(
            watcher._message_delivery_key(relative),
            watcher._message_delivery_key(absolute),
        )

    def test_sent_state_uses_canonical_group_name(self):
        first = watcher.App._sent_key(None, "CZ2拉晶工程", "delivery-key")
        second = watcher.App._sent_key(None, "CZ2拉晶工程 (34)", "delivery-key")

        self.assertEqual(first, second)

    def test_attachment_url_can_be_a_direct_string(self):
        self.assertEqual(
            watcher._attachment_urls(["https://example.test/files/photo-1.png"]),
            [("https://example.test/files/photo-1.png", "")],
        )

    def test_pending_preview_buffer_does_not_overwrite_same_group(self):
        pending = watcher.PendingPreviewBuffer()
        pending.add({"group": "台塑群組網 (13)", "group_key": "台塑群組網",
                     "preview": "黃紹瑄傳送了照片。", "time": "下午 10:13",
                     "badge": "1"}, now=0)
        pending.add({"group": "台塑群組網 (14)", "group_key": "台塑群組網",
                     "preview": "黃紹瑄傳送了照片。", "time": "下午 10:13",
                     "badge": "2"}, now=0.1)

        first = pending.pop_for_group("台塑群組網")

        self.assertEqual(first["badge"], "1")

        expired = pending.pop_expired(now=9, max_age=8)

        self.assertEqual(len(expired), 1)
        self.assertEqual([item["badge"] for item in expired], ["2"])

    def test_same_positive_badge_row_rerender_updates_one_pending_event(self):
        pending = watcher.PendingPreviewBuffer()
        first = pending.add({
            "group": "CZ2拉晶工程 (34)",
            "group_key": "CZ2拉晶工程",
            "preview": "主管好: 8/31 早班5員加班對應生產作業",
            "time": "",
            "badge": "1",
            "event_token": "without-time",
        }, now=0)
        settled = pending.add({
            "group": "CZ2拉晶工程 (34)",
            "group_key": "CZ2拉晶工程",
            "preview": "主管好: 8/31 早班5員加班對應生產作業",
            "time": "上午 11:32",
            "badge": "1",
            "event_token": "with-time",
        }, now=0.1)

        self.assertIs(settled, first)
        self.assertEqual(settled["time"], "上午 11:32")
        self.assertEqual(pending.count_for_group("CZ2拉晶工程"), 1)

    def test_row_rerender_after_full_claim_does_not_create_preview_fallback(self):
        pending = watcher.PendingPreviewBuffer()
        base = time.time()
        first = pending.add({
            "group": "CZ2拉晶工程 (34)",
            "group_key": "CZ2拉晶工程",
            "preview": "主管好: 8/31 早班5員加班對應生產作業",
            "time": "",
            "badge": "1",
            "event_token": "without-time",
        }, now=base)
        claimed = watcher._claim_backfill_candidate(
            pending,
            first,
            {
                "group": "CZ2拉晶工程",
                "message_id": "message-42",
                "text": first["preview"],
            },
            watcher.MessageIngressReservations(),
        )

        settled = pending.add({
            "group": "CZ2拉晶工程 (34)",
            "group_key": "CZ2拉晶工程",
            "preview": first["preview"],
            "time": "上午 11:32",
            "badge": "1",
            "event_token": "with-time",
        }, now=base + 0.1)

        self.assertEqual(claimed["message_id"], "message-42")
        self.assertIs(settled, first)
        self.assertFalse(pending)
        self.assertEqual(pending.pop_expired(now=base + 9, max_age=8), [])

    def test_badge_jump_creates_one_pending_slot_per_dense_message(self):
        events = watcher._expand_side_preview_event({
            "group": "台塑群組網 (13)",
            "text": "黃紹瑄傳送了照片。",
            "time": "上午 11:32",
            "badge": "2",
            "event_token": "sidebar-snapshot",
            "event_count": 2,
        }, "台塑群組網")

        self.assertEqual([event["badge"] for event in events], ["1", "2"])
        self.assertEqual(len({event["event_token"] for event in events}), 2)

    def test_sidebar_staged_preview_then_badge_emits_once(self):
        reducer = watcher.SidebarSnapshotReducer()
        reducer.observe({
            "group": "群組 (13)", "text": "A", "badge": "1",
        }, now=0)
        reducer.observe({"group": "群組 (13)", "text": "B", "badge": "1"}, now=0.1)
        reducer.observe({"group": "群組 (13)", "text": "B", "badge": "2"}, now=0.4)

        self.assertEqual(reducer.pop_due(now=1.0), [])
        events = reducer.pop_due(now=1.2)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["text"], "B")
        self.assertEqual(events[0]["badge"], "2")
        self.assertEqual(events[0]["event_count"], 1)
        self.assertEqual(reducer.pop_due(now=2), [])

    def test_sidebar_staged_badge_then_preview_emits_once(self):
        reducer = watcher.SidebarSnapshotReducer()
        reducer.observe({
            "group": "群組", "text": "A", "badge": "1",
        }, now=0)
        reducer.observe({"group": "群組", "text": "A", "badge": "2"}, now=0.1)
        reducer.observe({"group": "群組", "text": "B", "badge": "2"}, now=0.4)

        events = reducer.pop_due(now=1.2)

        self.assertEqual([(e["text"], e["badge"]) for e in events], [("B", "2")])

    def test_sidebar_preview_only_change_waits_for_unread_increase(self):
        reducer = watcher.SidebarSnapshotReducer()
        reducer.observe({
            "group": "群組", "text": "A", "badge": "1",
        }, now=0)
        reducer.observe({"group": "群組", "text": "B", "badge": "1"}, now=0.1)

        self.assertEqual(reducer.pop_due(now=0.8), [])
        updates = reducer.pop_due(now=0.9)
        self.assertEqual([event["type"] for event in updates], ["side_preview_update"])

        reducer.observe({"group": "群組", "text": "B", "badge": "2"}, now=2.0)
        events = reducer.pop_due(now=2.8)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_count"], 1)

    def test_sidebar_dense_same_preview_preserves_badge_jump_count(self):
        reducer = watcher.SidebarSnapshotReducer()
        reducer.observe({
            "group": "群組", "text": "A", "badge": "1",
        }, now=0)
        reducer.observe({"group": "群組", "text": "照片", "badge": "2"}, now=0.1)
        reducer.observe({"group": "群組", "text": "照片", "badge": "3"}, now=0.2)

        events = reducer.pop_due(now=1.0)

        self.assertEqual([event["badge"] for event in events], ["2", "3"])
        self.assertEqual([event["text"] for event in events], ["照片", "照片"])
        self.assertEqual([event["event_count"] for event in events], [1, 1])

    def test_sidebar_dense_distinct_previews_preserve_each_slot(self):
        reducer = watcher.SidebarSnapshotReducer()
        reducer.observe({
            "group": "群組", "text": "A", "badge": "1",
        }, now=0)
        reducer.observe({"group": "群組", "text": "B", "badge": "2"}, now=0.1)
        reducer.observe({"group": "群組", "text": "C", "badge": "3"}, now=0.2)

        events = reducer.pop_due(now=1.0)

        self.assertEqual(
            [(event["text"], event["badge"]) for event in events],
            [("B", "2"), ("C", "3")],
        )

    def test_direct_badge_jump_uses_uncertain_slot_only_for_full_backfill(self):
        reducer = watcher.SidebarSnapshotReducer()
        reducer.observe({
            "group": "群組", "text": "A", "badge": "1",
        }, now=0)
        reducer.observe({"group": "群組", "text": "C", "badge": "3"}, now=0.1)
        events = reducer.pop_due(now=1.0)
        self.assertEqual([event["preview_uncertain"] for event in events], [True, False])

        pending = watcher.PendingPreviewBuffer()
        for event in events:
            for expanded in watcher._expand_side_preview_event(event, "群組"):
                pending.add(expanded, now=1.0)
        reservations = watcher.MessageIngressReservations()
        claimed = watcher._claim_backfill_batch(
            pending,
            "群組",
            [
                {"group": "群組", "message_id": "m-b", "text": "B"},
                {"group": "群組", "message_id": "m-c", "text": "C"},
            ],
            reservations,
        )

        self.assertEqual([item["message_id"] for item in claimed], ["m-b", "m-c"])
        uncertain = watcher._expand_side_preview_event(events[0], "群組")[0]
        self.assertEqual(watcher._preview_fallback_message(uncertain)["text"], "")

    def test_badge_first_slot_stays_uncertain_when_next_message_overtakes_preview(self):
        reducer = watcher.SidebarSnapshotReducer()
        reducer.observe({
            "group": "群組", "text": "A", "badge": "1",
        }, now=0)
        reducer.observe({"group": "群組", "text": "A", "badge": "2"}, now=0.1)
        reducer.observe({"group": "群組", "text": "C", "badge": "3"}, now=0.2)

        events = reducer.pop_due(now=1.0)

        self.assertEqual(
            [(event["text"], event["badge"], event["preview_uncertain"])
             for event in events],
            [("A", "2", True), ("C", "3", False)],
        )
        pending = watcher.PendingPreviewBuffer()
        for event in events:
            pending.add(watcher._expand_side_preview_event(event, "群組")[0], now=1.0)
        claimed = watcher._claim_backfill_batch(
            pending,
            "群組",
            [
                {"group": "群組", "message_id": "m-b", "text": "B"},
                {"group": "群組", "message_id": "m-c", "text": "C"},
            ],
            watcher.MessageIngressReservations(),
        )
        self.assertEqual([item["message_id"] for item in claimed], ["m-b", "m-c"])

    def test_sidebar_each_rows_first_snapshot_is_only_a_baseline(self):
        reducer = watcher.SidebarSnapshotReducer()
        reducer.observe({
            "group": "既有群組", "text": "舊訊息", "badge": "4",
        }, now=0)
        reducer.observe({
            "group": "新群組", "text": "新訊息", "badge": "1",
        }, now=0.1)

        self.assertEqual(reducer.pop_due(now=1.0), [])

    def test_sidebar_late_badge_then_preview_updates_one_pending_event(self):
        reducer = watcher.SidebarSnapshotReducer()
        reducer.observe({
            "group": "群組", "text": "A", "badge": "1",
        }, now=0)
        reducer.observe({"group": "群組", "text": "A", "badge": "2"}, now=0.1)
        first_events = reducer.pop_due(now=0.9)
        self.assertEqual([event["type"] for event in first_events], ["side_preview"])

        pending = watcher.PendingPreviewBuffer()
        for event in watcher._expand_side_preview_event(first_events[0], "群組"):
            pending.add(event, now=1.0)

        reducer.observe({"group": "群組", "text": "B", "badge": "2"}, now=1.1)
        updates = reducer.pop_due(now=1.9)
        self.assertEqual([event["type"] for event in updates], ["side_preview_update"])
        self.assertTrue(pending.update_latest("群組", "2", updates[0]))

        self.assertEqual(pending.count_for_group("群組"), 1)
        self.assertEqual(pending.snapshot_for_group("群組")[0]["preview"], "B")
        self.assertTrue(pending.snapshot_for_group("群組")[0]["preview_uncertain"])
        self.assertTrue(pending.snapshot_for_group("群組")[0]["preview_provisional"])

    def test_late_preview_cannot_overwrite_a_certain_previous_message(self):
        pending = watcher.PendingPreviewBuffer()
        certain = pending.add({
            "group": "群組", "group_key": "群組", "preview": "A",
            "badge": "1", "preview_uncertain": False,
        }, now=0)

        updated = pending.update_latest("群組", "1", {
            "group": "群組", "text": "B", "time": "上午 11:32", "badge": "1",
        })

        self.assertFalse(updated)
        self.assertEqual(certain["preview"], "A")

    def test_provisional_preview_refreshes_fallback_deadline_until_invalidation(self):
        pending = watcher.PendingPreviewBuffer()
        item = pending.add({
            "group": "群組", "group_key": "群組", "preview": "Z",
            "badge": "1", "preview_uncertain": True,
        }, now=0)

        self.assertTrue(pending.update_latest(
            "群組", "1",
            {"group": "群組", "text": "B", "time": "上午 11:32", "badge": "1"},
            now=7.9,
        ))
        self.assertEqual(pending.pop_expired(now=8.1, max_age=8), [])
        self.assertTrue(pending.invalidate_provisional("群組", "1"))
        self.assertEqual(watcher._preview_fallback_message(item)["text"], "")

    def test_next_preview_before_badge_cannot_duplicate_full_next_message(self):
        reducer = watcher.SidebarSnapshotReducer()
        reducer.observe({"group": "群組", "text": "Z", "badge": "0"}, now=0)
        reducer.observe({"group": "群組", "text": "Z", "badge": "1"}, now=0.1)
        first_events = reducer.pop_due(now=0.9)
        self.assertEqual(len(first_events), 1)
        self.assertTrue(first_events[0]["preview_uncertain"])

        pending = watcher.PendingPreviewBuffer()
        pending.add(watcher._expand_side_preview_event(first_events[0], "群組")[0], now=1.0)

        reducer.observe({"group": "群組", "text": "B", "badge": "1"}, now=1.1)
        update = reducer.pop_due(now=1.9)[0]
        self.assertTrue(pending.update_latest("群組", "1", update))
        first = pending.snapshot_for_group("群組")[0]
        self.assertTrue(first["preview_provisional"])

        reducer.observe({"group": "群組", "text": "B", "badge": "2"}, now=2.0)
        second_event = reducer.pop_due(now=2.8)[0]
        self.assertEqual(second_event["invalidate_badge"], "1")
        self.assertTrue(pending.invalidate_provisional(
            "群組", second_event["invalidate_badge"]
        ))
        pending.add(
            watcher._expand_side_preview_event(second_event, "群組")[0], now=3.0
        )

        claimed = watcher._claim_backfill_batch(
            pending,
            "群組",
            [{"group": "群組", "message_id": "m-b", "text": "B"}],
            watcher.MessageIngressReservations(),
        )

        self.assertEqual([item["message_id"] for item in claimed], ["m-b"])
        self.assertEqual(pending.count_for_group("群組"), 1)
        self.assertEqual(
            watcher._preview_fallback_message(
                pending.snapshot_for_group("群組")[0]
            )["text"],
            "",
        )

    def test_queued_badge_snapshot_is_drained_before_due_preview(self):
        reducer = watcher.SidebarSnapshotReducer()
        reducer.observe({
            "group": "群組", "text": "A", "badge": "1",
        }, now=0)
        reducer.observe({"group": "群組", "text": "B", "badge": "1"}, now=0.1)
        snapshots = asyncio.Queue()
        snapshots.put_nowait({"group": "群組", "text": "B", "badge": "2"})

        self.assertEqual(
            watcher._drain_sidebar_snapshot_queue(snapshots, reducer), 1
        )
        events = reducer.pop_due(now=time.monotonic() + 1)

        self.assertEqual([(event["text"], event["badge"]) for event in events], [
            ("B", "2")
        ])

    def test_event_payload_reaches_message_candidate_parser(self):
        frame = '42["message:new",{"groupName":"晶圓三班佈告欄","content":"飲料我都拿到樓上右邊的冰箱了","senderName":"王小明","createdAt":"2026-07-25T14:25:00"}]'

        payloads = watcher._socketio_json_payloads(frame)
        candidates = [item for payload in payloads
                      for item in watcher._network_message_candidates(payload, "ws:test")]

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["text"], "飲料我都拿到樓上右邊的冰箱了")

    def test_message_envelope_with_json_data_reaches_candidate_parser(self):
        envelope = {
            "command": "message",
            "encryptVersion": 0,
            "data": '{"groupName":"晶圓三班佈告欄","content":"完整最後一則訊息","senderName":"王小明"}',
        }

        candidates = watcher._network_message_candidates(envelope, "ws:test")

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["text"], "完整最後一則訊息")

    def test_cid_message_response_uses_channel_name_and_inherited_cid(self):
        response = {
            "command": "getMessageResponse",
            "data": {
                "cid": "channel-42",
                "messages": [{"content": "未開啟群組的完整最後訊息", "senderName": "王小明"}],
            },
        }

        candidates = watcher._network_message_candidates(
            response, "ws:test", {"channel-42": "晶圓三班佈告欄"}
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["group"], "晶圓三班佈告欄")
        self.assertEqual(candidates[0]["cid"], "channel-42")
        self.assertEqual(candidates[0]["text"], "未開啟群組的完整最後訊息")

    def test_channel_name_is_learned_from_channel_list(self):
        names = {}
        watcher._remember_channel_names(
            {"data": {"channels": [{"cid": "channel-42", "name": "晶圓三班佈告欄"}]}}, names
        )
        self.assertEqual(names, {"channel-42": "晶圓三班佈告欄"})

    def test_message_sender_name_does_not_overwrite_channel_name_mapping(self):
        names = {"channel-42": "晶圓三班佈告欄"}
        response = {"data": {
            "cid": "channel-42",
            "messages": [{"cid": "channel-42", "name": "王小明", "message": "完整內容"}],
        }}

        watcher._remember_channel_names(response, names)

        self.assertEqual(names["channel-42"], "晶圓三班佈告欄")

    def test_get_message_response_ignores_lastmsg_and_uses_message_body(self):
        response = {"data": {
            "cid": "channel-42",
            "lastmsg": "王小明",
            "messages": [{"message": "最後一則完整訊息"}],
        }}

        candidates = watcher._network_message_candidates(
            response, "ws:test", {"channel-42": "晶圓三班佈告欄"}
        )

        self.assertEqual([item["text"] for item in candidates], ["最後一則完整訊息"])

    def test_preview_fallback_has_no_artificial_sender_label(self):
        fallback = watcher._preview_fallback_message({
            "group": "晶圓三班佈告欄 (13)", "preview": "這是截斷預覽...", "time": "下午 2:25",
        })

        self.assertTrue(fallback["is_preview"])
        self.assertEqual(fallback["sender"], "")
        self.assertEqual(fallback["text"], "這是截斷預覽...")

    def test_telegram_renders_preview_without_sender_prefix(self):
        rendered = watcher.format_for_tg(
            "晶圓三班佈告欄", "1", "這是截斷預覽...", "下午 2:25", None, ""
        )

        self.assertEqual(rendered.splitlines()[1], "這是截斷預覽...")


if __name__ == "__main__":
    unittest.main()
