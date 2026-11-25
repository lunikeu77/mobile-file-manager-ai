from __future__ import annotations

import asyncio
import io
import shutil
import subprocess
from pathlib import Path

import pikepdf
import pytest
from PIL import Image

from backend.models import PdfPreset, VideoPreset
from backend.security.validator import SpecValidationError
from backend.services.compression import EncryptedPDFError
from tests.conftest import has_ffmpeg


def _create_sample_mp4(dest: Path) -> None:
    """Generates a real valid 2-second 640x360 MP4 test video with audio using ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg, "ffmpeg must be installed to run real MP4 compression tests"
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=2:size=640x360:rate=24",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(dest),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"ffmpeg sample generation failed: {res.stderr}"


def _create_sample_pdf(dest: Path, *, encrypted: bool = False) -> None:
    """Creates a real PDF containing a raster image stream (and optionally encrypts it)."""
    img = Image.new("RGB", (400, 300), color=(40, 120, 210))
    img_buf = io.BytesIO()
    img.save(img_buf, format="PNG")
    img_buf.seek(0)

    # Convert Pillow image to PDF page then enrich with pikepdf
    raw_pdf = dest.with_suffix(".raw.pdf")
    img.save(raw_pdf, format="PDF")
    with pikepdf.open(raw_pdf) as pdf:
        if encrypted:
            pdf.save(
                dest,
                encryption=pikepdf.Encryption(owner="owner123", user="user123", R=4),
            )
        else:
            pdf.save(dest, compress_streams=False)
    raw_pdf.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_gate1_non_ai_file_ops_and_real_compression(container):
    """
    [검증 1] AI API 키가 전혀 없는 상태에서:
    1) 파일 저장/탐색
    2) 파일명 변경 & 확장자 문자열 변경(경고 포함, 내용 불변)
    3) 실제 MP4(FFmpeg) 압축 + ffprobe 무결성 검증 + 원본 보존
    4) 실제 PDF 압축 + 페이지 수 검증 + 암호화 PDF 예외 처리
    """
    # Confirm zero AI keys configured
    providers = await container.ai_router.list_provider_settings()
    assert all(not p["key_configured"] for p in providers)

    # 1. File save & explore
    txt_bytes = io.BytesIO(b"hello-world-content")
    saved_txt = await container.local_files.save_bytes_stream("notes.txt", txt_bytes)
    assert saved_txt["name"] == "notes.txt"

    listed = await container.local_files.list_files(source="local", extension="txt")
    assert any(f["file_id"] == saved_txt["file_id"] for f in listed)

    # 2. Rename & change extension (content preserved)
    renamed = await container.local_files.rename_local_file(saved_txt["file_id"], "renamed_notes.txt")
    assert renamed["name"] == "renamed_notes.txt"

    ext_changed = await container.local_files.change_extension_local(renamed["file_id"], "log")
    assert ext_changed["name"] == "renamed_notes.log"
    assert ext_changed["is_format_conversion"] is False
    assert "변환되지 않았" in ext_changed["warning"]
    assert Path(ext_changed["local_path"]).read_bytes() == b"hello-world-content"

    # Path traversal blocked in rename
    with pytest.raises(SpecValidationError):
        await container.local_files.rename_local_file(ext_changed["file_id"], "../escape.txt")

    # 3. Real FFmpeg MP4 compression + ffprobe verification
    assert has_ffmpeg(), "FFmpeg and ffprobe must be installed"
    sample_mp4 = container.settings.workspace_dir / "sample_video.mp4"
    _create_sample_mp4(sample_mp4)
    await container.local_files.sync_workspace_cache()
    mp4_rec = next(f for f in await container.local_files.list_files() if f["name"] == "sample_video.mp4")

    est = container.compression.estimate_video_target(
        duration_sec=2.0, preset=VideoPreset.BALANCED, target_size_bytes=200_000
    )
    assert est["estimated_video_kbps"] is not None
    assert "보장되지 않습니다" in est["disclaimer"]

    mp4_res = await container.compression.compress_mp4(
        mp4_rec["file_id"], preset=VideoPreset.BALANCED, target_size_bytes=200_000
    )
    assert mp4_res["original_preserved"] is True
    assert sample_mp4.exists()  # original preserved
    assert mp4_res["decode_verified"] is True
    assert mp4_res["probe_out"]["has_video"] is True
    assert mp4_res["compressed_size_bytes"] > 0
    assert Path(mp4_res["output_file"]["local_path"]).exists()

    # 4. Real PDF compression + encrypted PDF rejection
    sample_pdf = container.settings.workspace_dir / "sample_doc.pdf"
    _create_sample_pdf(sample_pdf, encrypted=False)
    enc_pdf = container.settings.workspace_dir / "locked_doc.pdf"
    _create_sample_pdf(enc_pdf, encrypted=True)
    await container.local_files.sync_workspace_cache()

    pdf_rec = next(f for f in await container.local_files.list_files() if f["name"] == "sample_doc.pdf")
    enc_rec = next(f for f in await container.local_files.list_files() if f["name"] == "locked_doc.pdf")

    pdf_res = await container.compression.compress_pdf(pdf_rec["file_id"], preset=PdfPreset.EBOOK)
    assert pdf_res["original_preserved"] is True
    assert pdf_res["pdf_info_out"]["page_count"] == pdf_res["pdf_info_in"]["page_count"] == 1
    assert Path(pdf_res["output_file"]["local_path"]).exists()

    with pytest.raises(EncryptedPDFError):
        await container.compression.compress_pdf(enc_rec["file_id"], preset=PdfPreset.EBOOK)
    assert enc_pdf.exists()  # original preserved even on failure

    # Ensure zero temp leftovers
    leftovers = list(container.settings.temp_dir.iterdir())
    assert leftovers == []


@pytest.mark.asyncio
async def test_l0_local_rule_parser_without_ai_keys(container):
    """L0 deterministic commands work completely offline with zero AI keys."""
    await container.local_files.save_bytes_stream("report.pdf", io.BytesIO(b"%PDF-1.4\n"))
    files = await container.local_files.list_files(source="local")
    fid = files[0]["file_id"]

    plan1 = await container.ai_router.plan_command("PDF만 찾아줘", source="local")
    assert plan1["provider_used"] == "local_rule_engine"
    assert plan1["spec"]["steps"][0]["action"] == "SEARCH_FILES"

    plan2 = await container.ai_router.plan_command("접미사 _v2 추가", selected_file_ids=[fid], source="local")
    assert plan2["provider_used"] == "local_rule_engine"
    assert plan2["spec"]["steps"][0]["action"] == "RENAME_FILE"
