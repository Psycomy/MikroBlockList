import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "routeros" / "install.rsc"


def _update_body(text):
    """Source block of the stored mbl-update script (from `source={` to the
    matching closing brace)."""
    start = text.index("/system script add name=mbl-update")
    open_at = text.index("source={", start) + len("source=")
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
    raise AssertionError("mbl-update source block is not closed")


class InstallerVersionTest(unittest.TestCase):
    def test_installer_version_matches_VERSION(self):
        version = (ROOT / "VERSION").read_text().strip()
        found = re.findall(r'^:local mblScriptVersion "([^"]*)"$',
                           INSTALLER.read_text(), re.MULTILINE)
        self.assertEqual(found, [version])

    def test_installer_has_version_placeholder(self):
        body = _update_body(INSTALLER.read_text())
        self.assertEqual(body.count("@MBL_VERSION@"), 1)

    def test_installer_injects_version_placeholder(self):
        self.assertIn(':set mblSrc [$mblInject $mblSrc "@MBL_VERSION@" $mblScriptVersion]', INSTALLER.read_text())

    def test_installer_has_variant_placeholder(self):
        body = _update_body(INSTALLER.read_text())
        self.assertEqual(body.count("@MBL_VARIANT@"), 1)
        self.assertIn(':local variant "@MBL_VARIANT@"', body)

    def test_installer_injects_variant_placeholder(self):
        self.assertIn(':set mblSrc [$mblInject $mblSrc "@MBL_VARIANT@" $mblVar]', INSTALLER.read_text())

    def test_installer_has_url_placeholders(self):
        body = _update_body(INSTALLER.read_text())
        self.assertEqual(body.count("@MBL_URL@"), 1)
        self.assertEqual(body.count("@MBL_URL6@"), 1)

    def test_installer_injects_url_placeholders(self):
        text = INSTALLER.read_text()
        self.assertIn(':set mblSrc [$mblInject $mblSrc "@MBL_URL@" $mblUrl]', text)
        self.assertIn(':set mblSrc [$mblInject $mblSrc "@MBL_URL6@" $mblUrl6]', text)

    def test_installer_has_ipv6_placeholder(self):
        body = _update_body(INSTALLER.read_text())
        self.assertEqual(body.count("@MBL_IPV6@"), 1)
        self.assertIn(':local ipv6 "@MBL_IPV6@"', body)

    def test_installer_injects_ipv6_placeholder(self):
        self.assertIn(':set mblSrc [$mblInject $mblSrc "@MBL_IPV6@" $mblV6]', INSTALLER.read_text())

    def test_installer_has_mode_placeholder(self):
        body = _update_body(INSTALLER.read_text())
        self.assertEqual(body.count("@MBL_MODE@"), 1)
        self.assertIn(':local mode "@MBL_MODE@"', body)
        # the installer reads the mode back from this line only
        self.assertEqual(body.count(':local mode "'), 1)

    def test_installer_injects_mode_placeholder(self):
        self.assertIn(':set mblSrc [$mblInject $mblSrc "@MBL_MODE@" $mblMd]', INSTALLER.read_text())

    def test_installer_writes_source_once(self):
        # RouterOS logs every change of a script source in full: the settings
        # are injected into a string and written back with a single set.
        text = INSTALLER.read_text()
        self.assertEqual(text.count('/system script set [find where name="mbl-update"] source='), 1)


if __name__ == "__main__":
    unittest.main()
