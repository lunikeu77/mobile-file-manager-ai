from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import pikepdf

from backend.config import Settings
from backend.models import PdfPreset, VideoPreset
from backend.services.local_files import LocalFileService


class CompressionError(RuntimeError):
    """Raised when media or PDF compression fails."""


class EncryptedPDFError(CompressionError):
    """Raised when an encrypted PDF cannot be processed without unlocking."""


class CompressionService:
    """
    Server-side MP4 (FFmpeg + ffprobe) and PDF (pikepdf + Ghostscript) compression engine.
    Enforces MAX_CONCURRENT_JOBS=1 (by default) and file-based streaming execution to stay well under 512MB RAM.
    Originals are ALWAYS preserved; failed temporary files are cleaned up automatically.
    """

    def __init__(self, settings: Settings, local_files: LocalFileService) -> None:
        self.settings = settings
        self.local_files = local_files
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)

    async def _run_subprocess(self, *args: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        return (
            proc.returncode or 0,
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
        )

    async def probe_video(self, path: Path) -> dict[str, Any]:
        bins = self.settings.detect_binaries()
        ffprobe = bins.get("ffprobe")
        if not ffprobe:
            raise CompressionError("ffprobe binary is not installed on the server.")
        code, stdout, stderr = await self._run_subprocess(
            ffprobe,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        )
        if code != 0 or not stdout.strip():
            raise CompressionError(f"ffprobe failed for {path.name}: {stderr.strip()}")
        data = json.loads(stdout)
        fmt = data.get("format", {})
        streams = data.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        duration = float(fmt.get("duration") or 0.0)
        return {
            "duration_sec": duration,
            "size_bytes": int(fmt.get("size") or path.stat().st_size),
            "bit_rate": int(fmt.get("bit_rate") or 0),
            "format_name": fmt.get("format_name"),
            "has_video": len(video_streams) > 0,
            "has_audio": len(audio_streams) > 0,
            "video_codec": video_streams[0].get("codec_name") if video_streams else None,
            "width": video_streams[0].get("width") if video_streams else None,
            "height": video_streams[0].get("height") if video_streams else None,
            "audio_codec": audio_streams[0].get("codec_name") if audio_streams else None,
        }

    def estimate_video_target(
        self,
        *,
        duration_sec: float,
        preset: VideoPreset,
        target_size_bytes: int | None = None,
        audio_kbps: int = 96,
    ) -> dict[str, Any]:
        safe_duration = max(duration_sec, 1.0)
        preset_defaults = {
            VideoPreset.FAST_COMPAT: {"crf": 24, "ffmpeg_preset": "veryfast", "audio_kbps": 128, "max_height": None},
            VideoPreset.BALANCED: {"crf": 26, "ffmpeg_preset": "medium", "audio_kbps": 112, "max_height": 1080},
            VideoPreset.HIGH_COMPRESSION: {"crf": 30, "ffmpeg_preset": "faster", "audio_kbps": 96, "max_height": 720},
        }
        cfg = preset_defaults[preset]
        eff_audio_kbps = audio_kbps or cfg["audio_kbps"]

        estimated_video_kbps: int | None = None
        estimated_output_bytes: int | None = None

        if target_size_bytes is not None:
            # Reserve ~4% for MP4 container muxing overhead and audio stream
            usable_bits = max(int(target_size_bytes * 8 * 0.96), 64_000)
            total_kbps = usable_bits / safe_duration / 1000.0
            estimated_video_kbps = max(80, int(total_kbps - eff_audio_kbps))
            estimated_output_bytes = int(((estimated_video_kbps + eff_audio_kbps) * 1000 / 8) * safe_duration * 1.04)

        return {
            "preset": preset.value,
            "duration_sec": round(duration_sec, 2),
            "audio_kbps": eff_audio_kbps,
            "estimated_video_kbps": estimated_video_kbps,
            "estimated_output_bytes": estimated_output_bytes,
            "crf": cfg["crf"],
            "ffmpeg_preset": cfg["ffmpeg_preset"],
            "max_height": cfg["max_height"],
            "disclaimer": (
                "목표 용량은 영상 길이와 오디오 비트레이트를 기준으로 추정한 값이며, "
                "MP4 컨테이너 오버헤드 및 코덱 특성(VBR/CRF)으로 인해 정확한 결과 용량·품질 일치는 보장되지 않습니다."
            ),
        }

    async def compress_mp4(
        self,
        file_id: str,
        *,
        preset: VideoPreset = VideoPreset.BALANCED,
        target_size_bytes: int | None = None,
        video_codec: str = "h264",
    ) -> dict[str, Any]:
        bins = self.settings.detect_binaries()
        ffmpeg = bins.get("ffmpeg")
        if not ffmpeg:
            raise CompressionError("ffmpeg binary is not installed on the server.")

        record = await self.local_files.get_file(file_id)
        if not record or record["source"] != "local":
            raise FileNotFoundError(f"Local video file '{file_id}' not found.")

        input_path = Path(record["local_path"])
        if not input_path.exists():
            raise FileNotFoundError(f"Input file missing: {input_path.name}")

        self.settings.ensure_directories()
        temp_output = self.settings.temp_dir / f"mp4_{uuid.uuid4().hex[:12]}.mp4"
        final_name = f"{input_path.stem}_compressed_{preset.value}.mp4"
        final_output = self.settings.outputs_dir / final_name
        if final_output.exists():
            final_output = self.settings.outputs_dir / f"{input_path.stem}_compressed_{preset.value}_{uuid.uuid4().hex[:6]}.mp4"

        async with self._semaphore:
            try:
                probe_in = await self.probe_video(input_path)
                if not probe_in.get("has_video"):
                    raise CompressionError("Input file does not contain a valid video stream.")

                est = self.estimate_video_target(
                    duration_sec=probe_in["duration_sec"],
                    preset=preset,
                    target_size_bytes=target_size_bytes,
                )

                codec_lib = "libx265" if video_codec == "hevc" else "libx264"
                warnings: list[str] = []
                cmd = [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(input_path),
                    "-threads",
                    "2",
                    "-c:v",
                    codec_lib,
                    "-preset",
                    est["ffmpeg_preset"],
                    "-pix_fmt",
                    "yuv420p",
                ]
                if video_codec == "hevc":
                    cmd.extend(["-x265-params", "pools=2:frame-threads=1"])
                    warnings.append(
                        "HEVC(libx265) 인코딩은 512MB RAM 무료 환경에서 메모리 사용량이 높을 수 있어 저메모리 스레드 제한(pools=2:frame-threads=1)으로 실행되었습니다."
                    )

                if est["max_height"] and probe_in.get("height") and int(probe_in["height"]) > est["max_height"]:
                    cmd.extend(["-vf", f"scale=-2:{est['max_height']}"])

                if est["estimated_video_kbps"]:
                    kbps = est["estimated_video_kbps"]
                    cmd.extend(["-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.25)}k", "-bufsize", f"{kbps * 2}k"])
                else:
                    cmd.extend(["-crf", str(est["crf"])])

                if probe_in.get("has_audio"):
                    cmd.extend(["-c:a", "aac", "-b:a", f"{est['audio_kbps']}k"])
                else:
                    cmd.append("-an")

                cmd.extend(["-movflags", "+faststart", str(temp_output)])

                code, _, stderr = await self._run_subprocess(*cmd)
                if code != 0 or not temp_output.exists() or temp_output.stat().st_size == 0:
                    raise CompressionError(f"FFmpeg encoding failed (exit {code}): {stderr[-600:]}")

                # Integrity verification via ffprobe + decode sanity check
                probe_out = await self.probe_video(temp_output)
                if not probe_out.get("has_video") or probe_out.get("size_bytes", 0) <= 0:
                    raise CompressionError("Compressed MP4 failed ffprobe stream/size verification.")

                decode_code, _, decode_err = await self._run_subprocess(
                    ffmpeg, "-v", "error", "-t", "5", "-i", str(temp_output), "-f", "null", "-"
                )
                if decode_code != 0:
                    raise CompressionError(f"Compressed MP4 failed decode integrity check: {decode_err}")

                shutil.move(str(temp_output), str(final_output))
                await self.local_files.sync_workspace_cache()
                output_record = next(
                    (
                        f
                        for f in await self.local_files.list_files(source="local")
                        if f.get("local_path") == str(final_output.resolve())
                    ),
                    None,
                )

                orig_size = input_path.stat().st_size
                new_size = final_output.stat().st_size
                ratio = round((1 - (new_size / orig_size)) * 100, 2) if orig_size > 0 else 0.0

                return {
                    "original_file_id": file_id,
                    "original_preserved": True,
                    "output_file": output_record,
                    "original_size_bytes": orig_size,
                    "compressed_size_bytes": new_size,
                    "reduction_percent": ratio,
                    "probe_in": probe_in,
                    "probe_out": probe_out,
                    "decode_verified": True,
                    "estimation": est,
                    "warnings": warnings,
                }
            except Exception:
                temp_output.unlink(missing_ok=True)
                raise

    def _inspect_pdf_sync(self, path: Path) -> dict[str, Any]:
        try:
            with pikepdf.open(path) as pdf:
                if pdf.is_encrypted:
                    raise EncryptedPDFError("암호화된 PDF 파일입니다. 비밀번호 해제 후 압축할 수 있습니다.")
                root = pdf.Root
                has_acroform = "/AcroForm" in root
                has_signatures = False
                has_links = False
                if has_acroform:
                    acro = root["/AcroForm"]
                    if isinstance(acro, pikepdf.Dictionary) and "/SigFlags" in acro:
                        has_signatures = True
                for page in pdf.pages:
                    annots = page.get("/Annots")
                    if not annots:
                        continue
                    try:
                        annot_iter = list(annots)
                    except Exception:
                        continue
                    for annot in annot_iter:
                        try:
                            if not isinstance(annot, pikepdf.Dictionary):
                                continue
                            subtype = str(annot.get("/Subtype", ""))
                            if subtype == "/Link":
                                has_links = True
                            if annot.get("/FT") == pikepdf.Name("/Sig"):
                                has_signatures = True
                        except Exception:
                            continue
                return {
                    "page_count": len(pdf.pages),
                    "encrypted": False,
                    "has_acroform": has_acroform,
                    "has_signatures": has_signatures,
                    "has_links": has_links,
                }
        except EncryptedPDFError:
            raise
        except pikepdf.PasswordError as exc:
            raise EncryptedPDFError("암호화된 PDF 파일입니다. 비밀번호 해제 후 압축할 수 있습니다.") from exc
        except Exception as exc:
            raise CompressionError(f"유효하지 않은 PDF 파일입니다: {exc}") from exc

    def _optimize_pdf_pikepdf_sync(self, input_path: Path, output_path: Path) -> None:
        with pikepdf.open(input_path) as pdf:
            pdf.remove_unreferenced_resources()
            pdf.save(
                output_path,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                recompress_flate=True,
            )

    async def compress_pdf(
        self,
        file_id: str,
        *,
        preset: PdfPreset = PdfPreset.EBOOK,
        preserve_interactive: bool = False,
    ) -> dict[str, Any]:
        record = await self.local_files.get_file(file_id)
        if not record or record["source"] != "local":
            raise FileNotFoundError(f"Local PDF file '{file_id}' not found.")

        input_path = Path(record["local_path"])
        if not input_path.exists():
            raise FileNotFoundError(f"Input file missing: {input_path.name}")

        self.settings.ensure_directories()
        temp_gs = self.settings.temp_dir / f"pdf_gs_{uuid.uuid4().hex[:12]}.pdf"
        temp_pike = self.settings.temp_dir / f"pdf_pk_{uuid.uuid4().hex[:12]}.pdf"
        final_output = self.settings.outputs_dir / f"{input_path.stem}_compressed_{preset.value}.pdf"
        if final_output.exists():
            final_output = self.settings.outputs_dir / f"{input_path.stem}_compressed_{preset.value}_{uuid.uuid4().hex[:6]}.pdf"

        async with self._semaphore:
            try:
                info_in = await asyncio.to_thread(self._inspect_pdf_sync, input_path)
                warnings: list[str] = []

                if info_in["has_signatures"]:
                    warnings.append(
                        "원본 PDF에 전자서명이 포함되어 있습니다. 재압축 시 전자서명이 무효화될 수 있으므로 원본을 반드시 보관하세요."
                    )
                if info_in["has_acroform"] or info_in["has_links"]:
                    warnings.append(
                        "원본 PDF에 폼(AcroForm) 또는 하이퍼링크가 포함되어 있습니다. Ghostscript 재인코딩 시 일부 인터랙티브 요소나 투명도가 평탄화될 수 있습니다."
                    )

                bins = self.settings.detect_binaries()
                gs_bin = bins.get("ghostscript")
                used_engine = "pikepdf"

                if preset != PdfPreset.STRUCTURE_ONLY and not preserve_interactive and gs_bin:
                    gs_setting = f"/{preset.value}"
                    code, _, stderr = await self._run_subprocess(
                        gs_bin,
                        "-sDEVICE=pdfwrite",
                        "-dCompatibilityLevel=1.4",
                        f"-dPDFSETTINGS={gs_setting}",
                        "-dNOPAUSE",
                        "-dQUIET",
                        "-dBATCH",
                        "-dDetectDuplicateImages=true",
                        "-dCompressFonts=true",
                        "-dSubsetFonts=true",
                        f"-sOutputFile={temp_gs}",
                        str(input_path),
                    )
                    if code == 0 and temp_gs.exists() and temp_gs.stat().st_size > 0:
                        await asyncio.to_thread(self._optimize_pdf_pikepdf_sync, temp_gs, temp_pike)
                        used_engine = "ghostscript+pikepdf"
                    else:
                        warnings.append(f"Ghostscript 실행 fallback으로 pikepdf 구조 최적화를 사용했습니다: {stderr[-200:]}")
                        await asyncio.to_thread(self._optimize_pdf_pikepdf_sync, input_path, temp_pike)
                else:
                    if preset != PdfPreset.STRUCTURE_ONLY and not gs_bin and not preserve_interactive:
                        warnings.append("서버에 Ghostscript 바이너리가 없어 pikepdf 구조 최적화(무손실 스트림/중복 리소스 제거)만 수행했습니다.")
                    await asyncio.to_thread(self._optimize_pdf_pikepdf_sync, input_path, temp_pike)

                if not temp_pike.exists() or temp_pike.stat().st_size == 0:
                    raise CompressionError("PDF optimization produced an empty output file.")

                # Integrity check on output PDF
                info_out = await asyncio.to_thread(self._inspect_pdf_sync, temp_pike)
                if info_out["page_count"] != info_in["page_count"]:
                    raise CompressionError(
                        f"Compressed PDF page count mismatch ({info_out['page_count']} != {info_in['page_count']})."
                    )

                orig_size = input_path.stat().st_size
                new_size = temp_pike.stat().st_size
                if new_size >= orig_size:
                    warnings.append(
                        "텍스트 위주이거나 이미 최적화된 PDF이므로 압축 효과가 크지 않습니다."
                    )

                shutil.move(str(temp_pike), str(final_output))
                await self.local_files.sync_workspace_cache()
                output_record = next(
                    (
                        f
                        for f in await self.local_files.list_files(source="local")
                        if f.get("local_path") == str(final_output.resolve())
                    ),
                    None,
                )

                ratio = round((1 - (new_size / orig_size)) * 100, 2) if orig_size > 0 else 0.0
                return {
                    "original_file_id": file_id,
                    "original_preserved": True,
                    "engine_used": used_engine,
                    "output_file": output_record,
                    "original_size_bytes": orig_size,
                    "compressed_size_bytes": new_size,
                    "reduction_percent": ratio,
                    "pdf_info_in": info_in,
                    "pdf_info_out": info_out,
                    "warnings": warnings,
                }
            except Exception:
                raise
            finally:
                temp_gs.unlink(missing_ok=True)
                temp_pike.unlink(missing_ok=True)
