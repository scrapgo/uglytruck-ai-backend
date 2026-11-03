"""Offline tests for the external photo fetcher (User-Agent rotation) and the
validate_images guard for the case where not a single photo could be downloaded.

Run from the backend folder:  venv/bin/python -m unittest tests.test_image_fetch -v
"""
import asyncio
import unittest
from io import BytesIO
from unittest.mock import patch

import httpx
from PIL import Image

from app.routers import models
from app.validators import image_validation

URL = "https://www.uglytruck.net/wp-content/uploads/gravity_forms/6-x/2026/11/Photo_Exterior_Front1.jpg"


def _png_bytes():
    buf = BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, format="PNG")
    return buf.getvalue()


def _fetch(handler, url=URL):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await models._fetch_image(client, url, 1, {"Authorization": "QB-USER-TOKEN x"})
    return asyncio.run(go())


class FetchImageUserAgentRotationTests(unittest.TestCase):
    def test_rotates_user_agent_when_server_drops_connection(self):
        seen = []

        def handler(request):
            ua = request.headers.get("User-Agent", "")
            seen.append(ua)
            if "Safari" not in ua or "Chrome" in ua:
                raise httpx.RemoteProtocolError("Server disconnected without sending a response.", request=request)
            return httpx.Response(200, content=_png_bytes(), headers={"content-type": "image/png"})

        result = _fetch(handler)
        self.assertIn("image", result)
        self.assertNotIn("error", result)
        self.assertGreaterEqual(len(seen), 2)

    def test_rotates_user_agent_on_403(self):
        seen = []

        def handler(request):
            ua = request.headers.get("User-Agent", "")
            seen.append(ua)
            if ua.startswith("python-httpx"):
                return httpx.Response(403, content=b"<html>403 - Forbidden</html>", headers={"content-type": "text/html"})
            return httpx.Response(200, content=_png_bytes(), headers={"content-type": "image/png"})

        result = _fetch(handler)
        self.assertIn("image", result)
        self.assertEqual(len(seen), 2)

    def test_gives_up_after_last_user_agent(self):
        seen = []

        def handler(request):
            seen.append(request.headers.get("User-Agent", ""))
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.", request=request)

        result = _fetch(handler)
        self.assertIn("error", result)
        self.assertIn("Request error", result["error"])
        self.assertEqual(len(seen), len(models.EXTERNAL_URL_USER_AGENTS))

    def test_quickbase_urls_keep_auth_headers_and_do_not_rotate(self):
        if "quickbase.com" not in str(models.settings.BASE_URL).lower():
            self.skipTest("BASE_URL is not Quickbase in this environment")
        seen = []

        def handler(request):
            seen.append(request.headers.get("Authorization"))
            raise httpx.ConnectError("boom", request=request)

        result = _fetch(handler, url="files/br5nbqyfg/5131/38/1")
        self.assertIn("error", result)
        self.assertEqual(seen, ["QB-USER-TOKEN x"])


def _fake_vision_client(payload):
    class FakeResp:
        status_code = 200

        def json(self):
            return payload

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return FakeResp()

    return FakeClient


def _seven_url_record():
    return {key: f"https://www.uglytruck.net/wp-content/uploads/{key}.jpg" for key in image_validation.PHOTO_FIELD_MAP}


class ValidateImagesNothingDownloadedTests(unittest.TestCase):
    def test_does_not_blame_seller_when_nothing_downloaded(self):
        rec = _seven_url_record()
        payload = {
            "views_found": [],
            "missing": list(image_validation.ALL_REQUIRED_VIEWS),
            "detailed_results": [
                {"image_index": i, "image_url": u, "error": "Request error: Server disconnected without sending a response."}
                for i, u in enumerate(rec.values(), 1)
            ],
        }
        with patch.object(image_validation.httpx, "AsyncClient", _fake_vision_client(payload)):
            image_urls, missing = asyncio.run(image_validation.validate_images(rec, "http://vision/ai/gpt-vision"))
        self.assertEqual(len(image_urls), 7)
        self.assertEqual(missing, [])

    def test_keeps_genuinely_missing_views(self):
        rec = _seven_url_record()
        labels = list(image_validation.ALL_REQUIRED_VIEWS)
        details = [{"image_index": i, "image_url": u, "predicted_label": labels[i - 1]} for i, u in enumerate(rec.values(), 1)]
        details[-1] = {"image_index": 7, "image_url": details[-1]["image_url"], "error": "Request error: timeout"}
        payload = {"views_found": labels[:-1], "missing": [labels[-1]], "detailed_results": details}
        with patch.object(image_validation.httpx, "AsyncClient", _fake_vision_client(payload)):
            _, missing = asyncio.run(image_validation.validate_images(rec, "http://vision/ai/gpt-vision"))
        self.assertEqual(missing, [labels[-1]])


if __name__ == "__main__":
    unittest.main()
