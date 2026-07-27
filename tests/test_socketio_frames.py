import importlib.util
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "fpc_watch_ui_login_telegram_v2026.07.27.3.py"
SPEC = importlib.util.spec_from_file_location("watcher", SOURCE)
watcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watcher)


class SocketIoFrameTests(unittest.TestCase):
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

    def test_preview_fallback_is_explicitly_labelled(self):
        fallback = watcher._preview_fallback_message({
            "group": "晶圓三班佈告欄 (13)", "preview": "這是截斷預覽...", "time": "下午 2:25",
        })

        self.assertTrue(fallback["is_preview"])
        self.assertEqual(fallback["sender"], "側欄預覽（可能截斷）")
        self.assertEqual(fallback["text"], "這是截斷預覽...")


if __name__ == "__main__":
    unittest.main()
