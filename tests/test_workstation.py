"""Quality and shell checks for the CopyPro workstation."""

import os
import tempfile
import unittest

from PIL import Image, ImageDraw, ImageOps

from copypro_qt import imaging
from copypro_qt.engines import documents as docs
from copypro_qt.engines import layout as layout_engine
from copypro_qt.engines import pricing
from copypro_qt.legacy import ct


class ResizeQualityTest(unittest.TestCase):
    def test_default_pillow_path_matches_imageops_fit(self):
        image = Image.new("RGB", (640, 280), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 40, 200, 180), fill=(20, 90, 200))
        draw.ellipse((300, 60, 520, 240), fill=(200, 40, 40))
        expected = ImageOps.fit(image, (300, 200), Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        actual = imaging.resize_pillow(image, (300, 200), None, False)
        self.assertEqual(actual.size, (300, 200))
        self.assertEqual(actual.tobytes(), expected.tobytes())

    def test_fit_box_keeps_target_aspect(self):
        left, top, right, bottom = imaging.fit_box(800, 400, 100, 150)
        crop_w = (right - left) * 800
        crop_h = (bottom - top) * 400
        self.assertAlmostEqual(crop_w / crop_h, 100 / 150, places=4)

    def test_vips_resize_matches_dimensions(self):
        if not imaging.HAVE_VIPS:
            self.skipTest(imaging.VIPS_ERROR or "pyvips is not installed")
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "photo.jpg")
        image = Image.new("RGB", (1800, 1200), (30, 30, 30))
        ImageDraw.Draw(image).rectangle((100, 100, 900, 700), fill=(240, 180, 40))
        image.save(path, "JPEG", quality=95)
        resized = imaging.resize_image(path, (300, 200))
        reference = imaging.resize_pillow(ImageOps.exif_transpose(Image.open(path)), (300, 200))
        self.assertEqual(resized.size, reference.size)
        difference = sum(abs(a - b) for a, b in zip(resized.tobytes(), reference.tobytes()))
        mean = difference / len(resized.tobytes())
        self.assertLess(mean, 15)


class LayoutAndPricingTest(unittest.TestCase):
    def test_repeat_duplex_makes_front_and_back(self):
        specs = layout_engine.page_specs(["a.jpg", "b.jpg"], 2, 2, "Repeat", True)
        self.assertEqual(len(specs), 2)
        self.assertEqual(specs[0][0], ["a.jpg", "a.jpg", "a.jpg", "a.jpg"])
        self.assertEqual(specs[1][0], ["b.jpg", "b.jpg", "b.jpg", "b.jpg"])
        self.assertTrue(specs[1][1])

    def test_counter_parse_and_percent_discount(self):
        self.assertEqual(pricing.parse_entry("8*710"), (8.0, "710"))
        self.assertEqual(pricing.parse_entry("710"), (1.0, "710"))
        self.assertEqual(pricing.apply_discount(10, ("percent", 10)), 9)

    def test_split_keeps_a_vector_page(self):
        folder = tempfile.mkdtemp()
        src = os.path.join(folder, "spread.pdf")
        dest = os.path.join(folder, "split.pdf")
        doc = ct.fitz.open()
        page = doc.new_page(width=400, height=200)
        page.draw_line(ct.fitz.Point(20, 20), ct.fitz.Point(180, 80), color=(0, 0, 1), width=2)
        page.draw_line(ct.fitz.Point(220, 20), ct.fitz.Point(380, 80), color=(1, 0, 0), width=2)
        doc.save(src)
        doc.close()
        docs.split_pdf(src, dest, None, None)
        out = ct.fitz.open(dest)
        try:
            self.assertEqual(out.page_count, 2)
            self.assertLess(out[0].rect.width, 250)
            self.assertTrue(out[0].get_drawings() or out[1].get_drawings())
        finally:
            out.close()


class ExportSmokeTest(unittest.TestCase):
    def test_resize_cutline_and_pdf_keep_current_behavior(self):
        from copypro_qt.engines import cutline
        from copypro_qt.engines import documents as docs
        from copypro_qt.workspaces.photos import _resize_batch

        folder = tempfile.mkdtemp()
        photo = os.path.join(folder, "photo.jpg")
        image = Image.new("RGB", (640, 480), "white")
        ImageDraw.Draw(image).rectangle((40, 40, 400, 300), fill=(20, 40, 180))
        image.save(photo, "JPEG", quality=90)
        size = (ct.mm_to_px(100, 72), ct.mm_to_px(150, 72))
        done, errors, _message = _resize_batch(
            [{"path": photo, "page": None, "rotation": 0, "fractions": None, "label": "photo.jpg"}],
            {"mode": "choose", "folder": folder, "subfolder": "out", "overwrite": False},
            {
                "fmt": "JPEG", "size": size, "stretch": False, "grayscale": False, "dpi": 72,
                "bleed": False, "bleed_mm": 0, "marks": False, "mark_style": "outside",
                "bleed_mode": "reflected", "bleed_color": (255, 255, 255), "quality": 90,
            },
        )
        self.assertEqual(done, 1)
        self.assertEqual(errors, [])

        sticker = os.path.join(folder, "sticker.png")
        art = Image.new("RGB", (300, 200), "white")
        ImageDraw.Draw(art).ellipse((30, 30, 270, 170), fill=(0, 0, 0))
        art.save(sticker)
        analysis, dpi, points, _info = cutline.load_analysis(sticker)
        contours = cutline.analyse(
            analysis, 12, 0.2, 0, False, dpi,
            cutline.source_pixel_width(sticker, dpi, points, analysis.width), 20,
        )
        self.assertTrue(contours)
        width_mm, height_mm = cutline.source_size_mm(sticker, dpi, points, analysis.size)
        dest = os.path.join(folder, "cut.pdf")
        cutline.export_pdf(sticker, dest, contours, analysis.size, width_mm, height_mm, 0.2, True)
        doc = ct.fitz.open(dest)
        try:
            colors = [item.get("color") for item in doc[0].get_drawings()]
            self.assertTrue(any(color == (1, 0, 0) for color in colors))
            self.assertTrue(doc[0].get_images())
        finally:
            doc.close()

        src = os.path.join(folder, "doc.pdf")
        pdf = ct.fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "CopyPro")
        pdf.save(src)
        pdf.close()
        out = os.path.join(folder, "doc-out.pdf")
        docs.process_pdf(src, out, 72, 80, False, True, False, False)
        opened = ct.fitz.open(out)
        try:
            self.assertIn("CopyPro", opened[0].get_text())
        finally:
            opened.close()


class ShellTest(unittest.TestCase):
    def test_workspaces_are_created_when_opened(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from copypro_qt.shell import Workstation

        app = QApplication.instance() or QApplication([])
        window = Workstation()
        window.show()
        app.processEvents()
        self.assertIn("photos", window._pages)
        self.assertNotIn("counter", window._pages)
        self.assertEqual(window._current, "photos")
        for key in ("impose", "scans", "documents", "counter"):
            window.show_workspace(key)
            self.assertIn(key, window._pages)
            self.assertEqual(window._current, key)
            self.assertEqual(window._rail_buttons[key].property("active"), "true")
        self.assertEqual(window._rail_buttons["photos"].property("active"), "false")
        window.show_workspace("photos")
        photos = window._pages["photos"]
        photos.add_paths([os.path.abspath(__file__)])
        self.assertIn(os.path.abspath(__file__), photos.files)
        self.assertFalse(hasattr(window, "queue"))
        self.assertTrue(window.inspector_host.isVisible())
        window.show_workspace("counter")
        self.assertFalse(window.inspector_host.isVisible())
        self.assertEqual(len(window._pages), 5)
        window.close()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
