"""Contact sheet layout and one-PDF export."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt5.QtGui import QColor, QImage
from PyQt5.QtWidgets import QApplication

import video_frame_capture as vfc


class ContactSheetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_grid_prefers_a_full_rectangle(self):
        cols, rows = vfc.contact_grid(6, 744, 540)
        self.assertEqual((cols, rows), (3, 2))
        self.assertEqual(vfc.contact_grid(1, 612, 792), (1, 1))

    def test_pages_follow_the_chosen_count(self):
        paths = [f"frame_{n}.png" for n in range(5)]
        pages = vfc.contact_page_slices(paths, 2)
        self.assertEqual(len(pages), 3)
        self.assertEqual(pages[-1], ["frame_4.png"])

    def test_listing_skips_other_files_and_sorts_by_name(self):
        with tempfile.TemporaryDirectory() as folder:
            open(os.path.join(folder, "notes.txt"), "w").close()
            for name in ("b.png", "a.JPG"):
                image = QImage(20, 12, QImage.Format_RGB32)
                image.fill(QColor("navy"))
                image.save(os.path.join(folder, name))
            found = vfc.list_contact_images(folder)
            self.assertEqual([os.path.basename(path) for path in found], ["a.JPG", "b.png"])

    def test_pdf_holds_every_page_and_the_file_name(self):
        with tempfile.TemporaryDirectory() as folder:
            for index, color in enumerate(("red", "green", "blue", "orange")):
                image = QImage(40, 24, QImage.Format_RGB32)
                image.fill(QColor(color))
                image.save(os.path.join(folder, f"clip_{index}.png"))
            images = vfc.list_contact_images(folder)
            pdf_path = os.path.join(folder, "Contact_Sheet.pdf")
            pages = vfc.write_contact_pdf(pdf_path, images, 2, "Letter", True)
            self.assertEqual(pages, 2)
            with open(pdf_path, "rb") as handle:
                data = handle.read()
            self.assertTrue(data.startswith(b"%PDF"))
            self.assertIn(b"/Count 2", data)
            self.assertIn(b"/Width 40", data)
            # Qt embeds the caption as a Unicode map, not plain ASCII.
            self.assertIn(b"<0063> <006C> <0069> <0070> <005F> <0030>", data)

    def test_video_frame_shrinks_and_keeps_color(self):
        import numpy as np
        frame = np.zeros((20, 40, 3), dtype=np.uint8)
        frame[:, :] = (0, 0, 255)
        image = vfc.numpy_frame_to_qimage(frame, max_edge=10)
        self.assertFalse(image.isNull())
        self.assertEqual(image.width(), 10)
        self.assertGreater(image.pixelColor(0, 0).red(), 200)

    def test_batch_prompt_shows_before_any_frames(self):
        dialog = vfc.ContactSheetDialog()
        dialog.prepare_batch("Batch time 15.00s. Click This Video or All Videos.")
        self.assertEqual(dialog.page_label.text(), "Page 0 of 0")
        self.assertIn("15.00s", dialog.summary.text())
        self.assertFalse(dialog.print_btn.isEnabled())

    def test_seek_skips_a_time_past_the_end(self):
        self.assertEqual(vfc.contact_seek_frame(15.0, 30.0, 300), -1)
        self.assertEqual(vfc.contact_seek_frame(15.0, 30.0, 900), 450)
        self.assertIsNone(vfc.contact_seek_frame(15.0, 0, 0))
        self.assertEqual(vfc.contact_seek_frame(0, 30.0, 10), 0)

    def test_in_memory_frame_prints_its_name(self):
        image = QImage(40, 24, QImage.Format_RGB32)
        image.fill(QColor("purple"))
        frame = vfc.ContactFrame("reel_0.png", "/clips", image=image)
        with tempfile.TemporaryDirectory() as folder:
            pdf_path = os.path.join(folder, "Contact_Sheet.pdf")
            pages = vfc.write_contact_pdf(pdf_path, [frame], 1, "Letter", True)
            self.assertEqual(pages, 1)
            with open(pdf_path, "rb") as handle:
                data = handle.read()
            self.assertIn(b"/Count 1", data)
            self.assertIn(b"<0072> <0065>", data)

    def test_preview_shows_the_first_page(self):
        with tempfile.TemporaryDirectory() as folder:
            image = QImage(30, 20, QImage.Format_RGB32)
            image.fill(QColor("teal"))
            image.save(os.path.join(folder, "take.png"))
            dialog = vfc.ContactSheetDialog()
            dialog.per_page.setValue(4)
            dialog.set_folder(folder)
            self.assertEqual(dialog.page_label.text(), "Page 1 of 1")
            self.assertTrue(dialog.print_btn.isEnabled())
            self.assertIsNotNone(dialog._page_image)
            # Letter landscape at 300 dpi is 3300 pixels wide.
            self.assertEqual(dialog._page_image.width(), 3300)
            still = vfc.load_contact_still(os.path.join(folder, "take.png"))
            self.assertEqual(still.picture().width(), 30)


if __name__ == "__main__":
    unittest.main()
