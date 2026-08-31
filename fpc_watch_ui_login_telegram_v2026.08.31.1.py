from pathlib import Path

def _parse_side_time_to_full(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    try:
        txt = s.replace("　", " ").replace("\u3000", " ").strip()
        is_pm = ("下午" in txt) or ("PM" in txt.upper())
        is_am = ("上午" in txt) or ("AM" in txt.upper())
        txt = txt.replace("上午", "").replace("下午", "").replace("AM", "").replace("PM", "").strip()
        hh = 0; mm = 0; ss = 0
        if ":" in txt:
            parts = txt.split(":")
            if len(parts) >= 2:
                hh = int(parts[0]); mm = int(parts[1])
            if len(parts) >= 3:
                ss = int(parts[2])
        else:
            hh = int(txt)
        if is_pm and hh < 12: hh += 12
        if is_am and hh == 12: hh = 0
        today = datetime.now()
        return f"{today.year}/{today.month:02d}/{today.day:02d} {hh:02d}:{mm:02d}:{ss:02d}"
    except Exception:
        return datetime.now().strftime("%Y/%m/%d %H:%M:%S")

def _parse_msg_time_to_full(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    try:
        txt = s.replace("-", "/").replace("　", " ").replace("\u3000", " ").strip()
        is_pm = ("下午" in txt) or ("PM" in txt.upper())
        is_am = ("上午" in txt) or ("AM" in txt.upper())
        txt = txt.replace("上午", "").replace("下午", "").replace("AM", "").replace("PM", "").strip()
        if "/" in txt and ":" in txt:
            parts = txt.split()
            if len(parts) == 1:
                date_part = datetime.now().strftime("%Y/%m/%d")
                time_part = parts[0]
            else:
                date_part, time_part = parts[0], parts[1]
            hms = time_part.split(":")
            hh = int(hms[0]); mm = int(hms[1]); ss = int(hms[2]) if len(hms) > 2 else 0
            if is_pm and hh < 12: hh += 12
            if is_am and hh == 12: hh = 0
            return f"{date_part} {hh:02d}:{mm:02d}:{ss:02d}"
        if ":" in txt:
            hms = txt.split(":")
            hh = int(hms[0]); mm = int(hms[1]); ss = int(hms[2]) if len(hms) > 2 else 0
            if is_pm and hh < 12: hh += 12
            if is_am and hh == 12: hh = 0
            today = datetime.now()
            return f"{today.year}/{today.month:02d}/{today.day:02d} {hh:02d}:{mm:02d}:{ss:02d}"
        return datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    except Exception:
        return datetime.now().strftime("%Y/%m/%d %H:%M:%S")
# -*- coding: utf-8 -*-
# Patched with Dedup Tab & Logging (JS/UI/CSV) + Dedup Toggles (Master/JS/UI/CSV)
"""
fpc_watch_ui_login_telegram.py  — Chatroom Message (High-Fidelity) Mode

- config.json 寬鬆讀取（註解/尾逗號/全形引號）
- 先用 fpc_state.json 自動登入；失敗再帳密
- 觀測器：
    (A) 側欄群組清單（更新左側群組與未讀）
    (B) 聊天室逐則訊息（高保真：時間 / 發送者 / 內容）
- CSV / 浮動通知 / Telegram 去重鍵：(聊天室時間, 群組)
- Telegram：MarkdownV2 嚴格跳脫 + 解析錯誤時自動降級純文字
- UI：頂部有「重新載入設定 / 儲存設定」；群組訊息分頁右上有「清空彈窗」「清空全部訊息」
"""

import os
import re
import csv
import sys
import json
import time
import queue
import asyncio
import logging
import threading
import traceback
import hashlib
import stat
from urllib.parse import urlparse, urljoin, quote, parse_qsl, urlencode, urlunparse
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List, Tuple

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


def _enable_windows_dpi_awareness() -> None:
    """Request DPI-aware screen metrics before the first Tk root is created."""
    if os.name != "nt":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_windows_dpi_awareness()


class SingleInstanceLock:
    """Hold an OS-backed file lock for the lifetime of one monitor process."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._handle = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError(f"runtime lock is not a regular file: {self.path}")
            handle = os.fdopen(fd, "r+b", buffering=0)
        except Exception:
            os.close(fd)
            raise
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None


def _runtime_lock_path() -> Path:
    """Use one per-user lock even when another version/copy is launched."""
    runtime_root = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime_root:
        return Path(runtime_root) / "fpc-chat-monitor" / "monitor.lock"
    return Path.home() / ".local" / "state" / "fpc-chat-monitor" / "monitor.lock"

# ---------- Windows asyncio 可靠化 ----------
def _log_boot(msg: str):
    try:
        with open("diagnose_asyncio.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    except Exception:
        pass

if os.name == "nt":
    try:
        import socket as _s
        s = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
        s.settimeout(0.05)
        try:
            s.connect(("127.0.0.1", 9))
        except Exception:
            pass
        finally:
            s.close()
        _log_boot("winsock smoke ok")
    except Exception as e:
        _log_boot(f"winsock error: {repr(e)}")

try:
    if os.name == "nt":
        try:
            import asyncio as _a
            _a.set_event_loop_policy(_a.WindowsProactorEventLoopPolicy())
            _log_boot("policy=Proactor")
        except Exception:
            import asyncio as _a
            _a.set_event_loop_policy(_a.WindowsSelectorEventLoopPolicy())
            _log_boot("policy=Selector(fallback)")
except Exception:
    _log_boot("set policy failed:\n" + traceback.format_exc())

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
APP_VERSION = "v2026.08.31.1"

def one_line(s: str, keep_newline: bool = False) -> str:
    if not s:
        return ""
    if keep_newline:
        s = s.replace("\r\n", "\n").replace("\r", "\n")
        return "\n".join(line.strip() for line in s.split("\n"))
    else:
        return re.sub(r"\s+", " ", s).strip()


def _responsive_ui_metrics(screen_width: int, screen_height: int, dpi: float = 96.0) -> Dict[str, object]:
    """Return deterministic Tk/browser sizing that stays inside the current screen."""
    screen_width = max(640, int(screen_width or 0))
    screen_height = max(480, int(screen_height or 0))
    dpi = max(50.0, min(300.0, float(dpi or 96.0)))
    pixel_ratio = max(0.75, min(2.5, dpi / 96.0))
    logical_width = screen_width / pixel_ratio
    logical_height = screen_height / pixel_ratio
    density = max(0.70, min(1.35, min(logical_width / 1920, logical_height / 1080)))
    window_width = max(640, min(round(1600 * pixel_ratio), screen_width - round(40 * pixel_ratio)))
    window_height = max(480, min(round(1000 * pixel_ratio), screen_height - round(80 * pixel_ratio)))
    compact = (window_width / pixel_ratio) < 1280 or (window_height / pixel_ratio) < 760
    font_size = max(10, min(15, round(12 * density)))
    group_column_width = max(170, int(window_width * (0.23 if compact else 0.25)))
    badge_column_width = max(48, int(56 * density))
    time_column_width = max(120, int(window_width * 0.13))
    forward_column_width = max(48, int(58 * density))
    message_content_width = max(
        300,
        window_width - group_column_width - badge_column_width
        - time_column_width - forward_column_width - 130,
    )
    return {
        "window_width": window_width,
        "window_height": window_height,
        "font_size": font_size,
        "small_font_size": max(9, font_size - 1),
        "tk_scaling": max(1.0, min(2.5, dpi / 72.0)),
        "pixel_ratio": pixel_ratio,
        "compact": compact,
        "padding": 4 if compact else 8,
        "entry_width": 24 if compact else 38,
        "group_column_width": group_column_width,
        "badge_column_width": badge_column_width,
        "time_column_width": time_column_width,
        "message_content_width": message_content_width,
        "forward_column_width": forward_column_width,
        "browser_width": 1536,
        "browser_height": 864,
        "tree_row_height": max(24, int(font_size * max(1.0, min(2.5, dpi / 72.0)) * 1.4)),
        "log_height": 7 if compact else 12,
    }


def _cleanup_expired_attachments(out_dir: Path, retention_days: int, now: Optional[float] = None) -> Dict[str, object]:
    """Delete only expired regular files below ``out_dir/attachments``.

    A non-positive retention disables cleanup. Symlinks and anything outside the
    attachment root are always skipped.
    """
    result: Dict[str, object] = {
        "enabled": int(retention_days or 0) > 0,
        "removed_files": 0,
        "removed_dirs": 0,
        "removed_bytes": 0,
        "errors": [],
    }
    if not result["enabled"]:
        return result
    attachment_root = Path(out_dir) / "attachments"
    if not attachment_root.is_dir():
        return result
    try:
        root_lstat = attachment_root.lstat()
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if attachment_root.is_symlink() or (
                reparse_flag and getattr(root_lstat, "st_file_attributes", 0) & reparse_flag):
            result["errors"].append("attachment root is a symlink or reparse point")
            return result
        out_dir_resolved = Path(out_dir).resolve(strict=True)
        root_resolved = attachment_root.resolve(strict=True)
        root_resolved.relative_to(out_dir_resolved)
    except OSError as exc:
        result["errors"].append(str(exc))
        return result
    except ValueError:
        result["errors"].append("attachment root resolves outside output directory")
        return result
    cutoff = (time.time() if now is None else float(now)) - int(retention_days) * 86400
    directories: List[Path] = []
    try:
        for path in attachment_root.rglob("*"):
            try:
                if path.is_symlink():
                    continue
                resolved = path.resolve(strict=True)
                resolved.relative_to(root_resolved)
                if path.is_dir():
                    directories.append(path)
                    continue
                if not path.is_file() or path.stat().st_mtime >= cutoff:
                    continue
                size = path.stat().st_size
                path.unlink()
                result["removed_files"] += 1
                result["removed_bytes"] += size
            except (OSError, ValueError) as exc:
                result["errors"].append(str(exc))
    except OSError as exc:
        result["errors"].append(str(exc))
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        try:
            directory.rmdir()
            result["removed_dirs"] += 1
        except OSError:
            pass
    return result


def _safe_attachment_component(value: str, fallback: str = "attachment") -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", one_line(value or "")).strip().strip(".")[:140]
    return cleaned if cleaned and cleaned not in {".", ".."} else fallback

def _network_message_candidates(payload, source: str = "", channel_names: Optional[Dict[str, str]] = None) -> List[Dict]:
    """從 API/WebSocket JSON 取出可能的訊息物件；未知欄位保留在 raw 供診斷。"""
    out, seen = [], set()
    channel_names = channel_names or {}
    group_keys = ("group", "groupName", "room", "roomName", "channel", "channelName", "conversationName")
    cid_keys = ("cid", "channelId", "channelID", "roomId", "roomID", "conversationId")
    text_keys = ("text", "content", "message", "body", "msgContent")
    sender_keys = ("sender", "senderName", "fromName", "author", "nickname", "userName")
    time_keys = ("time", "createdAt", "sendTime", "timestamp", "created_at")
    attach_keys = ("attachments", "attachment", "files", "file", "media", "medias")
    message_id_keys = ("messageId", "messageID", "msgId", "msgID", "message_id", "uuid")
    def walk(v, inherited_cid: str = ""):
        if isinstance(v, list):
            for x in v: walk(x, inherited_cid)
        elif isinstance(v, dict):
            cid = next((str(v[k]) for k in cid_keys if v.get(k) not in (None, "")), inherited_cid)
            group = next((v[k] for k in group_keys if isinstance(v.get(k), str) and v[k].strip()), "")
            if not group and cid:
                group = channel_names.get(cid, cid)
            text = next((v[k] for k in text_keys if isinstance(v.get(k), str) and v[k].strip()), "")
            sender = next((v[k] for k in sender_keys if isinstance(v.get(k), str) and v[k].strip()), "")
            sent = next((str(v[k]) for k in time_keys if v.get(k) not in (None, "")), "")
            attachments = next((v[k] for k in attach_keys if v.get(k) is not None), [])
            message_id = next((str(v[k]) for k in message_id_keys if v.get(k) not in (None, "")), "")
            if group and (text or attachments):
                key = (group, message_id, str(text), sent,
                       json.dumps(attachments, ensure_ascii=False, sort_keys=True, default=str))
                if key not in seen:
                    seen.add(key)
                    out.append({"type": "network_msg", "group": group, "cid": cid, "text": text, "sender": sender,
                                "time": sent, "attachments": attachments if isinstance(attachments, list) else [attachments],
                                "message_id": message_id, "source": source})
            for x in v.values():
                if isinstance(x, (dict, list)): walk(x, cid)
                elif isinstance(x, str) and x.lstrip().startswith(("{", "[")):
                    # Some Socket.IO message envelopes keep the actual payload in
                    # a JSON-encoded `data` string rather than a nested object.
                    try:
                        nested = json.loads(x)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(nested, (dict, list)):
                        walk(nested, cid)
    walk(payload)
    return out

def _socketio_json_payloads(frame) -> List[Dict]:
    """Unwrap Socket.IO event frames (for example ``42[\"event\", {...}]``).

    Engine.IO heartbeats are deliberately ignored.  Socket.IO event payloads are
    returned as dictionaries so they can share the HTTP/WebSocket message parser.
    """
    if isinstance(frame, dict):
        return [frame]
    if isinstance(frame, bytes):
        try:
            frame = frame.decode("utf-8")
        except UnicodeDecodeError:
            return []
    if not isinstance(frame, str):
        return []
    raw = frame.strip()
    if raw.startswith("42"):
        raw = raw[2:]
    elif raw.startswith("4") and len(raw) > 1 and raw[1] in "2356":
        # Socket.IO connect/disconnect/ack/binary packets are not message events.
        return []
    if not raw or raw[0] not in "[{":
        return []
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    if isinstance(decoded, dict):
        return [decoded]
    if not isinstance(decoded, list) or not decoded:
        return []
    return [value for value in decoded[1:] if isinstance(value, dict)]

def _socketio_event_name(frame) -> str:
    """Return an event name for diagnostics without storing event content."""
    if not isinstance(frame, str):
        return ""
    raw = frame.strip()
    if raw.startswith("42"):
        raw = raw[2:]
    if not raw.startswith("["):
        return ""
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    return decoded[0] if isinstance(decoded, list) and decoded and isinstance(decoded[0], str) else ""

def _json_shape(value, depth: int = 2):
    """Describe nested payload fields for diagnostics without recording message text."""
    if depth < 0:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(key): _json_shape(item, depth - 1)
                for key, item in list(value.items())[:30]}
    if isinstance(value, list):
        return {"type": "list", "length": len(value),
                "first": _json_shape(value[0], depth - 1) if value else None}
    if isinstance(value, str):
        return {"type": "str", "length": len(value)}
    return type(value).__name__

def _remember_channel_names(payload, channel_names: Dict[str, str]) -> None:
    """Learn CID-to-title mappings from explicit channel-list entries only."""
    cid_keys = ("cid", "channelId", "channelID", "roomId", "roomID", "conversationId")
    name_keys = ("groupName", "channelName", "roomName", "conversationName", "title", "name")
    channel_list_keys = {"channels", "channelList", "channel_list"}

    def walk(value, is_channel_entry: bool = False):
        if isinstance(value, list):
            for item in value:
                walk(item, is_channel_entry)
        elif isinstance(value, dict):
            if is_channel_entry:
                cid = next((str(value[key]) for key in cid_keys if value.get(key) not in (None, "")), "")
                name = next((value[key] for key in name_keys
                             if isinstance(value.get(key), str) and value[key].strip()), "")
                if cid and name:
                    channel_names[cid] = one_line(name)
            for key, item in value.items():
                if isinstance(item, (dict, list)):
                    walk(item, key in channel_list_keys)
    walk(payload)

def _candidate_for_group(candidates: List[Dict], group: str) -> Optional[Dict]:
    """Return the newest candidate whose server-side group name matches the sidebar."""
    wanted = _delivery_group_key(group)
    if not wanted:
        return None
    matched = [item for item in candidates
               if _delivery_group_key(item.get("group", "")) == wanted]
    if not matched:
        return None
    return matched[-1]

def _preview_fallback_message(pending: Dict) -> Dict:
    """Create a side-preview fallback when no full payload arrives."""
    return {
        "type": "network_msg",
        "group": one_line(pending.get("group", "")),
        "text": one_line(pending.get("preview", "")),
        "sender": "",
        "time": one_line(pending.get("time", "")),
        "attachments": [],
        "source": "side_preview_fallback",
        "is_preview": True,
        "event_token": one_line(pending.get("event_token", "")),
        "badge": one_line(pending.get("badge", "")),
    }


def _expand_side_preview_event(msg: Dict, group_key: str) -> List[Dict]:
    """Expand an unread badge jump into one pending slot per new message."""
    try:
        event_count = int(msg.get("event_count", 1) or 1)
    except (TypeError, ValueError):
        event_count = 1
    event_count = max(1, min(50, event_count))
    raw_badge = one_line(msg.get("badge", ""))
    badge_match = re.search(r"\d+", raw_badge)
    badge_value = int(badge_match.group(0)) if badge_match else 0
    first_badge = max(1, badge_value - event_count + 1)
    base_token = one_line(msg.get("event_token", ""))
    events: List[Dict] = []
    for index in range(event_count):
        badge = str(first_badge + index) if badge_value and event_count > 1 else raw_badge
        token = base_token
        if event_count > 1:
            token = f"{base_token}||slot:{index + 1}/{event_count}"
        events.append({
            "group": one_line(msg.get("group", "")),
            "group_key": group_key,
            "preview": one_line(msg.get("text", "")),
            "time": one_line(msg.get("time", "")),
            "badge": badge,
            "event_token": token,
        })
    return events

class PendingPreviewBuffer:
    """保留密集側欄事件；同群組的新事件不可覆蓋舊事件。"""
    ROW_SETTLE_SECONDS = 0.75

    def __init__(self):
        self._items: List[Dict] = []
        self._recent_claims: List[Tuple[str, str, str, float, Dict]] = []

    @staticmethod
    def _key(group: str) -> str:
        return re.sub(r"\s+", " ", group or "").strip().casefold()

    def __bool__(self) -> bool:
        return bool(self._items)

    def add(self, event: Dict, now: Optional[float] = None) -> Dict:
        current = time.time() if now is None else now
        item = dict(event)
        group_key = self._key(item.get("group_key") or item.get("group", ""))
        preview = one_line(item.get("preview", "")).casefold()
        badge = one_line(item.get("badge", ""))
        self._recent_claims = [
            claim for claim in self._recent_claims
            if 0 <= current - claim[3] <= self.ROW_SETTLE_SECONDS
        ]
        if badge:
            for claimed_group, claimed_preview, claimed_badge, _, claimed_item in reversed(
                self._recent_claims
            ):
                if (claimed_group == group_key and claimed_preview == preview
                        and claimed_badge == badge):
                    # The full server message already won the race.  Absorb the
                    # row's later time/badge render without creating a fallback.
                    claimed_item.update(item)
                    return claimed_item
        # One row is rendered in stages (preview/badge/time).  The unread badge
        # cannot stay equal for two genuine new unread messages, so only merge
        # a brief rerender with the same positive badge. Badge 1 -> 2 remains two.
        if badge:
            for existing in reversed(self._items):
                if self._key(existing.get("group_key") or existing.get("group", "")) != group_key:
                    continue
                age = current - float(existing.get("queued_at", current))
                if age > self.ROW_SETTLE_SECONDS:
                    break
                if (one_line(existing.get("preview", "")).casefold() == preview
                        and one_line(existing.get("badge", "")) == badge):
                    existing.update(item)
                    existing["queued_at"] = current
                    return existing
                break
        item["queued_at"] = current
        self._items.append(item)
        return item

    def has_group(self, group_key: str) -> bool:
        wanted = self._key(group_key)
        return any(self._key(item.get("group_key") or item.get("group", "")) == wanted
                   for item in self._items)

    def count_for_group(self, group_key: str) -> int:
        wanted = self._key(group_key)
        return sum(
            1 for item in self._items
            if self._key(item.get("group_key") or item.get("group", "")) == wanted
        )

    def pop_for_group(self, group_key: str) -> Optional[Dict]:
        wanted = self._key(group_key)
        for index, item in enumerate(self._items):
            if self._key(item.get("group_key") or item.get("group", "")) == wanted:
                claimed = self._items.pop(index)
                self._remember_claim(claimed)
                return claimed
        return None

    def remove(self, target: Dict) -> bool:
        for index, item in enumerate(self._items):
            if item is target:
                self._items.pop(index)
                self._remember_claim(item)
                return True
        return False

    def _remember_claim(self, item: Dict) -> None:
        badge = one_line(item.get("badge", ""))
        if not badge:
            return
        self._recent_claims.append((
            self._key(item.get("group_key") or item.get("group", "")),
            one_line(item.get("preview", "")).casefold(),
            badge,
            time.time(),
            item,
        ))

    def contains(self, target: Dict) -> bool:
        return any(item is target for item in self._items)

    def snapshot(self) -> List[Dict]:
        return list(self._items)

    def pop_expired(self, now: Optional[float] = None, max_age: float = 8) -> List[Dict]:
        current = time.time() if now is None else now
        expired = [item for item in self._items if current - item["queued_at"] >= max_age]
        self._items = [item for item in self._items if current - item["queued_at"] < max_age]
        return expired


class MessageIngressReservations:
    """Reserve server message IDs before they can consume another sidebar event."""

    def __init__(self):
        self._keys = set()

    @staticmethod
    def _aliases(item: Dict) -> List[Tuple[str, str, str]]:
        message_id = one_line(item.get("message_id", ""))
        if not message_id:
            return []
        aliases: List[Tuple[str, str, str]] = []
        cid = one_line(item.get("cid", ""))
        group = _delivery_group_key(item.get("group", ""))
        if cid:
            aliases.append(("cid", cid, message_id))
        if group:
            aliases.append(("group", group, message_id))
        return aliases

    def contains(self, item: Dict) -> bool:
        aliases = self._aliases(item)
        return bool(aliases) and any(alias in self._keys for alias in aliases)

    def reserve(self, item: Dict) -> bool:
        aliases = self._aliases(item)
        if not aliases:
            return True
        if any(alias in self._keys for alias in aliases):
            return False
        self._keys.update(aliases)
        return True

    def release(self, item: Dict) -> None:
        for alias in self._aliases(item):
            self._keys.discard(alias)


def _claim_passive_candidate(
    pending_groups: PendingPreviewBuffer,
    group_key: str,
    item: Dict,
    reservations: MessageIngressReservations,
) -> Optional[Dict]:
    """Claim the next sidebar event only when this server message is new."""
    if reservations.contains(item):
        return None
    pending = pending_groups.pop_for_group(group_key)
    if pending is None:
        return None
    claimed = dict(item)
    claimed["preview"] = pending.get("preview", "")
    claimed.setdefault("event_token", pending.get("event_token", ""))
    claimed.setdefault("badge", pending.get("badge", ""))
    if not reservations.reserve(claimed):
        raise RuntimeError("message reservation changed during an atomic claim")
    return claimed


def _claim_backfill_candidate(
    pending_groups: PendingPreviewBuffer,
    pending: Dict,
    item: Dict,
    reservations: Optional[MessageIngressReservations] = None,
) -> Optional[Dict]:
    """Atomically claim a pending sidebar event for the backfill producer.

    Passive HTTP/WebSocket processing and backfill can finish in either order.
    Only the producer that removes the pending object may enqueue a delivery.
    """
    if reservations is not None and reservations.contains(item):
        return None
    if not pending_groups.remove(pending):
        return None
    claimed = dict(item)
    claimed["preview"] = pending.get("preview", "")
    claimed.setdefault("event_token", pending.get("event_token", ""))
    claimed.setdefault("badge", pending.get("badge", ""))
    if reservations is not None and not reservations.reserve(claimed):
        raise RuntimeError("message reservation changed during an atomic claim")
    return claimed


def _claim_backfill_batch(
    pending_groups: PendingPreviewBuffer,
    group_key: str,
    candidates: List[Dict],
    reservations: MessageIngressReservations,
) -> List[Dict]:
    """Pair the newest unseen server messages with dense pending events in order."""
    pending_count = pending_groups.count_for_group(group_key)
    if pending_count <= 0:
        return []
    wanted = _delivery_group_key(group_key)
    matching = [
        item for item in candidates
        if _delivery_group_key(item.get("group", "")) == wanted
        and not reservations.contains(item)
    ]
    claimed: List[Dict] = []
    for item in matching[-pending_count:]:
        candidate = _claim_passive_candidate(
            pending_groups, group_key, item, reservations
        )
        if candidate is not None:
            claimed.append(candidate)
    return claimed


def _delivery_group_key(value: str) -> str:
    """Normalize display-only member counts without merging different groups."""
    group = one_line(value)
    group = re.sub(r"\s*[（(]\s*\d+\s*[)）]\s*$", "", group).strip()
    return re.sub(r"\s+", " ", group).casefold()


def _message_delivery_key(item: Dict) -> str:
    """建立逐筆傳送鍵；相同文字的不同訊息仍必須各自送達。"""
    group = _delivery_group_key(item.get("group", ""))
    message_id = one_line(item.get("message_id", ""))
    if message_id:
        # A server message ID is stable across passive, backfill, and DOM-derived
        # copies. UI metadata such as member count, badge, and event token must
        # not split the same server message into multiple deliveries.
        identity = {"group": group, "message_id": message_id}
        raw = json.dumps(identity, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
    attachments = _attachment_urls(item.get("attachments", []))
    identity = {
        "group": group,
        "badge": one_line(item.get("badge", "")),
        "time": one_line(item.get("time", "")),
        "text": one_line(item.get("text", ""), keep_newline=True),
        "attachments": attachments,
    }
    raw = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _send_telegram_bundle_once(
    tg,
    group: str,
    delivery_key: str,
    styled: str,
    plain: str,
    should_send_text: bool,
    saved_attachments: List[Path],
    already_sent,
    mark_sent,
) -> Tuple[str, List[Path]]:
    """Send one delivery identity at most once in the current monitor process."""
    if already_sent("tg", group, delivery_key):
        return "duplicate", []

    text_component = f"{delivery_key}:text"
    text_ok = True
    if should_send_text and not already_sent("tg", group, text_component):
        text_ok = tg.send_text(styled, fallback_plain=plain)
        if text_ok:
            mark_sent("tg", group, text_component)

    failed_attachments: List[Path] = []
    if text_ok:
        for attachment in saved_attachments:
            attachment_digest = hashlib.sha256(
                str(attachment).encode("utf-8")
            ).hexdigest()
            attachment_component = f"{delivery_key}:file:{attachment_digest}"
            if already_sent("tg", group, attachment_component):
                continue
            if tg.send_file(attachment, caption=f"{group}\n{attachment.name}"):
                mark_sent("tg", group, attachment_component)
            else:
                failed_attachments.append(attachment)

    if text_ok and not failed_attachments:
        mark_sent("tg", group, delivery_key)
        return "sent", []
    return "failed", failed_attachments

def _replace_group_in_request(value: str, learned_group: str, target_group: str) -> str:
    """Replay a learned request for another group without ever manipulating the UI."""
    if not value or not learned_group or learned_group == target_group:
        return value
    # APIs variously submit raw JSON/form data or URL-encoded text.
    return value.replace(learned_group, target_group).replace(
        quote(learned_group, safe=""), quote(target_group, safe="")
    )

def _diagnostic_url(url: str) -> str:
    """Retain endpoint shape while excluding credential-like query values from logs."""
    parsed = urlparse(url)
    secret_names = {"access_token", "api_key", "auth", "authorization", "password", "signature", "token"}
    query = [(key, "[redacted]" if key.casefold() in secret_names else value)
             for key, value in parse_qsl(parsed.query, keep_blank_values=True)]
    return urlunparse(parsed._replace(query=urlencode(query)))

def _attachment_urls(items) -> List[Tuple[str, str]]:
    """回傳 (下載 URL, 原始檔名)；只接受 http(s) URL。"""
    found, seen = [], set()
    def walk(v):
        if isinstance(v, str):
            url = v.strip()
            if url and url not in seen and (url.startswith(("http://", "https://", "/"))):
                seen.add(url)
                found.append((url, ""))
        elif isinstance(v, list):
            for x in v: walk(x)
        elif isinstance(v, dict):
            url = next((v[k] for k in ("url", "downloadUrl", "download_url", "fileUrl", "file_url",
                                       "imageUrl", "image_url", "originalUrl", "original_url", "src", "href")
                        if isinstance(v.get(k), str) and v[k].strip()), "")
            if url and url not in seen:
                seen.add(url)
                name = next((str(v[k]) for k in ("name", "fileName", "filename", "originalName") if v.get(k)), "")
                found.append((url, name))
            for x in v.values():
                if isinstance(x, (dict, list)): walk(x)
    walk(items)
    return found

# ---------- 浮動通知 ----------
class FloatingService:
    def __init__(self, width=280, height=90, gap=10, bottom_margin=50, auto_close_ms=6000):
        self.width = width
        self.height = height
        self.gap = gap
        self.bottom_margin = bottom_margin
        self.auto_close_ms = auto_close_ms
        self.q: "queue.Queue[tuple[str,str]]" = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def notify(self, title: str, msg: str):
        try:
            self.q.put((title, msg), block=False)
        except Exception:
            pass

    def clear_all(self):
        try:
            self.q.put(("__CLEAR__", ""), block=False)
        except Exception:
            pass

    def _run(self):
        try:
            import tkinter as tk  # noqa
        except Exception:
            return
        root = tk.Tk()
        root.withdraw()
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        try:
            popup_metrics = _responsive_ui_metrics(screen_w, screen_h, float(root.winfo_fpixels("1i")))
        except Exception:
            popup_metrics = _responsive_ui_metrics(screen_w, screen_h)
        popup_font_size = int(popup_metrics["small_font_size"])
        self.width = max(260, min(420, int(screen_w * 0.24)))
        self.height = max(90, min(150, int(screen_h * 0.13)))
        popups = []

        def reflow():
            x = screen_w - self.width - 20
            for idx, win in enumerate(reversed(popups)):
                y = screen_h - self.bottom_margin - self.height - idx * (self.height + self.gap)
                try:
                    win.geometry(f"{self.width}x{self.height}+{x}+{y}")
                except Exception:
                    pass

        def close_popup(win):
            try:
                if win in popups:
                    popups.remove(win)
                if win.winfo_exists():
                    win.destroy()
            except Exception:
                pass
            reflow()

        def pump():
            try:
                while True:
                    title, msg = self.q.get_nowait()

                    if title == "__CLEAR__":
                        for w in list(popups):
                            try:
                                close_popup(w)
                            except Exception:
                                pass
                        continue

                    win = tk.Toplevel(root)
                    win.overrideredirect(True)
                    try:
                        win.attributes("-topmost", True)
                    except Exception:
                        pass
                    frame = tk.Frame(win, bg="#ffffe0", bd=1, relief="solid")
                    frame.pack(fill="both", expand=True)
                    tk.Label(frame, text=title, bg="#ffffe0", anchor="w", justify="left",
                             wraplength=self.width - 30,
                             font=("Segoe UI", popup_font_size, "bold")).pack(fill="x", padx=8, pady=(6, 0))
                    tk.Label(frame, text=msg, bg="#ffffe0", anchor="w", justify="left",
                             wraplength=self.width - 24,
                             font=("Segoe UI", popup_font_size)).pack(fill="both", expand=True, padx=8, pady=(2, 6))
                    tk.Button(
                        frame, text="✖", bd=0, relief="flat", bg="#ffffe0",
                        activebackground="#ffe4e1", command=lambda w=win: close_popup(w)
                    ).place(relx=1.0, rely=0.0, x=-4, y=4, anchor="ne")
                    popups.append(win)
                    max_visible = max(1, (screen_h - self.bottom_margin) // (self.height + self.gap))
                    while len(popups) > max_visible:
                        close_popup(popups[0])
                    reflow()
                    if self.auto_close_ms and self.auto_close_ms > 0:
                        win.after(self.auto_close_ms, lambda w=win: close_popup(w))
            except queue.Empty:
                pass
            root.after(120, pump)

        root.after(120, pump)
        try:
            root.mainloop()
        except Exception:
            pass


class Notifier:
    def __init__(self, popup_mode: str = "floating", auto_close_sec: int = 6):
        self.mode = (popup_mode or "off").lower()
        self.auto_close_sec = int(auto_close_sec or 0)
        self._toast = None
        self._floating: Optional[FloatingService] = None

        if self.mode == "floating":
            try:
                self._floating = FloatingService(auto_close_ms=self.auto_close_sec * 1000)
            except Exception:
                logging.warning("floating 初始化失敗，降級 toast")
                self.mode = "toast"

        if self.mode == "toast":
            try:
                from win10toast import ToastNotifier  # type: ignore
                self._toast = ToastNotifier()
            except Exception:
                logging.warning("win10toast 不可用，降級 msgbox")
                self.mode = "msgbox"

    def _notify_msgbox(self, title: str, msg: str):
        import platform
        if platform.system() == "Windows":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, msg, title, 0x00000040)
                return
            except Exception:
                pass
        logging.info("[POPUP] %s - %s", title, msg)

    def _notify_toast(self, title: str, msg: str):
        try:
            if self._toast:
                self._toast.show_toast(title, msg, duration=max(1, self.auto_close_sec), threaded=True)
                return
        except Exception:
            pass
        self._notify_msgbox(title, msg)

    def notify(self, title: str, msg: str):
        if self.mode == "off":
            return
        if self.mode == "floating" and self._floating:
            self._floating.notify(title, msg)
        elif self.mode == "toast":
            self._notify_toast(title, msg)
        else:
            self._notify_msgbox(title, msg)

    def clear_all(self):
        if self.mode == "floating" and self._floating:
            try:
                self._floating.clear_all()
            except Exception:
                pass

# ---------- Telegram ----------
def escape_mdv2(s: str) -> str:
    """嚴格依 Telegram MarkdownV2 規則跳脫所有保留字元。"""
    if not s:
        return ""
    specials = set("_*[]()~`>#+-=|{}.!")
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch in specials:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)

def format_for_tg(name: str, badge: str, content: str, ts_display: str, parse_mode: Optional[str], sender: str = "") -> str:
    """聊天室訊息版：title=群組名(+未讀)；第二行=內容；第三行=時間；若有 sender 放進內容前綴。"""
    sender_prefix = f"[{sender}] " if sender else ""
    if parse_mode == "HTML":
        import html
        n = html.escape(name or "")
        b = html.escape(badge or "")
        c = html.escape(sender_prefix + (content or ""))
        t = html.escape(ts_display or "")
        title = f"<b>{n}</b>" + (f" [{b}]" if b else "")
        return f"{title}\n{c}\n{t}"
    elif parse_mode == "MarkdownV2":
        n = escape_mdv2(name or "")
        b = escape_mdv2(badge or "")
        c = escape_mdv2(sender_prefix + (content or ""))
        t = escape_mdv2(ts_display or "")
        title = f"*{n}*" + (f" [{b}]" if b else "")
        return f"{title}\n{c}\n{t}"
    else:
        title = f"{name}" + (f" [{badge}]" if badge else "")
        return f"{title}\n{sender_prefix}{content}\n{ts_display}"

class TelegramForwarder:
    def __init__(self, cfg: Dict):
        tgc = cfg.get("telegram", {}) or {}
        force_disabled = os.environ.get("FPC_DISABLE_TELEGRAM", "").strip().casefold() in {"1", "true", "yes"}
        self.enabled = bool(tgc.get("enabled", False)) and not force_disabled
        self.token = (tgc.get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = (tgc.get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
        self.parse_mode = (tgc.get("parse_mode") or os.environ.get("TELEGRAM_PARSE_MODE", "")).strip() or None
        self.disable_preview = bool(tgc.get("disable_preview", True))
        self.retry = int(tgc.get("retry", 3))
        self.timeout = int(tgc.get("timeout_sec", 15))
        self.rate_limit_ms = int(tgc.get("rate_limit_ms", 400))
        self.announce_on_start = bool(tgc.get("announce_on_start", True))
        self._last_ts = 0.0
        self._ok = False
        try:
            import requests  # noqa
            self._ok = True
        except Exception:
            self._ok = False
        if self.enabled and (not self.token or not self.chat_id):
            logging.warning("Telegram 啟用但缺 token/chat_id，將停用。")
            self.enabled = False

    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def _rl(self):
        need = self.rate_limit_ms / 1000.0
        dt = time.time() - self._last_ts
        if dt < need:
            time.sleep(need - dt)

    def send_text(self, text: str, fallback_plain: Optional[str] = None) -> bool:
        """Send without replaying a request whose delivery result is ambiguous."""
        if not (self.enabled and self._ok):
            return False
        from requests import post  # type: ignore
        from requests.exceptions import ConnectTimeout, ConnectionError, ReadTimeout  # type: ignore

        def _try_send(txt: str, with_parse: bool) -> bool:
            self._rl()
            data = {"chat_id": self.chat_id, "text": txt}
            if with_parse and self.parse_mode:
                data["parse_mode"] = self.parse_mode
            if self.disable_preview:
                data["disable_web_page_preview"] = True
            r = post(self._api("sendMessage"), data=data, timeout=self.timeout)
            if r.ok:
                self._last_ts = time.time()
                return True
            logging.warning("TG sendMessage 失敗: %s", r.text)
            if r.status_code == 429:
                try:
                    retry_after = max(1, int(r.json().get("parameters", {}).get("retry_after", 1)))
                except Exception:
                    retry_after = 1
                logging.warning("TG rate limited; retry after %ss", retry_after)
                time.sleep(retry_after)
            if r.status_code == 400 and "parse entities" in r.text.lower():
                return False
            return False

        # with_parse
        for i in range(1, self.retry + 1):
            try:
                if _try_send(text, with_parse=True):
                    return True
            except ConnectTimeout as e:
                logging.warning("TG connect timeout before delivery(%s/%s): %s", i, self.retry, e)
            except (ReadTimeout, ConnectionError) as e:
                logging.error(
                    "TG sendMessage result UNKNOWN; suppressing retry to avoid duplicate: %s", e
                )
                return True
            except Exception as e:
                logging.exception("TG sendMessage 例外(%s/%s): %s", i, self.retry, e)
            time.sleep(0.6 * i)

        # 降級純文字
        if fallback_plain:
            for i in range(1, self.retry + 1):
                try:
                    if _try_send(fallback_plain, with_parse=False):
                        return True
                except ConnectTimeout as e:
                    logging.warning("TG plain connect timeout(%s/%s): %s", i, self.retry, e)
                except (ReadTimeout, ConnectionError) as e:
                    logging.error(
                        "TG plain send result UNKNOWN; suppressing retry to avoid duplicate: %s", e
                    )
                    return True
                except Exception as e:
                    logging.exception("TG 純文字重送 例外(%s/%s): %s", i, self.retry, e)
                time.sleep(0.6 * i)
        return False

    def send_file(self, file_path: Path, caption: str = "") -> bool:
        """將已保存的附件上傳 Telegram；失敗交由呼叫端記錄，不重複重試。"""
        if not (self.enabled and self._ok and file_path.is_file()):
            return False
        from requests import post  # type: ignore
        from requests.exceptions import ConnectTimeout, ConnectionError, ReadTimeout  # type: ignore
        method = "sendPhoto" if file_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} else "sendDocument"
        for attempt in range(1, self.retry + 1):
            try:
                self._rl()
                with open(file_path, "rb") as fh:
                    data = {"chat_id": self.chat_id}
                    if caption:
                        data["caption"] = caption[:1024]
                    response = post(
                        self._api(method), data=data,
                        files={"photo" if method == "sendPhoto" else "document": fh},
                        timeout=self.timeout,
                    )
                if response.ok:
                    self._last_ts = time.time()
                    return True
                logging.warning("TG %s failed(%s/%s): %s", method, attempt, self.retry, response.text)
                if response.status_code == 429:
                    try:
                        retry_after = max(1, int(response.json().get("parameters", {}).get("retry_after", 1)))
                    except Exception:
                        retry_after = 1
                    time.sleep(retry_after)
                else:
                    time.sleep(0.6 * attempt)
            except ConnectTimeout as e:
                logging.warning("TG attachment connect timeout(%s/%s): %s", attempt, self.retry, e)
                time.sleep(0.6 * attempt)
            except (ReadTimeout, ConnectionError) as e:
                logging.error(
                    "TG attachment result UNKNOWN; suppressing retry to avoid duplicate: %s", e
                )
                return True
            except Exception as e:
                logging.exception("TG attachment upload failed(%s/%s): %s", attempt, self.retry, e)
                time.sleep(0.6 * attempt)
        return False

# ---------- Playwright 登入 ----------
try:
    from playwright.async_api import async_playwright
except Exception:
    async_playwright = None  # 讓錯誤能優雅呈現

async def auto_login(page, cfg: Dict, notifier: 'Notifier'):
    login_cfg = cfg.get("login", {}) or {}
    u_sel = login_cfg.get("username_selector", "input[name='account']")
    p_sel = login_cfg.get("password_selector", "input[name='password']")
    s_sel = login_cfg.get("submit_selector", "button[type='submit']")
    wait_sec = int(login_cfg.get("login_success_wait_sec", 12))
    username = (login_cfg.get("username") or "").strip()
    password = (login_cfg.get("password") or "").strip()

    if not username or not password:
        notifier.notify("登入設定", "未提供帳號或密碼，停留登入頁等你手動登入。")
        logging.info("未提供帳密，改由人工登入。")
        return False

    def picks(slist: str):
        return [s.strip() for s in slist.split(",") if s.strip()]

    for sel in picks(u_sel):
        try:
            await page.wait_for_selector(sel, timeout=4000)
            await page.fill(sel, username)
            break
        except Exception:
            continue
    else:
        notifier.notify("登入", "找不到帳號欄位，請手動登入。")
        return False

    for sel in picks(p_sel):
        try:
            await page.fill(sel, password)
            break
        except Exception:
            continue

    clicked = False
    for sel in picks(s_sel):
        try:
            await page.click(sel)
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        try:
            await page.keyboard.press("Enter")
        except Exception:
            pass

    try:
        await page.wait_for_load_state("networkidle", timeout=wait_sec * 1000)
    except Exception:
        pass
    return True

async def ensure_logged_in(p, cfg: Dict, notifier: 'Notifier', base_url: str, login_url: str, storage_state_file: Path, headless: bool):
    force_relogin = bool(cfg.get("login", {}).get("force_relogin", False))
    use_storage = storage_state_file.exists() and not force_relogin
    browser_cfg = cfg.get("browser", {}) or {}
    viewport = browser_cfg.get("viewport") or {"width": 1536, "height": 864}
    viewport = {
        "width": max(800, int(viewport.get("width", 1536))),
        "height": max(600, int(viewport.get("height", 864))),
    }
    context_options = {
        "storage_state": str(storage_state_file) if use_storage else None,
        "locale": "zh-TW",
        "timezone_id": "Asia/Taipei",
        "viewport": viewport,
        "color_scheme": "light",
    }
    if headless:
        br = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--headless=new",
                "--no-sandbox",
            ],
        )
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")
        ctx = await br.new_context(
            **context_options,
            user_agent=ua,
            extra_http_headers={"Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"},
        )
        await ctx.add_init_script("""
          Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
          window.chrome = { runtime: {} };
          Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
          Object.defineProperty(navigator, 'languages', { get: () => ['zh-TW','zh','en-US','en'] });
          try {
            const orig = navigator.permissions.query.bind(navigator.permissions);
            navigator.permissions.query = (p) =>
              p && p.name === 'notifications'
                ? Promise.resolve({ state: 'granted' })
                : orig(p);
          } catch (_) {}
        """)
        try:
            await ctx.grant_permissions(['clipboard-read','clipboard-write','notifications'])
        except Exception:
            pass
    else:
        br = await p.chromium.launch(
            headless=False,
            args=[f"--window-size={viewport['width']},{viewport['height']}"]
        )
        ctx = await br.new_context(**context_options)
    pg = await ctx.new_page()

    async def at_login():
        try:
            return "/login" in (pg.url or "").lower()
        except Exception:
            return False

    await pg.goto(base_url, wait_until="domcontentloaded")
    if not await at_login():
        return br, ctx, pg, True

    logging.info("storage_state 失效，嘗試帳密登入 …")
    await pg.goto(login_url, wait_until="domcontentloaded")
    try:
        ok = await auto_login(pg, cfg, notifier)
    except Exception as e:
        logging.exception("自動登入流程異常：%s", e)
        ok = False

    if ok and await at_login():
        notifier.notify("登入", "已自動填表送出，若有 OTP/驗證請完成。")
        try:
            await pg.wait_for_url(lambda u: "/login" not in u.lower(), timeout=15000)
        except Exception:
            pass

    try:
        await ctx.storage_state(path=str(storage_state_file))
        logging.info("已保存登入狀態到 %s", storage_state_file)
    except Exception as e:
        logging.warning("保存登入狀態失敗：%s", e)

    await pg.goto(base_url, wait_until="domcontentloaded")
    if await at_login():
        notifier.notify("登入需要", "仍在登入頁，請完成登入後重啟。")
        logging.error("登入失敗（仍在 /login）。")
        return br, ctx, pg, False
    return br, ctx, pg, True

# ---------- UI ----------

async def _attach_msg_preview(pg):
    # 方案A：訊息預覽觀測器（包成函式避免縮排問題）
    await pg.evaluate("""
      () => {
        const delay = (ms) => new Promise(r => setTimeout(r, ms));
        const pickRoot = () => {
          const cs = document.querySelector("#cs");
          if (!cs) return null;
          return (
            cs.querySelector(".el-scrollbar__view.view-box") ||
            cs.querySelector(".view-box") ||
            cs.querySelector("[class*='scrollbar__view']") ||
            cs.querySelector("[class*='virtual'],[class*='infinite'],[class*='list']") ||
            cs
          );
        };
        const getGroupName = () => {
          const cand = document.querySelector("#chat-header .title")
                    || document.querySelector(".title");
          return cand ? (cand.textContent || "").trim() : "";
        };
        const any = (node, sel) => node.querySelector(sel);
        const textOf = (el) => (el ? (el.innerText || el.textContent || "").trim() : "");

        const setup = async () => {
          let root = null;
          for (let i=0;i<50;i++) {
            root = pickRoot();
            if (root) break;
            await delay(200);
          }
          if (!root) {
            console.log("[MSG] root not found (#cs)");
            return false;
          }
          console.log("[MSG] watcher attached on", root.className || root.id || root.tagName);

          const seen = new Set();
          const keyOf = (s) => (s||"").trim().slice(0,200)+"::"+(s||"").length;

          const extract = (node) => {
            const textEl =
              any(node, ".channel-cell-content") ||
              any(node, ".message-content") ||
              any(node, ".msg-content") ||
              any(node, "[class*='cell-content']") ||
              any(node, "[class*='message'] [class*='content']") ||
              node;
            const text = textOf(textEl);
            const nameEl =
              any(node, ".channel-cell-name") ||
              any(node, ".name, .sender, .username") ||
              any(node, "[class*='author'],[class*='sender'],[class*='from']");
            const sender = textOf(nameEl);
            let time = "";
            const row = node.closest(".el-row") || node.parentElement;
            if (row) {
              const tEl = row.querySelector(".channel-cell-time,[class*='time']");
              time = textOf(tEl);
            }
            return { text, sender, time };
          };

          const toMsgNodes = () => {
            const cs = document.querySelector("#cs") || document;
            const candidates = Array.from(cs.querySelectorAll(
              ".channel-cell, .channel-media, .message, [class*='message'], [class*='cell']"
            ));
            if (candidates.length === 0) {
              return Array.from(cs.querySelectorAll(".channel-cell-content, .message-content, .msg-content"))
                .map(el => el.closest("div"));
            }
            return candidates;
          };

          const push = (item) => {
            const payload = {
              type: "msg",
              group: getGroupName(),
              sender: item.sender || "",
              text: item.text || "",
              time: item.time || ""
            };
            try { window.pyPushMsg(payload); } catch(e) {}
            console.log("[MSG]", JSON.stringify(payload));
          };

          let lastCount = -1;
          const scan = () => {
            const list = toMsgNodes();
            if (list.length !== lastCount) {
              console.log("[MSG][scan] nodes:", list.length);
              lastCount = list.length;
            }
            for (const n of list) {
              if (!n) continue;
              const item = extract(n);
              const k = keyOf(item.text);
              if (!item.text || seen.has(k)) continue;
              seen.add(k);
              push(item);
            }
          };

          setTimeout(scan, 300);
          const mo = new MutationObserver(() => scan());
          mo.observe(root, { childList: true, subtree: true, characterData: true });
          setInterval(scan, 3000);
          return true;
        };

        setup();
      }
    """ )

class App(tk.Tk):

    # ---------- Sent flags (notify / telegram) persistent per-day ----------
    def _sent_store_path(self):
        try:
            out_dir = Path(self.var_outdir.get()).resolve()
        except Exception:
            out_dir = Path.cwd()
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"sent_flags_{datetime.now().strftime('%Y%m%d')}.json"

    def _sent__init(self):
        if not hasattr(self, "_sent_flags"):
            self._sent_flags = {"notify": set(), "tg": set()}
            try:
                p = self._sent_store_path()
                if p.exists():
                    data = json.loads(p.read_text(encoding="utf-8"))
                    self._sent_flags["notify"] = set(data.get("notify", []))
                    self._sent_flags["tg"] = set(data.get("tg", []))
            except Exception:
                pass

    def _sent_key(self, group: str, content: str) -> str:
        return f"{_delivery_group_key(group)}\x1f{content}"

    def _sent_done(self, kind: str, group: str, content: str) -> bool:
        self._sent__init()
        key = self._sent_key(group, content)
        return key in self._sent_flags.get(kind, set())

    def _sent_mark(self, kind: str, group: str, content: str):
        self._sent__init()
        key = self._sent_key(group, content)
        self._sent_flags.setdefault(kind, set()).add(key)
        try:
            p = self._sent_store_path()
            data = {k: list(v) for k, v in self._sent_flags.items()}
            p.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
        except Exception as e:
            self._push_from_worker({"type":"log","text":f"[WARN] 寫入 sent_flags 失敗：{e}\n"})

    def _sent_log_row(self, sent_time: str, group: str, content: str, action: str):
        try:
            out_dir = Path(self.var_outdir.get()).resolve()
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"sent_log_{datetime.now().strftime('%Y%m%d')}.csv"
            if not path.exists():
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    f.write(f"# FPC Watch {APP_VERSION}\n")
                    f.write("時間,群組,內容,動作\n")
            with open(path, "a", encoding="utf-8-sig", newline="") as f:
                f.write(f"{sent_time},{group},{content},{action}\n")
        except Exception as e:
            self._push_from_worker({"type":"log","text":f"[WARN] 寫入 sent_log 失敗：{e}\n"})
    def __init__(self):
        # ---- runtime flags (safe defaults) ----
        self._want_dump_dom = False
        self._force_reload_cfg = False
        self._clear_groups_pending = False
        self._clear_all_msgs_pending = False
        self._close_all_groups_pending = False
        self._stop_flag = False
        self._ui_paused = False
        super().__init__()
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        try:
            dpi = float(self.winfo_fpixels("1i"))
        except Exception:
            dpi = 96.0
        self.ui_metrics = _responsive_ui_metrics(screen_width, screen_height, dpi)
        self.tk.call("tk", "scaling", self.ui_metrics["tk_scaling"])
        import tkinter.font as tkfont
        for font_name in (
            "TkDefaultFont",
            "TkTextFont",
            "TkFixedFont",
            "TkMenuFont",
            "TkHeadingFont",
            "TkCaptionFont",
            "TkSmallCaptionFont",
            "TkIconFont",
            "TkTooltipFont",
        ):
            try:
                font = tkfont.nametofont(font_name)
                size = (self.ui_metrics["small_font_size"]
                        if font_name in {"TkFixedFont", "TkSmallCaptionFont", "TkTooltipFont"}
                        else self.ui_metrics["font_size"])
                font.configure(size=size)
            except Exception:
                pass
        self.title(f"FPC Watch（聊天室逐則訊息・高保真） | {APP_VERSION}")
        window_width = int(self.ui_metrics["window_width"])
        window_height = int(self.ui_metrics["window_height"])
        pos_x = max(0, (screen_width - window_width) // 2)
        pos_y = max(0, (screen_height - window_height) // 2)
        self.geometry(f"{window_width}x{window_height}+{pos_x}+{pos_y}")
        self.minsize(min(800, window_width), min(520, window_height))
        style = ttk.Style(self)
        style.configure("Treeview", rowheight=int(self.ui_metrics["tree_row_height"]))

        self.ui_queue = queue.Queue()
        self.worker_thread: Optional[threading.Thread] = None

        # 狀態
        self.group_logs: Dict[str, List[tuple]] = {}
        self.cur_group_display: str = ""          # 右側訊息
        self.group_badge: Dict[str, str] = {}               # 左側未讀
        self.group_last_ts: Dict[str, float] = {}           # 左側排序依據
        self.csv_seen: Dict[Path, set[tuple[str, str, str]]] = {}  # 去重 (time, group, text)
        self.ui_last_sig: Dict[str, Tuple[str, str, str]] = {}     # 右側 UI 去重 (time, text, sender)
        self._notifier_inst: Optional[Notifier] = None

        # ===== 頂部工具列 =====
        padding = int(self.ui_metrics["padding"])
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=padding, pady=padding)
        top.grid_columnconfigure(0, weight=1)
        action_row = ttk.Frame(top)
        action_row.grid(row=0, column=0, sticky="w")
        path_row = ttk.Frame(top)
        path_row.grid(row=1, column=0, sticky="ew", pady=(padding, 0))
        path_row.grid_columnconfigure(1, weight=1)

        self.var_outdir = tk.StringVar(value=str((BASE / "scraped_chats").resolve()))
        self.var_headless = tk.BooleanVar(value=False)

        ttk.Button(action_row, text="開始監控", command=self.start_watch).pack(side=tk.LEFT, padx=(0, padding))
        ttk.Button(action_row, text="停止", command=self.stop_watch).pack(side=tk.LEFT, padx=(0, padding))
        ttk.Button(action_row, text="重新載入設定", command=self.reload_settings).pack(side=tk.LEFT, padx=(0, padding))
        ttk.Button(action_row, text="儲存設定", command=self._save_all_settings).pack(side=tk.LEFT)

        ttk.Label(path_row, text="輸出資料夾").grid(row=0, column=0, sticky="w")
        ttk.Entry(path_row, textvariable=self.var_outdir).grid(row=0, column=1, sticky="ew", padx=padding)
        ttk.Button(path_row, text="選擇...", command=self.browse_out).grid(row=0, column=2, padx=(0, padding))
        ttk.Checkbutton(path_row, text="Headless", variable=self.var_headless).grid(row=0, column=3, sticky="w")

        # ===== Notebook =====
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=padding, pady=(0, padding))
        self.tab_monitor = ttk.Frame(self.nb)
        self.nb.add(self.tab_monitor, text="監控台")
        self.monitor_canvas = tk.Canvas(self.tab_monitor, highlightthickness=0)
        self.monitor_scroll_y = ttk.Scrollbar(self.tab_monitor, orient="vertical", command=self.monitor_canvas.yview)
        self.monitor_scroll_x = ttk.Scrollbar(self.tab_monitor, orient="horizontal", command=self.monitor_canvas.xview)
        self.monitor_canvas.configure(
            yscrollcommand=self.monitor_scroll_y.set,
            xscrollcommand=self.monitor_scroll_x.set,
        )
        self.monitor_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.monitor_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.monitor_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.monitor_body = ttk.Frame(self.monitor_canvas)
        self._monitor_window = self.monitor_canvas.create_window((0, 0), window=self.monitor_body, anchor="nw")
        self.monitor_body.bind(
            "<Configure>",
            lambda _event: self.monitor_canvas.configure(scrollregion=self.monitor_canvas.bbox("all")),
        )
        self.monitor_canvas.bind(
            "<Configure>",
            lambda event: self.monitor_canvas.itemconfigure(
                self._monitor_window, width=max(event.width, self.monitor_body.winfo_reqwidth())
            ),
        )
        self.tab_groups = ttk.Frame(self.nb)
        self.nb.add(self.tab_groups, text="群組訊息")
        # ---- 去重分頁 ----
        self.tab_dedup = ttk.Frame(self.nb)
        self.nb.add(self.tab_dedup, text="去重")

        dz = ttk.Frame(self.tab_dedup)
        dz.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        ttk.Label(dz, text="去重紀錄（JS / UI / CSV）").pack(anchor="w")

        self.dedup_scroll_y = ttk.Scrollbar(dz, orient="vertical")
        self.txt_dedup = tk.Text(dz, height=20, yscrollcommand=self.dedup_scroll_y.set)
        self.dedup_scroll_y.config(command=self.txt_dedup.yview)
        self.txt_dedup.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.dedup_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)


        # ---- 監控台：登入設定 ----
        lg = ttk.LabelFrame(self.monitor_body, text="登入設定")
        lg.pack(fill=tk.X, padx=0, pady=6)
        self.var_base = tk.StringVar()
        self.var_login = tk.StringVar()
        self.var_user = tk.StringVar()
        self.var_pwd = tk.StringVar()
        self.var_force = tk.BooleanVar(value=False)
        ttk.Label(lg, text="Base URL").grid(row=0, column=0, sticky="e")
        entry_width = int(self.ui_metrics["entry_width"])
        ttk.Entry(lg, textvariable=self.var_base, width=entry_width).grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        ttk.Label(lg, text="Login URL").grid(row=0, column=2, sticky="e")
        ttk.Entry(lg, textvariable=self.var_login, width=entry_width).grid(row=0, column=3, sticky="ew", padx=6, pady=4)
        ttk.Label(lg, text="帳號").grid(row=1, column=0, sticky="e")
        ttk.Entry(lg, textvariable=self.var_user, width=entry_width).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Label(lg, text="密碼").grid(row=1, column=2, sticky="e")
        ttk.Entry(lg, textvariable=self.var_pwd, show="*", width=entry_width).grid(row=1, column=3, sticky="ew", padx=6)
        self.var_u_sel = tk.StringVar()
        self.var_p_sel = tk.StringVar()
        self.var_s_sel = tk.StringVar()
        self.var_wait = tk.IntVar(value=12)
        ttk.Label(lg, text="帳號 selector").grid(row=2, column=0, sticky="e")
        ttk.Entry(lg, textvariable=self.var_u_sel, width=entry_width).grid(row=2, column=1, sticky="ew", padx=6)
        ttk.Label(lg, text="密碼 selector").grid(row=2, column=2, sticky="e")
        ttk.Entry(lg, textvariable=self.var_p_sel, width=entry_width).grid(row=2, column=3, sticky="ew", padx=6)
        ttk.Label(lg, text="送出 selector").grid(row=3, column=0, sticky="e")
        ttk.Entry(lg, textvariable=self.var_s_sel, width=entry_width).grid(row=3, column=1, sticky="ew", padx=6)
        ttk.Label(lg, text="成功等待秒").grid(row=3, column=2, sticky="e")
        ttk.Entry(lg, textvariable=self.var_wait, width=10).grid(row=3, column=3, sticky="w", padx=6)
        ttk.Checkbutton(lg, text="強制重登（忽略 fpc_state.json）", variable=self.var_force).grid(row=4, column=1, sticky="w", padx=6, pady=4)
        lg.grid_columnconfigure(1, weight=1)
        lg.grid_columnconfigure(3, weight=1)

        # ---- 通知 ----
        ui = ttk.LabelFrame(self.monitor_body, text="通知")
        ui.pack(fill=tk.X, padx=0, pady=6)
        # ---- 去重功能 ----
        dd = ttk.LabelFrame(self.monitor_body, text="去重功能")
        dd.pack(fill=tk.X, padx=0, pady=6)
        self.var_dedup_master = tk.BooleanVar(value=False)
        self.var_dedup_js = tk.BooleanVar(value=False)
        self.var_dedup_ui = tk.BooleanVar(value=False)
        self.var_dedup_csv = tk.BooleanVar(value=False)

        ttk.Checkbutton(dd, text="啟用去重（總開關）", variable=self.var_dedup_master).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(dd, text="JS（前端 DOM 去重）", variable=self.var_dedup_js).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Checkbutton(dd, text="UI（右側視圖去重）", variable=self.var_dedup_ui).grid(row=0, column=2, sticky="w", padx=6)
        ttk.Checkbutton(dd, text="CSV（寫檔/轉發去重）", variable=self.var_dedup_csv).grid(row=0, column=3, sticky="w", padx=6)
        for i in range(4):
            dd.grid_columnconfigure(i, weight=1)

        # ---- 工具：聊天室 DOM 快照 ----
        tool = ttk.LabelFrame(self.monitor_body, text="診斷工具")
        tool.pack(fill=tk.X, padx=0, pady=6)
        ttk.Button(tool, text="Dump 聊天室 DOM", command=self._request_dump_dom).grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Label(tool, text="（產生 dom_dump/YYMMDD_HHMMSS.html）").grid(row=0, column=1, sticky="w", padx=6)
        for i in range(2):
            tool.grid_columnconfigure(i, weight=1)

        self.var_popup = tk.StringVar(value="floating")
        self.var_auto_close = tk.IntVar(value=6)
        ttk.Label(ui, text="模式").grid(row=0, column=0, sticky="e")
        ttk.Combobox(ui, textvariable=self.var_popup, values=("floating", "toast", "msgbox", "off"),
                     state="readonly", width=12).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(ui, text="自動關閉秒").grid(row=0, column=2, sticky="e")
        ttk.Entry(ui, textvariable=self.var_auto_close, width=10).grid(row=0, column=3, sticky="w", padx=6)
        ttk.Button(ui, text="測試通知",
                   command=lambda: self._notifier().notify("測試通知", "這是一則測試訊息")
                   ).grid(row=0, column=4, sticky="w", padx=8)

        # ---- Telegram ----
        tg = ttk.LabelFrame(self.monitor_body, text="Telegram 轉發")
        tg.pack(fill=tk.X, padx=0, pady=6)
        self.var_tg_enable = tk.BooleanVar(value=True)
        self.var_tg_token = tk.StringVar()
        self.var_tg_chatid = tk.StringVar()
        self.var_tg_parse = tk.StringVar(value="HTML")
        self.var_tg_no_preview = tk.BooleanVar(value=True)
        self.var_tg_retry = tk.IntVar(value=3)
        self.var_tg_timeout = tk.IntVar(value=15)
        self.var_tg_rl_ms = tk.IntVar(value=400)
        self.var_tg_announce = tk.BooleanVar(value=True)
        ttk.Checkbutton(tg, text="啟用", variable=self.var_tg_enable).grid(row=0, column=0, sticky="w", padx=6)
        ttk.Label(tg, text="Bot Token").grid(row=0, column=1, sticky="e")
        ttk.Entry(tg, textvariable=self.var_tg_token, width=entry_width).grid(row=0, column=2, sticky="ew", padx=6)
        ttk.Label(tg, text="Chat ID").grid(row=0, column=3, sticky="e")
        ttk.Entry(tg, textvariable=self.var_tg_chatid, width=max(14, entry_width - 6)).grid(row=0, column=4, sticky="ew", padx=6)
        ttk.Label(tg, text="Parse").grid(row=1, column=1, sticky="e")
        ttk.Combobox(tg, textvariable=self.var_tg_parse, values=("", "HTML", "MarkdownV2"),
                     state="readonly", width=12).grid(row=1, column=2, sticky="w", padx=6)
        ttk.Checkbutton(tg, text="關閉連結預覽", variable=self.var_tg_no_preview).grid(row=1, column=3, sticky="w")
        ttk.Button(tg, text="測試 Telegram", command=self._test_tg).grid(row=1, column=4, sticky="w", padx=6)
        ttk.Label(tg, text="重試").grid(row=2, column=1, sticky="e")
        ttk.Entry(tg, textvariable=self.var_tg_retry, width=8).grid(row=2, column=2, sticky="w")
        ttk.Label(tg, text="逾時秒").grid(row=2, column=3, sticky="e")
        ttk.Entry(tg, textvariable=self.var_tg_timeout, width=8).grid(row=2, column=4, sticky="w")
        ttk.Label(tg, text="節流ms").grid(row=2, column=5, sticky="e")
        ttk.Entry(tg, textvariable=self.var_tg_rl_ms, width=8).grid(row=2, column=6, sticky="w")
        ttk.Checkbutton(tg, text="啟動時公告", variable=self.var_tg_announce).grid(row=2, column=0, sticky="w", padx=6)
        tg.grid_columnconfigure(2, weight=1)
        tg.grid_columnconfigure(4, weight=1)

        # ---- 附件暫存 ----
        attachment_box = ttk.LabelFrame(self.monitor_body, text="附件暫存")
        attachment_box.pack(fill=tk.X, padx=0, pady=6)
        self.var_attachment_retention_days = tk.IntVar(value=7)
        self.var_attachment_cleanup_hours = tk.IntVar(value=24)
        ttk.Label(attachment_box, text="保留天數（0=停用清理）").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        ttk.Spinbox(attachment_box, from_=0, to=3650, textvariable=self.var_attachment_retention_days,
                    width=8).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(attachment_box, text="清理間隔（小時）").grid(row=0, column=2, sticky="e", padx=6)
        ttk.Spinbox(attachment_box, from_=1, to=168, textvariable=self.var_attachment_cleanup_hours,
                    width=8).grid(row=0, column=3, sticky="w", padx=6)
        ttk.Label(attachment_box, text="只清除輸出資料夾內 attachments/ 的過期檔案").grid(
            row=0, column=4, sticky="w", padx=6)
        attachment_box.grid_columnconfigure(4, weight=1)

        # ---- 群組訊息分頁 ----
        left = ttk.Frame(self.tab_groups)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6), pady=4)
        ttk.Label(left, text="群組").pack(anchor="w")
        self.tree_scroll_y = ttk.Scrollbar(left, orient="vertical")
        self.tree = ttk.Treeview(left, columns=("badge",), show="tree headings", height=24,
                                 yscrollcommand=self.tree_scroll_y.set)
        self.tree_scroll_y.config(command=self.tree.yview)
        self.tree.heading("#0", text="名稱")
        self.tree.heading("badge", text="未讀")
        self.tree.column("#0", width=int(self.ui_metrics["group_column_width"]))
        self.tree.column("badge", width=int(self.ui_metrics["badge_column_width"]), anchor="center", stretch=False)
        self.tree.pack(side=tk.LEFT, fill=tk.Y)
        self.tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self.on_group_select)

        right = ttk.Frame(self.tab_groups)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=4)
        hdr = ttk.Frame(right)
        hdr.pack(fill=tk.X)
        ttk.Label(hdr, text="訊息（依群組）").pack(side=tk.LEFT)

        # 右上兩顆：左=清空彈窗、右=清空全部訊息
        ttk.Button(hdr, text="清空全部訊息", command=self._clear_all_group_messages).pack(side=tk.RIGHT, padx=6)
        ttk.Button(hdr, text="清空彈窗", command=self._clear_popups).pack(side=tk.RIGHT, padx=6)

        self.grp_scroll_y = ttk.Scrollbar(right, orient="vertical")
        self.tv_msg = ttk.Treeview(right, columns=("time","content","fwd"), show="headings", height=24, yscrollcommand=self.grp_scroll_y.set)
        self.grp_scroll_y.config(command=self.tv_msg.yview)
        self.tv_msg.heading("time", text="時間")
        self.tv_msg.heading("content", text="內容")
        self.tv_msg.heading("fwd", text="轉發")
        self.tv_msg.column("time", width=int(self.ui_metrics["time_column_width"]), anchor="w", stretch=False)
        self.tv_msg.column("content", width=int(self.ui_metrics["message_content_width"]), anchor="w", stretch=True)
        self.tv_msg.column("fwd", width=int(self.ui_metrics["forward_column_width"]), anchor="center", stretch=False)
        self.tv_msg.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # 轉發欄位樣式：✓(綠) / ✗(橙)
        try:
            self.tv_msg.tag_configure("fwd_yes", background="#eaffea")
            self.tv_msg.tag_configure("fwd_no", background="#fff4e5")
        except Exception:
            pass

        self.grp_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.tab_groups.bind("<Configure>", self._resize_group_columns)

        # ---- 載入設定 ----
        self._load_all_settings()
        self.after(80, self._poll_queue)

    # ---------- 設定（寬鬆 JSONC） ----------
    def _settings_path(self) -> Path:
        return CONFIG_PATH

    def _sanitize_json_like(self, raw: str) -> str:
        raw = raw.replace("\ufeff", "")
        raw = raw.replace("“", '"').replace("”", '"').replace("＂", '"')
        raw = raw.replace("‘", "'").replace("’", "'")
        out = []
        i, n = 0, len(raw)
        in_str = False
        esc = False
        in_sl = False
        in_ml = False
        while i < n:
            ch = raw[i]
            ch2 = raw[i + 1] if i + 1 < n else ""
            if in_sl:
                if ch in "\r\n":
                    in_sl = False
                    out.append(ch)
                i += 1
                continue
            if in_ml:
                if ch == "*" and ch2 == "/":
                    in_ml = False
                    i += 2
                else:
                    i += 1
                continue
            if in_str:
                out.append(ch)
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                i += 1
                continue
            if ch == '"':
                in_str = True
                out.append(ch)
                i += 1
                continue
            if ch == "/" and ch2 == "/":
                in_sl = True
                i += 2
                continue
            if ch == "/" and ch2 == "*":
                in_ml = True
                i += 2
                continue
            out.append(ch)
            i += 1
        s = "".join(out)
        s = re.sub(r",\s*([}\]])", r"\1", s)
        def fix_backslash(m):
            inner = m.group(1)
            inner2 = re.sub(r'(?<!\\)\\(?![\\/\"bfnrtu])', r'\\\\', inner)
            return '"' + inner2 + '"'
        s = re.sub(r'"((?:\\.|[^"\\])*)"', fix_backslash, s)
        return s

    def _load_json(self) -> Dict:
        p = self._settings_path()
        if not p.exists():
            return {}
        try:
            raw = p.read_text(encoding="utf-8")
            raw = self._sanitize_json_like(raw)
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                i = max(e.pos - 40, 0)
                j = min(e.pos + 40, len(raw))
                ctx = raw[i:j].replace("\n", "\\n").replace("\r", "\\r")
                self.log_sys(f"[WARN] JSON 解析失敗：{e}; 位置附近：…{ctx}…\n")
                return json.loads(raw, strict=False)
        except Exception as e:
            self.log_sys(f"[WARN] 讀取設定失敗：{e}\n")
            return {}

    def _save_json(self, data: Dict):
        p = self._settings_path()
        try:
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.log_sys(f"[OK] 設定已儲存：{p.resolve()}\n")
        except Exception as e:
            self.log_sys(f"[ERROR] 儲存設定失敗：{e}\n")

    def _load_all_settings(self):
        cfg = self._load_json()
        urls = cfg.get("urls", {}) or {}
        self.var_base.set(urls.get("base", "https://im.fpcetg.com.tw/"))
        self.var_login.set(urls.get("login", "https://im.fpcetg.com.tw/login"))

        login = cfg.get("login", {}) or {}
        self.var_user.set(login.get("username", ""))
        self.var_pwd.set(login.get("password", ""))
        self.var_u_sel.set(login.get("username_selector", "input[name='account'],#account,input[type='text']"))
        self.var_p_sel.set(login.get("password_selector", "input[name='password'],#password,input[type='password']"))
        self.var_s_sel.set(login.get("submit_selector", "button[type='submit'],button.login,.el-button--primary"))
        self.var_wait.set(int(login.get("login_success_wait_sec", 12)))
        self.var_force.set(bool(login.get("force_relogin", False)))

        br = cfg.get("browser", {}) or {}
        self.var_headless.set(bool(br.get("headless", False)))
        raw_viewport = br.get("viewport") or {"width": 1536, "height": 864}
        try:
            self.browser_viewport = {
                "width": max(800, int(raw_viewport.get("width", 1536))),
                "height": max(600, int(raw_viewport.get("height", 864))),
            }
        except (AttributeError, TypeError, ValueError):
            self.browser_viewport = {"width": 1536, "height": 864}

        watch = cfg.get("watch", {}) or {}
        self.var_outdir.set(str(Path(watch.get("out_dir", "scraped_chats")).resolve()))
        self.var_attachment_retention_days.set(max(0, int(watch.get("attachment_retention_days", 7))))
        self.var_attachment_cleanup_hours.set(max(1, int(watch.get("attachment_cleanup_interval_hours", 24))))

        # 側欄（群組列表）選擇器
        sels = (watch.get("selectors") or {})
        self.sel_group_row = sels.get("group_row", "div.list-row,.list .item,[role='list'] [role='listitem']")
        self.sel_badge     = sels.get("badge", ".el-badge__content,.badge.unread,.presence.online,.status-dot.green")
        self.sel_groupname = sels.get("group_name", ".text.item:nth-child(1),.title,.name,h3,h4,.chat-title")
        self.sel_preview   = sels.get("preview", "span.subinfo,.subinfo,.last-msg,.preview")
        self.sel_side_time = sels.get("time", ".grid-content .subinfo,.el-col.el-col-6 .subinfo,.time,.timestamp")

        # 聊天室逐則訊息選擇器
        msgs = (watch.get("message_selectors") or {})
        self.sel_msg_row    = msgs.get("message_row", ".message-row,.message,[class*='message']")
        self.sel_msg_sender = msgs.get("msg_sender", ".sender,.author,.nickname,.from,.name")
        self.sel_msg_text   = msgs.get("msg_text", ".text,.message-text,.bubble,.content,[class*='text']")
        self.sel_msg_time   = msgs.get("msg_time", ".time,.timestamp,time,[class*='time']")
        self.sel_group_title= msgs.get("active_group", ".chat-title,.header .title,.room-name,h2,h3")

        # 觀測器節流 / 保底
        self.debounce_ms = int(watch.get("debounce_ms", 80))
        self.poll_ms     = int(watch.get("poll_ms", 2000))
        self.debug_flag  = bool(watch.get("debug", False))

        # === Telegram 讀回 UI ===
        tg = cfg.get("telegram", {}) or {}
        self.var_tg_enable.set(bool(tg.get("enabled", self.var_tg_enable.get())))
        self.var_tg_token.set(tg.get("bot_token", ""))
        self.var_tg_chatid.set(tg.get("chat_id", ""))
        self.var_tg_parse.set(tg.get("parse_mode", ""))  # "", "HTML", "MarkdownV2"
        self.var_tg_no_preview.set(bool(tg.get("disable_preview", True)))
        self.var_tg_retry.set(int(tg.get("retry", 3)))
        self.var_tg_timeout.set(int(tg.get("timeout_sec", 15)))
        self.var_tg_rl_ms.set(int(tg.get("rate_limit_ms", 400)))
        self.var_tg_announce.set(bool(tg.get("announce_on_start", True)))
        # Dedup settings (default all False)
        dd = cfg.get("dedup", {}) or {}
        self.var_dedup_master.set(bool(dd.get("enabled", False)))
        self.var_dedup_js.set(bool(dd.get("js", False)))
        self.var_dedup_ui.set(bool(dd.get("ui", False)))
        self.var_dedup_csv.set(bool(dd.get("csv", False)))


        self.log_sys("[INFO] 設定已載入。\n")

    def _gather_cfg(self) -> Dict:
        return {
            "urls": {"base": self.var_base.get().strip(), "login": self.var_login.get().strip()},
            "login": {
                "username": self.var_user.get().strip(),
                "password": self.var_pwd.get(),
                "username_selector": self.var_u_sel.get().strip(),
                "password_selector": self.var_p_sel.get().strip(),
                "submit_selector": self.var_s_sel.get().strip(),
                "login_success_wait_sec": int(self.var_wait.get()),
                "force_relogin": bool(self.var_force.get()),
            },
            "browser": {
                "headless": bool(self.var_headless.get()),
                "storage_state_file": "fpc_state.json",
                "viewport": dict(getattr(self, "browser_viewport", {"width": 1536, "height": 864})),
            },
            "ui": {"popup_mode": self.var_popup.get(), "auto_close_sec": int(self.var_auto_close.get())},
            "watch": {
                "out_dir": str(Path(self.var_outdir.get()).resolve()),
                "debounce_ms": int(self.debounce_ms),
                "poll_ms": int(self.poll_ms),
                "debug": bool(self.debug_flag),
                "attachment_retention_days": max(0, int(self.var_attachment_retention_days.get())),
                "attachment_cleanup_interval_hours": max(1, int(self.var_attachment_cleanup_hours.get())),
                "selectors": {
                    "group_row": self.sel_group_row,
                    "badge": self.sel_badge,
                    "group_name": self.sel_groupname,
                    "preview": self.sel_preview,
                    "time": self.sel_side_time
                },
                "message_selectors": {
                    "message_row": self.sel_msg_row,
                    "msg_sender": self.sel_msg_sender,
                    "msg_text": self.sel_msg_text,
                    "msg_time": self.sel_msg_time,
                    "active_group": self.sel_group_title
                }
            },
            "dedup": {
                "enabled": bool(self.var_dedup_master.get()),
                "js": bool(self.var_dedup_js.get()),
                "ui": bool(self.var_dedup_ui.get()),
                "csv": bool(self.var_dedup_csv.get())
            },
            "telegram": {
                "enabled": bool(self.var_tg_enable.get()),
                "bot_token": self.var_tg_token.get().strip(),
                "chat_id": self.var_tg_chatid.get().strip(),
                "parse_mode": self.var_tg_parse.get().strip(),
                "disable_preview": bool(self.var_tg_no_preview.get()),
                "retry": int(self.var_tg_retry.get()),
                "timeout_sec": int(self.var_tg_timeout.get()),
                "rate_limit_ms": int(self.var_tg_rl_ms.get()),
                "announce_on_start": bool(self.var_tg_announce.get())
            },
        }

    def _save_all_settings(self):
        self._save_json(self._gather_cfg())

    def reload_settings(self):
        """從 config.json 重新讀入到 UI 控制項"""
        self._load_all_settings()
        self.log_sys("[INFO] 設定已重新載入。\n")

    # ---------- CSV 去重 ----------
    def _csv_seen_for(self, path: Path):
        s = self.csv_seen.get(path)
        if s is not None:
            return s
        s = set()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8-sig", newline="") as f:
                    r = csv.reader(f)
                    first = next(r, None)
                    if first and first[0].startswith("# FPC Watch "):
                        _ = next(r, None)
                    for row in r:
                        if len(row) >= 3:
                            t, g, p = row[0], row[1], row[2]
                            s.add((t, g, p))
                        elif len(row) >= 2:
                            # 舊版相容：沒有內容欄就以空字串當內容
                            t, g = row[0], row[1]
                            s.add((t, g, ""))
            except Exception as e:
                self.log_sys(f"[WARN] 載入 CSV 以供查重失敗：{e}\n")
        self.csv_seen[path] = s
        return s

    def _csv_has_rec(self, path: Path, t: str, g: str, p: str) -> bool:
        # 去重鍵：(時間, 群組, 內容)
        return (t, g, p) in self._csv_seen_for(path)

    def _csv_append_row(self, path: Path, row: List[str]):
        new = not path.exists()
        try:
            with open(path, "a", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow([f"# FPC Watch {APP_VERSION}"])
                    w.writerow(["時間", "群組", "內容", "發送者", "未讀/徽章"])
                w.writerow(row)
            # 去重鍵：(時間, 群組, 內容)
            t, g, p = row[0], row[1], row[2] if len(row) > 2 else ""
            self._csv_seen_for(path).add((t, g, p))
        except Exception as e:
            self.log_sys(f"[ERROR] 寫入 CSV 失敗：{e}\n")

    # ---------- UI 輔助 ----------
    def _resize_group_columns(self, event):
        """Keep group/message columns readable as the main window is resized."""
        available = max(640, int(getattr(event, "width", 0) or self.winfo_width()))
        badge_width = int(self.ui_metrics["badge_column_width"])
        group_area = max(220, min(460, int(available * 0.30)))
        self.tree.column("#0", width=max(160, group_area - badge_width - 28))
        self.tree.column("badge", width=badge_width)
        message_area = max(360, available - group_area - 48)
        time_width = max(120, min(190, int(message_area * 0.22)))
        forward_width = int(self.ui_metrics["forward_column_width"])
        content_width = max(220, message_area - time_width - forward_width - 28)
        self.tv_msg.column("time", width=time_width)
        self.tv_msg.column("content", width=content_width)
        self.tv_msg.column("fwd", width=forward_width)

    def browse_out(self):
        p = filedialog.askdirectory(title="選擇輸出資料夾")
        if p:
            self.var_outdir.set(p)

    def log_sys(self, s: str):
        self.syslog = getattr(self, "syslog", None)
        if self.syslog:
            self.syslog.insert(tk.END, s)
            self.syslog.see(tk.END)
        # Keep a durable copy of exactly the runtime diagnostics shown in the UI.
        # Logging failure must never stop monitoring or forwarding.
        try:
            raw_out_dir = self.var_outdir.get().strip() if hasattr(self, "var_outdir") else ""
            out_dir = Path(raw_out_dir or (BASE / "scraped_chats"))
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"system_{datetime.now().strftime('%Y%m%d')}.log"
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(path, "a", encoding="utf-8") as f:
                for line in (s or "").splitlines() or [""]:
                    f.write(f"[{stamp}] {line}\n")
        except Exception:
            pass

    def _append_dedup(self, kind: str, group: str, sent: str, sender: str, text: str, note: str = ""):
        """
        將去重事件寫到「去重」分頁文字框。
        kind: 'JS' / 'UI' / 'CSV'
        """
        line = f"[{kind}] {sent} 〔{group}〕"
        if sender:
            line += f" [{sender}]"
        if text:
            line += f" {text}"
        if note:
            line += f"  -- {note}"
        line = one_line(line)
        try:
            self.txt_dedup.insert(tk.END, line + "\n")
            self.txt_dedup.see(tk.END)
        except Exception:
            pass


    # ---- Dedup helpers ----
    def _dedup_enabled(self) -> bool:
        return bool(self.var_dedup_master.get())

    def _dedup_on(self, kind: str) -> bool:
        if not self._dedup_enabled():
            return False
        k = (kind or "").upper()
        if k == "JS":
            return bool(self.var_dedup_js.get())
        if k == "UI":
            return bool(self.var_dedup_ui.get())
        if k == "CSV":
            return bool(self.var_dedup_csv.get())
        return False

    def _request_dump_dom(self):
        try:
            self._want_dump_dom = True
            self.log_sys("已排程 Dump DOM，請稍候...\n")
        except Exception:
            pass

    def _notifier(self) -> Notifier:
        mode = (self.var_popup.get() or "off")
        secs = int(self.var_auto_close.get() or 0)
        if (self._notifier_inst is None
                or self._notifier_inst.mode != mode.lower()
                or self._notifier_inst.auto_close_sec != secs):
            self._notifier_inst = Notifier(mode, secs)
        return self._notifier_inst

    def _clear_popups(self):
        n = self._notifier()
        if n.mode != "floating":
            messagebox.showinfo("清空彈窗", "僅支援 Floating 模式。")
            return
        try:
            n.clear_all()
        except Exception:
            messagebox.showwarning("清空彈窗", "清空失敗，請再試一次。")

    def _normalize_group(self, raw_name: str, preview: str = ""):
        """回傳 (key, display, people)；display 形如 群組名(人數) 或 群組名。"""
        name = one_line(raw_name or "")
        pv = one_line(preview or "")
        if pv and pv in name:
            name = name.split(pv, 1)[0]
        name = re.sub(r"[·•⋅。]+\s*.*$", "", name).strip()
        m = re.search(r"[（(]\s*(\d+)\s*[)）]\s*$", name)
        people = int(m.group(1)) if m else None
        base = re.sub(r"\s*[（(]\s*\d+\s*[)）]\s*$", "", name).strip()
        display = f"{base}({people})" if people is not None else base
        key = base.lower()
        return key, display, people

    def _parse_unread(self, badge: str) -> int:
        m = re.search(r"\d+", badge or "")
        return int(m.group(0)) if m else 0

    def _ensure_group_row(self, raw_name: str, badge: str = "", preview: str = "") -> str:
        key, display, _ = self._normalize_group(raw_name, preview)
        unread = self._parse_unread(badge)
        if unread <= 0:
            if self.tree.exists(key):
                try: self.tree.delete(key)
                except Exception: pass
            self.group_badge.pop(key, None)
            self.group_last_ts.pop(key, None)
            return key
        if not self.tree.exists(key):
            self.tree.insert("", tk.END, iid=key, text=display, values=("",))
            self.group_last_ts[key] = 0.0
        else:
            self.tree.item(key, text=display)
        epoch_now = time.time()
        if epoch_now >= self.group_last_ts.get(key, 0.0):
            self.group_last_ts[key] = epoch_now
            self.group_badge[key] = str(unread)
            self.tree.set(key, "badge", str(unread))
        return key

    def _append_group_message(self, group_name: str, content: str, sender: str, ts_display: str):
        """
        右側訊息 UI 去重：同群組若 (時間, 內容, 發送者) 與上一筆相同，不重複顯示
        """
        key, display, _ = self._normalize_group(group_name, "")
        if not self.tree.exists(key):
            self.tree.insert("", tk.END, iid=key, text=display, values=("",))
            self.group_last_ts[key] = 0.0

        sig = (ts_display, one_line(content, keep_newline=True), one_line(sender))
        if self.ui_last_sig.get(key) == sig:
            # UI 去重命中：同群組連續 (時間, 內容, 發送者) 相同
            try:
                self._append_dedup("UI", display, ts_display, sender, content, "右側連續相同，略過顯示")
            except Exception:
                pass
            return
        self.ui_last_sig[key] = sig

        fwd = self._sent_done('tg', display, one_line(content, keep_newline=True))
        mark = '✓' if fwd else '✗'
        row = (ts_display, one_line(content, keep_newline=True), mark)
        self.group_logs.setdefault(key, []).append(row)

        if getattr(self, 'cur_group_display', '') == key:
            try:
                self.tv_msg.insert("", tk.END, values=row, tags=("fwd_yes" if mark=="✓" else "fwd_no",))
            except Exception:
                pass

    def on_group_select(self, _evt):
        sel = self.tree.selection()
        if not sel:
            return
        key = sel[0]
        self.cur_group_display = key
        for iid in self.tv_msg.get_children():
            self.tv_msg.delete(iid)
        for (t, c, m) in self.group_logs.get(key, []):
            tag = ("fwd_yes" if m=="✓" else "fwd_no")
            self.tv_msg.insert("", tk.END, values=(t, c, m), tags=(tag,))
        try:
            last = self.tv_msg.get_children()[-1]
            self.tv_msg.see(last)
        except Exception:
            pass

    def _clear_all_group_messages(self):
        if not (self.group_logs or self.tree.get_children()):
            messagebox.showinfo("清空全部訊息", "目前沒有可清空的群組/訊息。")
            return
        if not messagebox.askyesno("清空全部訊息", "確定要清空所有群組與訊息嗎？（未來新事件會自動重新建立）"):
            return
        try:
            for iid in self.tv_msg.get_children():
                self.tv_msg.delete(iid)
            for iid in self.tree.get_children():
                self.tree.delete(iid)
            self.group_logs.clear()
            self.group_badge.clear()
            self.group_last_ts.clear()
            self.ui_last_sig.clear()
            self.tree.selection_remove(self.tree.selection())
            messagebox.showinfo("清空完成", "已清空左側群組與右側訊息。")
        except Exception as e:
            messagebox.showerror("清空失敗", f"{e}")

    def _test_tg(self):
        cfg = self._gather_cfg()
        tg = self._build_tg(cfg)
        if not tg.enabled:
            messagebox.showinfo("Telegram", "尚未啟用 Telegram。")
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        styled = format_for_tg("測試訊息", "", "UI 測試成功", ts, tg.parse_mode, "BOT")
        plain = format_for_tg("測試訊息", "", "UI 測試成功", ts, None, "BOT")
        ok = tg.send_text(styled, fallback_plain=plain)
        messagebox.showinfo("Telegram", "已送出。" if ok else "送出失敗，請檢查 Token/ChatID/網路。")

    # ---------- 執行控制 ----------
    def start_watch(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("提示", "監控已在執行中。")
            return
        cfg = self._gather_cfg()
        self._save_all_settings()
        self.log_sys(f"[INFO] Version: {APP_VERSION}\n")
        self.log_sys("[INFO] 準備啟動監控...\n")

        def runner():
            asyncio.run(self._monitor_loop_ui(cfg))

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        self.worker_thread = t

    def stop_watch(self):
        messagebox.showinfo("停止", "結束請直接關閉程式或中斷執行。")

    def _push_from_worker(self, evt: dict):
        self.ui_queue.put(evt)

    def _poll_queue(self):
        try:
            while True:
                evt = self.ui_queue.get_nowait()
                t = evt.get("type")
                if t == "log":
                    self.log_sys(evt.get("text", ""))
                elif t == "side":  # 側欄：更新群組行與未讀
                    name = one_line(evt.get("name", ""))
                    badge = one_line(evt.get("badge", ""))
                    preview = one_line(evt.get("preview", ""))
                    self._ensure_group_row(name, badge, preview)
                elif t == "msg":   # 聊天室訊息：右側寫入
                    grp = one_line(evt.get("group", ""))
                    snd = one_line(evt.get("sender", ""))
                    txt = one_line(evt.get("text", ""), keep_newline=True)
                    sent = one_line(evt.get("time", "")) or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self._append_group_message(grp, txt, snd, sent)
                elif t == "dedup":
                    kind = evt.get("kind", "")
                    grp  = one_line(evt.get("group", ""))
                    snd  = one_line(evt.get("sender", ""))
                    txt  = one_line(evt.get("text", ""))
                    sent = one_line(evt.get("time", "")) or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    note = one_line(evt.get("note", ""))
                    self._append_dedup(kind, grp, sent, snd, txt, note)
        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    # ---------- Telegram/Notifier Builder ----------
    def _build_tg(self, cfg: Dict) -> TelegramForwarder:
        return TelegramForwarder(cfg)

    # ---------- 監控核心 ----------
    async def _monitor_loop_ui(self, cfg: Dict):
        try:
            from playwright.async_api import async_playwright  # noqa
        except Exception:
            self._push_from_worker({"type": "log", "text": "[ERROR] Playwright 未安裝。\n"})
            return

        urls = cfg.get("urls", {}) or {}
        base_url = urls.get("base", "https://im.fpcetg.com.tw/")
        login_url = urls.get("login", "https://im.fpcetg.com.tw/login")

        tg = self._build_tg(cfg)
        notifier = self._notifier()
        # === DEBUG: 啟動時輸出通知/TG狀態 ===
        try:
            self._push_from_worker({"type":"log","text":f"[DEBUG] 通知模式: {notifier.mode}, 自動關閉秒: {notifier.auto_close_sec}\n"})
        except Exception: pass
        try:
            _cid = (tg.chat_id or "")
            _cid_mask = (_cid[:6] + "***") if _cid else ""
            self._push_from_worker({"type":"log","text":f"[DEBUG] Telegram: {'ON' if tg.enabled else 'OFF'}, parse={tg.parse_mode or 'plain'}, chat_id={_cid_mask}\n"})
        except Exception: pass
        # --- 監控啟動時記錄通知與 TG 狀態 ---
        self._push_from_worker({"type": "log", "text": f"[INFO] 通知模式: {notifier.mode}, 自動關閉秒: {notifier.auto_close_sec}\n"})
        self._push_from_worker({"type": "log", "text": f"[INFO] Telegram: {'ON' if tg.enabled else 'OFF'}, parse={tg.parse_mode or 'plain'}, chat_id={(tg.chat_id or '')[:6]}***\n"})

        watch = cfg.get("watch", {}) or {}
        out_dir = Path(watch.get("out_dir", "scraped_chats"))
        out_dir.mkdir(parents=True, exist_ok=True)
        self._push_from_worker({"type": "log", "text": f"[INFO] Version: {APP_VERSION}\n"})
        attachment_retention_days = max(0, int(watch.get("attachment_retention_days", 7)))
        attachment_cleanup_hours = max(1, int(watch.get("attachment_cleanup_interval_hours", 24)))

        async def run_attachment_cleanup() -> float:
            try:
                if attachment_retention_days <= 0:
                    self._push_from_worker({"type": "log", "text": "[FILE] attachment cleanup disabled\n"})
                else:
                    result = await asyncio.to_thread(
                        _cleanup_expired_attachments, out_dir, attachment_retention_days
                    )
                    self._push_from_worker({"type": "log", "text":
                        f"[FILE] attachment cleanup: removed={result['removed_files']}, "
                        f"bytes={result['removed_bytes']}, retention={attachment_retention_days}d, "
                        f"errors={len(result['errors'])}\n"})
            except Exception as exc:
                self._push_from_worker({"type": "log", "text":
                    f"[FILE][WARN] attachment cleanup failed; monitoring continues: {exc}\n"})
            return time.monotonic() + attachment_cleanup_hours * 3600

        next_attachment_cleanup_at = await run_attachment_cleanup()

        # CSV 異動：訊息模式另存 messages_YYYYMMDD.csv
        def today_csv() -> Path:
            return out_dir / f"messages_{datetime.now().strftime('%Y%m%d')}.csv"

        base = getattr(sys, "_MEIPASS", str(BASE))
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(base, "ms-playwright")

        async with async_playwright() as p:
            br, ctx, pg, ok_login = await ensure_logged_in(
                p, cfg, notifier, base_url, login_url,
                Path((cfg.get("browser", {}) or {}).get("storage_state_file", "fpc_state.json")),
                bool((cfg.get("browser", {}) or {}).get("headless", False))
            )
            if not ok_login:
                await br.close()
                self._push_from_worker({"type": "log", "text": "[ERROR] 登入失敗，監控結束。\n"})
                return

            # 將頁面 console 拉回 UI（debug）
            def _pg_console_handler(m):
                typ = getattr(m, 'type', 'log')
                txt = getattr(m, 'text', '') if hasattr(m, 'text') else ''
                if callable(txt):
                    try:
                        txt = m.text()
                    except Exception:
                        txt = ''
                if 'Failed to load resource' in str(txt) and '404' in str(txt):
                    self._push_from_worker({'type': 'log', 'text': f"[PAGE][warn404] {txt}\n"})
                    return
                self._push_from_worker({'type': 'log', 'text': f"[PAGE][{typ}] {txt}\n"})
            pg.on('console', _pg_console_handler)


            start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            notifier.notify("登入成功", f"開始監控與轉發（{start_ts}）")
            if tg.enabled and bool((cfg.get("telegram", {}) or {}).get("announce_on_start", True)):
                try:
                    start_msg = format_for_tg("登入成功", "", "已開始監控並轉發", start_ts, tg.parse_mode, "SYSTEM")
                    tg.send_text(start_msg, fallback_plain=format_for_tg("登入成功", "", "已開始監控並轉發", start_ts, None, "SYSTEM"))
                except Exception:
                    pass
            self._push_from_worker({"type": "log", "text": "[INFO] 監控已啟動。\n"})

            # Python 端兩個佇列：側欄(side)與訊息(msg)
            py_side_q: asyncio.Queue = asyncio.Queue()
            py_msg_q: asyncio.Queue = asyncio.Queue()
            py_dedup_q: asyncio.Queue = asyncio.Queue()  # 新增：去重事件

            async def py_push_side(evt):
                await py_side_q.put(evt)

            async def py_push_msg(evt):
                await py_msg_q.put(evt)

            async def py_push_dedup(evt):
                # evt: {kind, group, time, sender, text, note?}
                await py_dedup_q.put(evt)

            await pg.expose_function("pyPushSide", py_push_side)
            await pg.expose_function("pyPushMsg", py_push_msg)
            await pg.expose_function("pyPushDedup", py_push_dedup)

            # 不點入群組：被動攔截頁面本來就收到的 API/WebSocket 訊息。
            # 只有群組側欄剛變動時才接受候選訊息，避免舊資料或其他 API 造成重複轉發。
            pending_groups = PendingPreviewBuffer()
            ingress_reservations = MessageIngressReservations()
            # A getMessageResponse can arrive before the separate channel-list event
            # that maps its CID to the sidebar title. Keep only these unresolved
            # candidates briefly, then retry once the mapping is known.
            unresolved_network_candidates: Dict[str, List[Dict]] = {}
            # A recipe is learned from a normal message response, then replayed with
            # the same authenticated browser context.  Replaying an HTTP request does
            # not click, focus, select, or otherwise alter a chat in the page UI.
            latest_message_recipes: List[Dict] = []
            backfill_locks: Dict[str, asyncio.Lock] = {}
            channel_names: Dict[str, str] = {}
            diag_dir = out_dir / "network_diagnostics"
            diag_dir.mkdir(parents=True, exist_ok=True)

            def learn_latest_message_recipe(response, candidates: List[Dict]):
                """Keep only request data needed to replay a latest-message query.

                Authentication remains inside Playwright's RequestContext; no cookies
                or authorization headers are copied into diagnostics or configuration.
                """
                if not candidates:
                    return
                try:
                    req = response.request
                    recipe = {
                        "url": req.url,
                        "method": req.method,
                        "post_data": req.post_data or "",
                        "group": candidates[-1].get("group", ""),
                    }
                except Exception:
                    return
                if not recipe["group"] or recipe["method"] not in {"GET", "POST", "PUT"}:
                    return
                signature = (recipe["method"], recipe["url"], recipe["post_data"], recipe["group"])
                if any((x["method"], x["url"], x["post_data"], x["group"]) == signature
                       for x in latest_message_recipes):
                    return
                latest_message_recipes.append(recipe)
                del latest_message_recipes[:-12]
                try:
                    with open(diag_dir / f"message_api_{datetime.now().strftime('%Y%m%d')}.jsonl", "a", encoding="utf-8") as df:
                        df.write(json.dumps({
                            "at": datetime.now().isoformat(timespec="seconds"),
                            "app_version": APP_VERSION,
                            "method": recipe["method"],
                            "url": _diagnostic_url(recipe["url"]),
                            "group": recipe["group"],
                            "has_body": bool(recipe["post_data"]),
                        }, ensure_ascii=False) + "\n")
                except Exception:
                    pass
                self._push_from_worker({"type": "log", "text":
                    f"[NET] learned reusable message request for {recipe['group']} (no UI interaction)\n"})

            async def inspect_network_payload(payload, source: str, response=None):
                try:
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    _remember_channel_names(payload, channel_names)

                    async def enqueue_if_pending(item: Dict, allow_defer: bool = True) -> bool:
                        key = self._normalize_group(one_line(item.get("group", "")))[0]
                        was_reserved = ingress_reservations.contains(item)
                        claimed = _claim_passive_candidate(
                            pending_groups, key, item, ingress_reservations
                        )
                        if claimed:
                            await py_msg_q.put(claimed)
                            return True
                        if was_reserved:
                            self._push_from_worker({"type": "log", "text":
                                f"[NET][SKIP] duplicate server message before pending claim: "
                                f"{item.get('message_id', '')}\n"})
                            return False
                        cid = str(item.get("cid", "") or "")
                        # Defer only an ID-only candidate while a sidebar change is
                        # pending. This avoids replaying unrelated historical data.
                        if allow_defer and cid and item.get("group") == cid and pending_groups:
                            waiting = unresolved_network_candidates.setdefault(cid, [])
                            waiting.append({
                                "item": dict(item),
                                "pending_refs": pending_groups.snapshot(),
                            })
                            if len(waiting) > 256:
                                del waiting[:-256]
                                self._push_from_worker({"type": "log", "text":
                                    f"[NET][WARN] deferred CID queue capped at 256 for {cid}\n"})
                            self._push_from_worker({"type": "log", "text":
                                f"[NET] full payload received for CID {cid}; waiting channel title mapping\n"})
                        return False

                    # The channel list normally follows getMessageResponse. Retry
                    # ID-only candidates after this payload teaches us its title.
                    for cid in list(unresolved_network_candidates):
                        group = channel_names.get(cid)
                        if not group:
                            continue
                        waiting = unresolved_network_candidates.pop(cid)
                        group_key = self._normalize_group(group)[0]
                        for deferred in waiting:
                            item = deferred["item"]
                            target = next((
                                pending for pending in deferred["pending_refs"]
                                if pending_groups.contains(pending)
                                and self._normalize_group(pending.get("group", ""))[0] == group_key
                            ), None)
                            if target is None:
                                continue
                            item["group"] = group
                            claimed = _claim_backfill_candidate(
                                pending_groups, target, item, ingress_reservations
                            )
                            if claimed is not None:
                                await py_msg_q.put(claimed)
                    candidates = _network_message_candidates(payload, source, channel_names)
                    with open(diag_dir / f"network_{datetime.now().strftime('%Y%m%d')}.jsonl", "a", encoding="utf-8") as df:
                        df.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "source": source,
                                             "app_version": APP_VERSION,
                                             "candidate_count": len(candidates),
                                             "top_keys": list(payload.keys())[:30] if isinstance(payload, dict) else []}, ensure_ascii=False) + "\n")
                    if "event:message" in source and isinstance(payload, dict):
                        raw_data = payload.get("data")
                        envelope = {
                            "at": datetime.now().isoformat(timespec="seconds"),
                            "app_version": APP_VERSION,
                            "command": payload.get("command"),
                            "encryptVersion": payload.get("encryptVersion"),
                            "data_type": type(raw_data).__name__,
                            "data_length": len(raw_data) if isinstance(raw_data, (str, bytes, list, dict)) else None,
                            "data_prefix": raw_data[:16] if isinstance(raw_data, str) else None,
                            "data_keys": list(raw_data.keys())[:30] if isinstance(raw_data, dict) else [],
                            "data_shape": _json_shape(raw_data),
                        }
                        with open(diag_dir / f"message_envelopes_{datetime.now().strftime('%Y%m%d')}.jsonl", "a", encoding="utf-8") as df:
                            df.write(json.dumps(envelope, ensure_ascii=False) + "\n")
                    if response is not None:
                        learn_latest_message_recipe(response, candidates)
                    for item in candidates:
                        await enqueue_if_pending(item)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return
                except Exception as e:
                    self._push_from_worker({"type": "log", "text": f"[NET][WARN] payload parse failed: {e}\n"})

            async def inspect_response(response):
                url = response.url
                if not re.search(r"chat|message|conversation|channel|socket", url, re.I):
                    return
                try:
                    if "json" in (response.headers.get("content-type", "").lower()):
                        await inspect_network_payload(await response.json(), f"http:{url}", response)
                except Exception:
                    return

            async def backfill_last_message(pending: Dict):
                """Fetch one latest message for a sidebar group without changing page UI."""
                group = pending["group"]
                key = self._normalize_group(group)[0]
                if not key:
                    return
                lock = backfill_locks.setdefault(key, asyncio.Lock())
                async with lock:
                    # A passive payload may resolve this event while it waits
                    # behind an earlier dense event from the same group.
                    if not pending_groups.contains(pending):
                        return
                    for recipe in reversed(latest_message_recipes):
                        url = _replace_group_in_request(recipe["url"], recipe["group"], group)
                        data = _replace_group_in_request(recipe["post_data"], recipe["group"], group)
                        try:
                            kwargs = {"method": recipe["method"], "timeout": 8000}
                            if recipe["method"] != "GET" and data:
                                kwargs["data"] = data
                            response = await ctx.request.fetch(url, **kwargs)
                            if not response.ok:
                                continue
                            payload = await response.json()
                            candidates = _network_message_candidates(
                                payload, "backfill", channel_names
                            )
                            claimed_batch = _claim_backfill_batch(
                                pending_groups, key, candidates, ingress_reservations
                            )
                            if claimed_batch:
                                for claimed in claimed_batch:
                                    await py_msg_q.put(claimed)
                                self._push_from_worker({"type": "log", "text":
                                    f"[NET] fetched {len(claimed_batch)} full message(s) for "
                                    f"{group} without opening the group\n"})
                                return
                            if _candidate_for_group(candidates, group) is not None:
                                self._push_from_worker({"type": "log", "text":
                                    f"[NET][SKIP] pending already delivered or message already "
                                    f"reserved for {group}; backfill discarded\n"})
                                return
                        except Exception as e:
                            self._push_from_worker({"type": "log", "text":
                                f"[NET][WARN] latest-message backfill failed for {group}: {e}\n"})
                    self._push_from_worker({"type": "log", "text":
                        f"[NET] no reusable full-message API yet for {group}; preview withheld\n"})

            def on_websocket(ws):
                def frame_received(frame):
                    event = _socketio_event_name(frame)
                    source = f"ws:{ws.url}" + (f" event:{event}" if event else "")
                    for payload in _socketio_json_payloads(frame):
                        asyncio.create_task(inspect_network_payload(payload, source))
                ws.on("framereceived", frame_received)

            pg.on("response", lambda response: asyncio.create_task(inspect_response(response)))
            pg.on("websocket", on_websocket)
            self._push_from_worker({"type": "log", "text": "[NET] passive API/WebSocket monitor enabled (no group click)\n"})
            # Socket.IO normally connects during ensure_logged_in(), before these
            # handlers are registered. Reload once to establish an observed socket
            # without clicking, selecting, or opening any chat group.
            try:
                await pg.reload(wait_until="domcontentloaded", timeout=15000)
                self._push_from_worker({"type": "log", "text": "[NET] page reloaded; Socket.IO capture is now attached\n"})
            except Exception as e:
                self._push_from_worker({"type": "log", "text": f"[NET][WARN] Socket.IO capture reload failed: {e}\n"})

            # The selected-chat DOM has no stable server message ID. Sending it
            # alongside HTTP/WebSocket/backfill creates two identities for one
            # message, so it is intentionally not attached in unopened-group mode.
            # ---- ①b 直接從側欄預覽推播 msg（預覽非空且未讀>0）----
            await pg.evaluate("""
              () => {
                const delay = (ms) => new Promise(r => setTimeout(r, ms));
                const key = (t) => (t||\"\").trim().slice(0,160) + \"::\" + (t||\"\").length;

                const pickRows = () => Array.from(document.querySelectorAll('.text.item .list-row'))
                  .filter(r => r.querySelector('.ellipsis1') && r.querySelector('.subinfo'));

                const getName  = (row) => (row.querySelector('.ellipsis1')?.textContent || '').trim();
                const getPrev  = (row) => (row.querySelector('.subinfo')?.textContent  || '').trim();
                const getBadge = (row) => (row.querySelector('.el-badge__content')?.textContent || '').trim();
                const getTime  = (row) => (row.querySelector('.grid-content .subinfo, .el-col.el-col-6 .subinfo, .time, .timestamp')?.textContent || '').trim();

                const setup = async () => {
                  for (let i=0;i<40;i++) { // 最多等 8 秒
                    if (pickRows().length) break;
                    await delay(200);
                  }
                  const lastSnapshot = new Map();
                  const lastUnread = new Map();
                  const push = (name, prev, time, badge, eventCount) => {
                    const eventToken = [name, prev, time || '', badge || ''].join('||');
                    const payload = { type: 'side_preview', group: name, sender: '', text: prev,
                                      time: time || '', badge: badge || '', event_token: eventToken,
                                      event_count: eventCount || 1 };
                    try { window.pyPushMsg(payload); } catch (e) {}
                    console.log('[MSG][side]', JSON.stringify(payload));
                  };
                  const scan = () => {
                    const rows = pickRows();
                    for (const r of rows) {
                      const name = getName(r), prev = getPrev(r), badge = getBadge(r), time = getTime(r);
                      if (!name || !prev) continue;
                      const unread = parseInt((badge||'0').replace(/[^0-9]/g,''), 10) || 0;
                      const previousUnread = lastUnread.get(name);
                      lastUnread.set(name, unread);
                      const k = name + '||' + key(prev) + '||' + time + '||' + badge;
                      if (unread <= 0) {
                        lastSnapshot.set(name, k);
                        continue;
                      }
                      if (lastSnapshot.get(name) === k) continue;
                      lastSnapshot.set(name, k);
                      const eventCount = previousUnread !== undefined && unread > previousUnread
                        ? Math.min(50, unread - previousUnread) : 1;
                      push(name, prev, time, badge, eventCount);
                    }
                  };
                  setTimeout(scan, 200);
                  let settleTimer = null;
                  const scheduleScan = () => {
                    if (settleTimer !== null) clearTimeout(settleTimer);
                    settleTimer = setTimeout(() => {
                      settleTimer = null;
                      scan();
                    }, 250);
                  };
                  const mo = new MutationObserver(scheduleScan);
                  mo.observe(document.body, { childList: true, subtree: true, characterData: true });
                  setInterval(scan, 3000);
                };
                setup();
              }
            """)

            await pg.evaluate("""
              (sel) => {
                const rows  = sel.rows,  badgeS = sel.badge, nameS = sel.name, prevS = sel.prev, timeS = sel.time;
                const debounceMs = sel.debounceMs || 80;
                const DEBUG = !!sel.debug;

                const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
                const qFirst = (scope, sels)=>{
                  for (const s of sels){
                    const el = scope.querySelector(s);
                    if (el && visible(el)) return el;
                  }
                  return null;
                };
                const text = (el)=> (el && el.innerText ? el.innerText : "").replace(/\\s+/g," ").trim();

                async function snapshotRow(row){
                  const nameEl = qFirst(row, nameS)
                                || row.querySelector('.text.item .el-col.el-col-13 > .ellipsis1')
                                || row.querySelector('.text.item [class^="ellipsis"]')
                                || row;
                  const prevEl = qFirst(row, prevS) || row;
                  const timeEl = qFirst(row, timeS) || null;
                  const badgeEl= qFirst(row, badgeS) || null;
                  const data = {
                    name: text(nameEl),
                    preview: text(prevEl),
                    badge: text(badgeEl),
                    time: text(timeEl)
                  };
                  if (!data.name && !data.preview) return null;
                  if (DEBUG) console.log("[SIDE]", data);
                  return data;
                }

                let scheduled = false;
                async function scanAll(){
                  scheduled = false;
                  const arr = Array.from(document.querySelectorAll(rows.join(',')));
                  for (const r of arr){
                    const data = await snapshotRow(r);
                    if (data) { window.pyPushSide(data); }
                  }
                }
                const schedule = ()=>{ if (!scheduled){ scheduled=true; setTimeout(scanAll, debounceMs); }};

                scanAll();
                const obs = new MutationObserver(schedule);
                obs.observe(document.body, {subtree:true, childList:true, attributes:true, characterData:true});
                window.__imSidebarObs = obs;
              }
            """, {
                "rows":  [s.strip() for s in self.sel_group_row.split(",") if s.strip()],
                "badge": [s.strip() for s in self.sel_badge.split(",") if s.strip()],
                "name":  [s.strip() for s in self.sel_groupname.split(",") if s.strip()],
                "prev":  [s.strip() for s in self.sel_preview.split(",") if s.strip()],
                "time":  [s.strip() for s in (self.sel_side_time.split(",") if self.sel_side_time else []) if s.strip()],
                "debounceMs": int(self.debounce_ms),
                "debug": bool(self.debug_flag)
            })

            # ---- ② 聊天室逐則訊息觀測器（高保真）----
            
            await pg.evaluate("""
              async (sel) => {
                const delay = (ms) => new Promise(r => setTimeout(r, ms));
                const clickByText = async (text) => {
                  const nodes = Array.from(document.querySelectorAll('div,span,button,a'));
                  const el = nodes.find(e => (e.textContent || '').trim().includes(text));
                  if (el) el.click();
                  return !!el;
                };
                await clickByText('對話');
                await delay(300);
                const collectUnread = () => {
                  const rows = Array.from(document.querySelectorAll('.text.item .list-row'));
                  const list = [];
                  for (const row of rows) {
                    const nameEl = row.querySelector('.ellipsis1');
                    const badge  = row.querySelector('.el-badge__content');
                    const name   = nameEl ? nameEl.textContent.trim() : '';
                    const unread = badge ? parseInt((badge.textContent || '0').trim(), 10) : 0;
                    if (name && Number.isFinite(unread) && unread > 0) {
                      list.push({ name, unread });
                    }
                  }
                  list.sort((a, b) => b.unread - a.unread);
                  return list;
                };
                let last = JSON.stringify([]);
                const push = (items) => {
                  try { window.pyPushMsg({ type: 'unread', items }); } catch (e) {}
                  console.log('[Watcher] 未讀清單', JSON.stringify(items));
                };
                const first = collectUnread();
                last = JSON.stringify(first);
                push(first);
                setInterval(() => {
                  const cur = collectUnread();
                  const js = JSON.stringify(cur);
                  if (js !== last) { last = js; push(cur); }
                }, 5000);
              }
            """, {
                "rowS":  [s.strip() for s in self.sel_msg_row.split(",") if s.strip()],
                "sndS":  [s.strip() for s in self.sel_msg_sender.split(",") if s.strip()],
                "txtS":  [s.strip() for s in self.sel_msg_text.split(",") if s.strip()],
                "timeS": [s.strip() for s in self.sel_msg_time.split(",") if s.strip()],
                "titleS":[s.strip() for s in self.sel_group_title.split(",") if s.strip()],
                "debounceMs": int(self.debounce_ms),
                "pollMs": int(self.poll_ms),
                "debug": bool(self.debug_flag),
                "dedupJS": self._dedup_on("JS")
            })

            # 2 秒緩衝：避免一開始把舊訊息全發出去
            forward_ready_at = time.time() + 2.0

            try:
                while True:
                    if time.monotonic() >= next_attachment_cleanup_at:
                        next_attachment_cleanup_at = await run_attachment_cleanup()
                    # 若 UI 要求 Dump DOM，抓取聊天面板或整頁 HTML
                    if self._want_dump_dom:
                        try:
                            html = await pg.evaluate("""() => {
                                const panel = document.querySelector('[class*="chat"] [class*="message-list"], [role="main"], main, body');
                                return (panel ? panel.outerHTML : document.documentElement.outerHTML);
                            }""")
                            
                            # DOM dump is read-only.  This legacy observer body is
                            # retained for diagnostics but must never attach a
                            # second pyPushMsg producer.
                            await pg.evaluate("""
                              () => {
                                return false;
                                const delay = (ms) => new Promise(r => setTimeout(r, ms));
                                const pickRoot = () => {
                                  const cs = document.querySelector("#cs");
                                  if (!cs) return null;
                                  return (
                                    cs.querySelector(".el-scrollbar__view.view-box") ||
                                    cs.querySelector(".view-box") ||
                                    cs.querySelector("[class*='scrollbar__view']") ||
                                    cs.querySelector("[class*='virtual'],[class*='infinite'],[class*='list']") ||
                                    cs
                                  );
                                };

                                const getGroupName = () => {
                                  const cand = document.querySelector("#chat-header .title")
                                            || document.querySelector(".title");
                                  return cand ? (cand.textContent || "").trim() : "";
                                };

                                const any = (node, sel) => node.querySelector(sel);
                                const textOf = (el) => (el ? (el.innerText || el.textContent || "").trim() : "");

                                const setup = async () => {
                                  let root = null;
                                  for (let i=0;i<50;i++) { // 最多等 10 秒
                                    root = pickRoot();
                                    if (root) break;
                                    await delay(200);
                                  }
                                  if (!root) {
                                    console.log("[MSG] root not found (#cs)");
                                    return false;
                                  }
                                  console.log("[MSG] watcher attached on", root.className || root.id || root.tagName);

                                  const seen = new Set();
                                  const keyOf = (s) => (s||"").trim().slice(0,200)+"::"+(s||"").length;

                                  const extract = (node) => {
                                    // 常見訊息內容選擇器盡量都試
                                    const textEl =
                                      any(node, ".channel-cell-content") ||
                                      any(node, ".message-content") ||
                                      any(node, ".msg-content") ||
                                      any(node, "[class*='cell-content']") ||
                                      any(node, "[class*='message'] [class*='content']") ||
                                      node;
                                    const text = textOf(textEl);

                                    const nameEl =
                                      any(node, ".channel-cell-name") ||
                                      any(node, ".name, .sender, .username") ||
                                      any(node, "[class*='author'],[class*='sender'],[class*='from']");
                                    const sender = textOf(nameEl);

                                    let time = "";
                                    const row = node.closest(".el-row") || node.parentElement;
                                    if (row) {
                                      const tEl = row.querySelector(".channel-cell-time,[class*='time']");
                                      time = textOf(tEl);
                                    }
                                    return { text, sender, time };
                                  };

                                  const toMsgNodes = () => {
                                    const cs = document.querySelector("#cs") || document;
                                    // 訊息容器多個 class 版本都嘗試
                                    const candidates = Array.from(cs.querySelectorAll(
                                      ".channel-cell, .channel-media, .message, [class*='message'], [class*='cell']"
                                    ));
                                    // 若抓不到則退而求其次：抓看起來像訊息行、內含內容節點者
                                    if (candidates.length === 0) {
                                      return Array.from(cs.querySelectorAll(".channel-cell-content, .message-content, .msg-content"))
                                        .map(el => el.closest("div"));
                                    }
                                    return candidates;
                                  };

                                  const push = (item) => {
                                    const payload = {
                                      type: "msg",
                                      group: getGroupName(),
                                      sender: item.sender || "",
                                      text: item.text || "",
                                      time: item.time || ""
                                    };
                                    try { window.pyPushMsg(payload); } catch(e) {}
                                    console.log("[MSG]", JSON.stringify(payload));
                                  };

                                  let lastCount = -1;
                                  const scan = () => {
                                    const list = toMsgNodes();
                                    if (list.length !== lastCount) {
                                      console.log("[MSG][scan] nodes:", list.length);
                                      lastCount = list.length;
                                    }
                                    for (const n of list) {
                                      if (!n) continue;
                                      const item = extract(n);
                                      const k = keyOf(item.text);
                                      if (!item.text || seen.has(k)) continue;
                                      seen.add(k);
                                      push(item);
                                    }
                                  };

                                  // 初次掃描
                                  setTimeout(scan, 300);

                                  // 監聽 DOM 變化
                                  const mo = new MutationObserver(() => scan());
                                  mo.observe(root, { childList: true, subtree: true, characterData: true });

                                  // 滾動時有些站才會載入（虛擬清單），定時補掃
                                  setInterval(scan, 3000);
                                  return true;
                                };

                                setup();
                              }
                            """)
                            # 側欄預覽只由前面的 side_preview 觀測器送入。
                            # 舊版此處另有一個直接當成完整訊息的觀測器，會與 8 秒補抓流程競爭，
                            # 並以「群組＋相同預覽文字」吃掉連續照片事件。



                            # (moved to module top)
                            out_dir = os.path.join(os.getcwd(), "dom_dump")
                            os.makedirs(out_dir, exist_ok=True)
                            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                            out_path = os.path.join(out_dir, f"chat_dom_{ts}.html")
                            with open(out_path, "w", encoding="utf-8") as f:
                                f.write(html)
                            self._push_from_worker({"type":"log", "text": f"[DOM] 已輸出：{out_path}\n"})
                        except Exception as e:
                            self._push_from_worker({"type":"log", "text": f"[DOM] Dump 失敗：{e}\n"})
                        finally:
                            self._want_dump_dom = False

                    # 先處理側欄
                    try:
                        evt = await asyncio.wait_for(py_side_q.get(), timeout=0.05)
                        self._push_from_worker({
                            "type": "side",
                            "name": evt.get("name",""),
                            "badge": evt.get("badge",""),
                            "preview": evt.get("preview","")
                        })
                    except asyncio.TimeoutError:
                        pass

                    # 新增：處理 JS 去重事件
                    try:
                        d = await asyncio.wait_for(py_dedup_q.get(), timeout=0.01)
                        self._push_from_worker({
                            "type": "dedup",
                            "kind": d.get("kind","JS"),
                            "group": d.get("group",""),
                            "sender": d.get("sender",""),
                            "text": d.get("text",""),
                            "time": d.get("time",""),
                            "note": d.get("note","")
                        })
                    except asyncio.TimeoutError:
                        pass

                    # 再處理訊息
                    try:
                        msg = await asyncio.wait_for(py_msg_q.get(), timeout=0.05)
                    except asyncio.TimeoutError:
                        # 未收到完整網路資料時，不自動點入群組；改以明確標示的側欄預覽備援。
                        for pending in pending_groups.pop_expired(max_age=8):
                            fallback = _preview_fallback_message(pending)
                            if fallback["text"]:
                                await py_msg_q.put(fallback)
                                self._push_from_worker({"type": "log", "text":
                                    f"[NET] full message unavailable for {pending['group']}; "
                                    "forwarding sidebar preview\n"})
                            else:
                                self._push_from_worker({"type": "log", "text":
                                    f"[NET] full message unavailable for {pending['group']}; no preview to forward\n"})
                        continue

                    sys_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    if msg.get("type") == "side_preview":
                        raw_group = one_line(msg.get("group", ""))
                        key = self._normalize_group(raw_group)[0]
                        if raw_group and key:
                            events = _expand_side_preview_event(msg, key)
                            for event in events:
                                pending = pending_groups.add(event)
                                asyncio.create_task(backfill_last_message(pending))
                            self._push_from_worker({"type": "log", "text":
                                f"[NET] waiting full payload: {raw_group} "
                                f"(slots={len(events)})\n"})
                        continue
                    group = one_line(msg.get("group", ""))
                    sender= one_line(msg.get("sender", ""))
                    text  = one_line(msg.get("text", ""))
                    sent  = one_line(msg.get("time", "")) or sys_ts
                    attachment_refs = _attachment_urls(msg.get("attachments", []))
                    if not text and not sender and not attachment_refs:
                        continue
                    delivery_key = _message_delivery_key(msg)

                    # 更新 UI（右側）
                    self._push_from_worker({
                        "type": "msg", "group": group, "sender": sender, "text": text, "time": sent
                    })

                    # 2 秒緩衝內：只更新 UI，不記錄/不轉發
                    if time.time() < forward_ready_at:
                        self._push_from_worker({"type":"log","text":"[SKIP] 啟動緩衝中(2s)，略過 CSV/轉發。\n"})
                        continue

                    # CSV 去重（(時間, 群組, 內容)）
                    csv_path = today_csv()
                    has_unique_identity = bool(msg.get("message_id") or msg.get("event_token") or attachment_refs)
                    is_dup = (self._csv_has_rec(csv_path, sent, group, text)
                              if self._dedup_on("CSV") and not has_unique_identity else False)

                    if not is_dup:
                        # 寫 CSV
                        badge = self.group_badge.get(self._normalize_group(group)[0], "")
                        self._push_from_worker({"type":"log","text":f"[CSV] append → {csv_path} | {sent} | {group} | {text[:28]}...\n"})
                        self._csv_append_row(csv_path, [sent, group, text, sender, badge])

                        saved_attachments: List[Path] = []
                        for raw_url, original_name in attachment_refs:
                            try:
                                file_url = urljoin(base_url, raw_url)
                                resp = await ctx.request.get(file_url)
                                if not resp.ok:
                                    raise RuntimeError(f"HTTP {resp.status}")
                                body = await resp.body()
                                attachment_root = (out_dir / "attachments").resolve()
                                safe_group = _safe_attachment_component(group, "unknown")[:80]
                                folder = attachment_root / safe_group / datetime.now().strftime("%Y%m%d")
                                folder.resolve().relative_to(attachment_root)
                                folder.mkdir(parents=True, exist_ok=True)
                                parsed_name = Path(urlparse(file_url).path).name
                                base_name = _safe_attachment_component(
                                    original_name or parsed_name or "attachment", "attachment"
                                )
                                dest = folder / f"{hashlib.sha256(body).hexdigest()[:12]}_{base_name[:140]}"
                                if not dest.exists():
                                    dest.write_bytes(body)
                                saved_attachments.append(dest)
                                self._push_from_worker({"type": "log", "text": f"[FILE] saved {dest}\n"})
                            except Exception as e:
                                self._push_from_worker({"type": "log", "text": f"[FILE][WARN] download failed: {e}\n"})

                        # 浮動通知
                        title = f"{group}" + (f" [{badge}]" if badge else "")
                        self._push_from_worker({"type":"log","text":f"[INFO] 顯示通知 → {title} | {text[:24]}...\n"})
                        try:
                            self._push_from_worker({"type":"log","text":f"[INFO] 顯示通知 → {title} | {text[:28]}...\n"})
                            notifier.notify(title, f"{text}\n{sent}") if not self._sent_done('notify', group, delivery_key) else None
                            self._sent_mark('notify', group, delivery_key)
                            self._sent_log_row(sent, group, text, 'notify')
                        except Exception as e:
                            self._push_from_worker({"type":"log","text":f"[WARN] 通知顯示失敗: {e}\n"})
                        # Telegram 轉發
                        if tg.enabled:
                            styled = format_for_tg(group, badge, text, sent, tg.parse_mode, sender)
                            plain  = format_for_tg(group, badge, text, sent, None, sender)
                            delivery_status, failed_attachments = _send_telegram_bundle_once(
                                tg=tg,
                                group=group,
                                delivery_key=delivery_key,
                                styled=styled,
                                plain=plain,
                                should_send_text=bool(text or sender),
                                saved_attachments=saved_attachments,
                                already_sent=self._sent_done,
                                mark_sent=self._sent_mark,
                            )
                            if delivery_status == "duplicate":
                                self._push_from_worker({"type": "log", "text":
                                    f"[TG][SKIP] already sent (delivery={delivery_key[:12]}, "
                                    f"source={msg.get('source', msg.get('type', 'unknown'))})\n"})
                            elif delivery_status == "sent":
                                self._push_from_worker({"type":"log","text":
                                    f"[INFO] Telegram 送出 → {group}: {text[:28]}... "
                                    f"(parse={tg.parse_mode or 'plain'}, "
                                    f"delivery={delivery_key[:12]}, "
                                    f"source={msg.get('source', msg.get('type', 'unknown'))})\n"})
                                self._sent_log_row(sent, group, text, 'tg')
                            else:
                                ingress_reservations.release(msg)
                                for attachment in failed_attachments:
                                    self._push_from_worker({"type": "log", "text":
                                        f"[FILE][WARN] Telegram upload failed; kept local: {attachment}\n"})
                                self._push_from_worker({"type": "log", "text":
                                    "[WARN] Telegram 轉發失敗，未標記為已送出，後續事件會重試\n"})
                    else:
                        # CSV 去重命中
                        self._push_from_worker({
                            "type": "dedup",
                            "kind": "CSV",
                            "group": group,
                            "sender": sender,
                            "text": text,
                            "time": sent,
                            "note": "同日 CSV 已存在 (時間, 群組, 內容)"
                        })
            finally:
                await br.close()
                self._push_from_worker({"type": "log", "text": "[INFO] 監控已結束。\n"})

def main():
    instance_lock = SingleInstanceLock(_runtime_lock_path())
    try:
        acquired = instance_lock.acquire()
    except OSError as exc:
        print(
            f"ERROR: 無法建立單一執行鎖 {_runtime_lock_path()}: {exc}",
            file=sys.stderr,
        )
        return 2
    if not acquired:
        print(
            "ERROR: 已有另一個 FPC chat monitor 程式正在執行；"
            "請先關閉舊程序再啟動新版。",
            file=sys.stderr,
        )
        return 2
    try:
        app = App()
        # 系統訊息區（晚建立也 OK，供 log_sys 使用）
        frm = ttk.Frame(app.monitor_body)
        frm.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 6))
        ttk.Label(frm, text="系統訊息").pack(anchor="w")
        app.sys_scroll = ttk.Scrollbar(frm, orient="vertical")
        app.syslog = tk.Text(frm, height=int(app.ui_metrics["log_height"]), yscrollcommand=app.sys_scroll.set)
        app.sys_scroll.config(command=app.syslog.yview)
        app.syslog.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        app.sys_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        if "--ui-smoke-test" in sys.argv:
            app.update_idletasks()
            app.update()
            smoke_layout = {
                "window": [app.winfo_width(), app.winfo_height()],
                "canvas": [app.monitor_canvas.winfo_width(), app.monitor_canvas.winfo_height()],
                "body": [app.monitor_body.winfo_width(), app.monitor_body.winfo_height()],
                "body_requested": [app.monitor_body.winfo_reqwidth(), app.monitor_body.winfo_reqheight()],
            }
            app.withdraw()
            print(json.dumps({
                "app_version": APP_VERSION,
                "metrics": app.ui_metrics,
                "layout": smoke_layout,
                "monitor_scrollregion": app.monitor_canvas.cget("scrollregion"),
            }, ensure_ascii=False))
            app.destroy()
            return 0
        app.mainloop()
        return 0
    finally:
        instance_lock.release()

if __name__ == "__main__":
    raise SystemExit(main())
