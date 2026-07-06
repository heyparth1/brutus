"""Issue image extraction + download (no network — client is faked)."""

from brutus.fetch.images import download_images, extract_image_urls


def test_extract_image_urls_finds_github_and_extensions():
    text = (
        "![bug](https://user-images.githubusercontent.com/1/a.png)\n"
        '<img src="https://cdn.example.com/b.jpg">\n'
        "see https://github.com/user-attachments/assets/abc123\n"
        "not an image: https://example.com/page"
    )
    urls = extract_image_urls(text)
    assert any("a.png" in u for u in urls)
    assert any("b.jpg" in u for u in urls)
    assert any("user-attachments/assets/abc123" in u for u in urls)
    assert not any(u.endswith("/page") for u in urls)


def test_download_images_saves_bytes(tmp_path):
    class FakeResp:
        status_code = 200
        content = b"PNGDATA"
        headers = {"content-type": "image/png"}

    class FakeClient:
        def get(self, url):
            return FakeResp()

    saved = download_images(
        "![x](https://user-images.githubusercontent.com/1/a.png)",
        tmp_path / "images",
        client=FakeClient(),
    )
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"PNGDATA"
    assert saved[0].suffix == ".png"


def test_download_images_none_when_no_images(tmp_path):
    assert download_images("just text, no pictures", tmp_path) == []
