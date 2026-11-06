import io
import tempfile
import unittest
from pathlib import Path

from codesync.flyout.icons import load_tray_icon, render_icon, save_application_icon


class IconTests(unittest.TestCase):
    def test_tray_and_exe_share_the_cropped_mark(self) -> None:
        tray = load_tray_icon()
        self.assertEqual(tray.size, (256, 256))
        self.assertEqual(tray.mode, "RGBA")
        icon = render_icon(32)
        self.assertGreater(self._bright_fraction(icon), 0.05)
        red, green, blue, _alpha = icon.getpixel((0, 0))
        self.assertLess(red + green + blue, 40)

        buffer = io.BytesIO()
        tray.save(buffer, format="ICO")
        widths = _ico_widths(buffer.getvalue())
        self.assertEqual(widths, [16, 20, 24, 32, 40, 48, 64, 128, 256])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CodeSync.ico"
            save_application_icon(path)
            self.assertEqual(_ico_widths(path.read_bytes()), widths)

    def _bright_fraction(self, image) -> float:
        pixels = image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()
        bright = sum(1 for red, green, blue, _alpha in pixels if red + green + blue > 400)
        return bright / (image.size[0] * image.size[1])


def _ico_widths(data: bytes) -> list[int]:
    if data[:4] != b"\x00\x00\x01\x00":
        raise AssertionError("icon file is not an ICO")
    count = int.from_bytes(data[4:6], "little")
    return [256 if data[6 + index * 16] == 0 else data[6 + index * 16] for index in range(count)]
