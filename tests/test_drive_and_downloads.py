from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


class RangeRequestHandler(SimpleHTTPRequestHandler):
    """Minimal HTTP handler supporting Accept-Ranges / Range: bytes=X- for real resume tests."""

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        path = Path(self.translate_path(self.path))
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        range_hdr = self.headers.get("Range")
        if range_hdr and range_hdr.startswith("bytes="):
            start_str = range_hdr.replace("bytes=", "").split("-")[0]
            start = int(start_str) if start_str else 0
            sliced = data[start:]
            self.send_response(206)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{len(data)-1}/{len(data)}")
            self.send_header("Content-Length", str(len(sliced)))
            self.end_headers()
            self.wfile.write(sliced)
            return
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.mark.asyncio
async def test_http_range_resume_download(container, tmp_path: Path):
    """Verifies real HTTP Range-based resume downloading into local workspace."""
    serve_dir = tmp_path / "http_root"
    serve_dir.mkdir()
    payload = b"ABCD1234" * 16384  # 128 KB
    (serve_dir / "payload.bin").write_bytes(payload)

    handler = partial(RangeRequestHandler, directory=str(serve_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        job = await container.downloads.create_http_download(
            f"http://127.0.0.1:{port}/payload.bin",
            filename="resumed_payload.bin",
            allow_localhost=True,
            auto_start=False,
        )
        # Pre-populate first 4096 bytes in .part temp file to simulate interrupted download
        Path(job["local_temp_path"]).write_bytes(payload[:4096])

        done = await container.downloads.run_http_download(job["download_id"])
        assert done["status"] == "COMPLETED"
        assert done["supports_range"] == 1
        assert done["bytes_received"] == len(payload)
        assert Path(done["output_path"]).read_bytes() == payload
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_drive_oauth_pkce_and_resumable_upload(container, monkeypatch):
    """Verifies Google Drive OAuth PKCE URL generation, encrypted token storage, and Resumable Upload chunking."""
    import io
    import httpx

    cfg = await container.drive.save_oauth_config("test-client-id-12345", "test-client-secret-67890", "drive.file")
    assert cfg["configured"] is True
    assert cfg["scope_mode"] == "drive.file"

    auth = await container.drive.create_authorization_url()
    assert "code_challenge_method=S256" in auth["auth_url"]
    assert "drive.file" in auth["auth_url"]

    # Store encrypted mock token directly (as if OAuth callback completed)
    await container.secrets.set_json_secret(
        container.drive.SECRET_TOKENS,
        {"access_token": "ya29.mock-access-token", "refresh_token": "1//mock-refresh", "expires_at": 9999999999},
    )

    # Ensure token is NOT stored plaintext in SQLite
    raw_row = await container.db.fetch_one(
        "SELECT ciphertext FROM encrypted_secrets WHERE key_reference = ?",
        (container.drive.SECRET_TOKENS,),
    )
    assert "ya29.mock-access-token" not in raw_row["ciphertext"]

    saved = await container.local_files.save_bytes_stream("upload_me.mp4", io.BytesIO(b"video-bytes-123456"))
    seen_requests: list[tuple[str, str]] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append((request.method, str(request.url)))
        if request.method == "POST" and "uploadType=resumable" in str(request.url):
            return httpx.Response(
                200,
                headers={"Location": "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=abc"},
            )
        if request.method == "PUT":
            return httpx.Response(
                200,
                json={"id": "drv_file_001", "name": "upload_me.mp4", "mimeType": "video/mp4", "size": "18"},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    orig_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return orig_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    res = await container.drive.resumable_upload_local_file(saved["file_id"])
    assert res["drive_file"]["id"] == "drv_file_001"
    assert len(seen_requests) == 2
    assert seen_requests[0][0] == "POST"
    assert seen_requests[1][0] == "PUT"

