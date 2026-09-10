import sys
import unittest
from unittest.mock import patch

import teamtalk_sdk


class SDKTests(unittest.TestCase):
    def test_parser_ignores_pro_sdk(self):
        parser = teamtalk_sdk._SDKLinkParser()
        parser.feed('<a href="https://www.bearware.dk/x/tt5sdk_v5.22a_win64.7z">standard</a>'
                    '<a href="https://www.bearware.dk/x/tt5prosdk_v5.22a_win64.7z">pro</a>')
        self.assertEqual(len(parser.links), 1)

    @patch.object(sys, "platform", "linux")
    @patch("platform.machine", return_value="x86_64")
    def test_linux_selector(self, _machine):
        self.assertEqual(teamtalk_sdk._platform_selector(), ("Linux", "x86_64", "_ubuntu22_x86_64.7z"))

    def test_archive_traversal_is_rejected(self):
        with self.assertRaises(teamtalk_sdk.TeamTalkSDKError):
            teamtalk_sdk._safe_archive_names(["Library/TeamTalk5.py", "../escape"])


if __name__ == "__main__":
    unittest.main()
