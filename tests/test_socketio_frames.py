import importlib.util
import os
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / "fpc_watch_ui_login_telegram_v2026.08.31.1.py"
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

        args = {
            "tg": tg,
            "group": "台塑群組網 (13)",
            "delivery_key": "photo-delivery",
            "styled": "styled",
            "plain": "plain",
            "should_send_text": True,
            "saved_attachments": [Path("photo-1.png")],
            "already_sent": already_sent,
            "mark_sent": mark_sent,
        }

        self.assertEqual(watcher._send_telegram_bundle_once(**args)[0], "failed")
        self.assertEqual(watcher._send_telegram_bundle_once(**args)[0], "sent")
        self.assertEqual(watcher._send_telegram_bundle_once(**args)[0], "duplicate")
        self.assertEqual(tg.send_text.call_count, 1)
        self.assertEqual(tg.send_file.call_count, 2)

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
