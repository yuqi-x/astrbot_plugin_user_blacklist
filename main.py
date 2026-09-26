"""用户黑名单插件 —— 在配置界面填入 QQ 号即可屏蔽某个人。

无论群聊还是私聊，命中名单的发送者的消息都会被直接截断事件传播链：
不回复、不进大模型、不消耗 token，对方甚至无法察觉自己被屏蔽。

名单来源（合并生效）：
1. 插件配置项 ``blacklist``（AstrBot 面板 → 插件管理 → 用户黑名单 → 配置）
2. 插件目录下的 ``blacklist.json``（按文件修改时间热更新，改完即生效，无需重载）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

PLUGIN_DIR = Path(__file__).resolve().parent
LIST_FILE = PLUGIN_DIR / "blacklist.json"

SCOPE_BOTH = "both"
SCOPE_GROUP = "group"
SCOPE_PRIVATE = "private"
VALID_SCOPES = (SCOPE_BOTH, SCOPE_GROUP, SCOPE_PRIVATE)


def _normalize(raw: Any) -> set[str]:
    """把各种形态的输入统一成 ID 字符串集合。"""
    if isinstance(raw, dict):
        raw = raw.get("users") or raw.get("blacklist") or []
    if isinstance(raw, str):
        raw = (
            raw.replace("\r", ",")
            .replace("\n", ",")
            .replace("，", ",")
            .replace("、", ",")
            .replace(" ", ",")
            .replace(";", ",")
            .split(",")
        )
    ids: set[str] = set()
    for item in raw or []:
        text = str(item).strip()
        if text and not text.startswith("#"):
            ids.add(text)
    return ids


@register(
    "astrbot_plugin_user_blacklist",
    "枝祈",
    "用户黑名单：在配置界面填入 QQ 号即可屏蔽该用户，群聊与私聊的消息都会被静默忽略。",
    "1.0.0",
)
class UserBlacklistPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        try:
            self.config: Any = config if config is not None else {}
        except Exception:  # noqa: BLE001
            self.config = {}
        self._file_mtime: float | None = None
        self._file_ids: set[str] = set()

    # ------------------------------------------------------------------ 配置
    def _get(self, key: str, default: Any = None) -> Any:
        try:
            value = self.config.get(key, default)
        except Exception:  # noqa: BLE001
            return default
        return default if value is None else value

    def _enabled(self) -> bool:
        return bool(self._get("enable", True))

    def _scope(self) -> str:
        scope = str(self._get("scope", SCOPE_BOTH) or SCOPE_BOTH).strip().lower()
        return scope if scope in VALID_SCOPES else SCOPE_BOTH

    def _ignore_admin(self) -> bool:
        return bool(self._get("ignore_admin", True))

    def _blacklist_ids(self) -> set[str]:
        """配置 + 外部文件合并后的屏蔽名单。"""
        ids = _normalize(self._get("blacklist"))

        try:
            if LIST_FILE.exists():
                mtime = LIST_FILE.stat().st_mtime
                if mtime != self._file_mtime:
                    with LIST_FILE.open("r", encoding="utf-8") as fp:
                        self._file_ids = _normalize(json.load(fp))
                    self._file_mtime = mtime
                    if self._file_ids:
                        logger.info(
                            f"[user_blacklist] blacklist.json 已加载 {len(self._file_ids)} 个用户"
                        )
                ids |= self._file_ids
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[user_blacklist] 读取 blacklist.json 失败: {exc}")
            ids |= self._file_ids

        return ids

    # -------------------------------------------------------------- 命中判定
    def _should_block(self, event: AstrMessageEvent, sender: str) -> bool:
        if not sender or sender not in self._blacklist_ids():
            return False

        # 管理员豁免：防止把自己锁在门外
        if self._ignore_admin():
            try:
                if event.is_admin():
                    return False
            except Exception:  # noqa: BLE001
                pass

        scope = self._scope()
        if scope != SCOPE_BOTH:
            is_group = bool(event.get_group_id())
            if scope == SCOPE_GROUP and not is_group:
                return False
            if scope == SCOPE_PRIVATE and is_group:
                return False

        return True

    # ---------------------------------------------------------------- 事件钩子
    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def block_blacklisted(self, event: AstrMessageEvent):
        """最高优先级守卫：黑名单用户的消息直接终止传播。"""
        if not self._enabled():
            return

        try:
            sender = str(event.get_sender_id() or "")
        except Exception:  # noqa: BLE001
            return

        if not self._should_block(event, sender):
            return

        # 可选：回一句拒绝提示（留空则完全静默）
        notice = str(self._get("notice", "") or "").strip()
        if notice:
            try:
                await event.send(event.plain_result(notice))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[user_blacklist] 发送提示语失败: {exc}")

        if bool(self._get("log_block", True)):
            try:
                chat = "群聊" if event.get_group_id() else "私聊"
                session = event.get_group_id() or "私聊"
            except Exception:  # noqa: BLE001
                chat, session = "未知", "?"
            logger.info(f"[user_blacklist] 已屏蔽黑名单用户 {sender}（{chat} {session}）的消息")

        event.stop_event()
