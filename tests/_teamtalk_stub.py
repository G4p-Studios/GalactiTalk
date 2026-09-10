"""Minimal TeamTalk5 replacement used only by the public CI test suite."""
from __future__ import annotations

from types import ModuleType


def build_stub() -> ModuleType:
    module = ModuleType("TeamTalk5")

    class TeamTalk:
        pass

    class MediaFileStatus:
        MFS_FINISHED = 1
        MFS_ERROR = 2
        MFS_ABORTED = 3

    class TextMsgType:
        MSGTYPE_CHANNEL = 1
        MSGTYPE_USER = 2

    class UserRight:
        USERRIGHT_UPLOAD_FILES = 1

    class UserType:
        USERTYPE_ADMIN = 1

    module.TeamTalk = TeamTalk
    module.MediaFileStatus = MediaFileStatus
    module.TextMsgType = TextMsgType
    module.UserRight = UserRight
    module.UserType = UserType
    module.ttstr = lambda value: value.decode() if isinstance(value, bytes) else value
    module.buildTextMessage = lambda message: message
    module.rebuildTextMessage = lambda message: message
    return module
