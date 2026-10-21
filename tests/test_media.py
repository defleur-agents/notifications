"""Media endpoint: only image files under the allowlisted roots, realpath-checked, size-capped, no listing."""
import os, subprocess, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def load(roots, max_bytes=None):
    os.environ["INBOX_MEDIA_ROOTS"] = ":".join(roots)
    if max_bytes:
        os.environ["INBOX_MEDIA_MAX"] = str(max_bytes)
    else:
        os.environ.pop("INBOX_MEDIA_MAX", None)
    src = (ROOT / "mcp/front.py").read_text() + "\n" + (ROOT / "mcp/tools.py").read_text() + "\n" + (ROOT / "mcp/minimal_assets.py").read_text()
    ns = {"__name__": "notif_test"}
    exec(compile(src, "front+tools", "exec"), ns)
    return ns


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); t = Path(self.tmp.name)
        self.allowed = t / "allowed"; self.outside = t / "outside"
        (self.allowed / "d").mkdir(parents=True); self.outside.mkdir()
        (self.allowed / "d/a.jpg").write_bytes(JPEG)
        (self.allowed / "d/b.png").write_bytes(PNG)
        (self.allowed / "d/fake.jpg").write_bytes(b"<html>not an image</html>")
        (self.allowed / "d/notes.txt").write_bytes(b"secret")
        (self.allowed / "d/big.jpg").write_bytes(JPEG + b"\x00" * 5000)
        (self.outside / "x.jpg").write_bytes(JPEG)
        os.symlink(self.outside / "x.jpg", self.allowed / "d/link.jpg")
        os.symlink(self.outside, self.allowed / "dirlink")
        self.ns = load([str(self.allowed)], max_bytes=4096)
        self.route = self.ns["inbox_route"]

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, path):
        return self.route("GET", "/notifications/media" + path, "", {}, b"")

    def test_serves_allowed_images_with_type(self):
        code, ctype, data = self.get(str(self.allowed / "d/a.jpg"))
        self.assertEqual((code, ctype, data), (200, "image/jpeg", JPEG))
        self.assertEqual(self.get(str(self.allowed / "d/b.png"))[:2], (200, "image/png"))
        # URL-encoded segments decode before the check
        self.assertEqual(self.get(str(self.allowed / "d/a.jpg").replace("/a.jpg", "/%61.jpg"))[0], 200)

    def test_rejects(self):
        a = str(self.allowed)
        for path, code in [
            (a + "/d/../../outside/x.jpg", 404),            # traversal
            (a + "/d/%2e%2e/%2e%2e/outside/x.jpg", 404),    # encoded traversal
            (a + "/d/link.jpg", 404),                       # symlink leaving the root
            (a + "/dirlink/x.jpg", 404),                    # symlinked dir leaving the root
            (a + "/d/notes.txt", 404),                      # non-image extension
            (a + "/d/fake.jpg", 415),                       # image extension, not image bytes
            (a + "/d/", 404), (a + "/d", 404), (a, 404),    # no directory listing
            (a + "/d/missing.jpg", 404),
            (a + "/d/big.jpg", 413),                        # size cap
            ("/etc/passwd", 404), (str(self.outside / "x.jpg"), 404),
            (a + "/d/a.jpg%00.png", 400),
        ]:
            self.assertEqual(self.get(path)[0], code, path)
        self.assertEqual(self.route("POST", "/notifications/media" + a + "/d/a.jpg", "", {}, b"")[0], 404)

    def test_no_roots_serves_nothing(self):
        ns = load([])
        self.assertEqual(ns["inbox_route"]("GET", "/notifications/media" + str(self.allowed / "d/a.jpg"), "", {}, b"")[0], 404)

    def test_root_prefix_is_not_a_sibling_match(self):
        sib = Path(str(self.allowed) + "-evil"); sib.mkdir(); (sib / "a.jpg").write_bytes(JPEG)
        self.assertEqual(self.get(str(sib / "a.jpg"))[0], 404)


class RenderTests(unittest.TestCase):
    def test_render_js(self):
        subprocess.run(["node", str(ROOT / "tests/render.test.js")], check=True)

    def test_no_inner_html(self):
        for name in ["inbox.js", "render.js"]:
            src = (ROOT / "web/minimal" / name).read_text()
            self.assertNotIn("innerHTML", src); self.assertNotIn("insertAdjacentHTML", src); self.assertNotIn("outerHTML", src)
            self.assertNotIn("document.write", src)


if __name__ == "__main__":
    unittest.main()
