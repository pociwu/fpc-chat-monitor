import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "fpc_watch_ui_login_telegram_v2026.08.30.2.py"
SPEC = importlib.util.spec_from_file_location("watcher", SOURCE)
watcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watcher)


class SocketIoFrameTests(unittest.TestCase):
    def test_local_verification_can_force_disable_telegram(self):
        cfg = {"telegram": {"enabled": True, "bot_token": "secret", "chat_id": "123"}}

        with patch.dict(os.environ, {"FPC_DISABLE_TELEGRAM": "1"}):
            forwarder = watcher.TelegramForwarder(cfg)

        self.assertFalse(forwarder.enabled)

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

    def test_attachment_url_can_be_a_direct_string(self):
        self.assertEqual(
            watcher._attachment_urls(["https://example.test/files/photo-1.png"]),
            [("https://example.test/files/photo-1.png", "")],
        )

    def test_pending_preview_buffer_does_not_overwrite_same_group(self):
        pending = watcher.PendingPreviewBuffer()
        pending.add({"group": "台塑群組網 (13)", "group_key": "台塑群組網",
                     "text": "黃紹瑄傳送了照片。", "badge": "1"}, now=0)
        pending.add({"group": "台塑群組網 (14)", "group_key": "台塑群組網",
                     "text": "黃紹瑄傳送了照片。", "badge": "2"}, now=0.1)

        first = pending.pop_for_group("台塑群組網")

        self.assertEqual(first["badge"], "1")

        expired = pending.pop_expired(now=9, max_age=8)

        self.assertEqual(len(expired), 1)
        self.assertEqual([item["badge"] for item in expired], ["2"])

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
