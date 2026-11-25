from __future__ import annotations

import re
from typing import Any

from backend.models import AllowedAction


def parse_local_deterministic_command(
    command: str,
    selected_file_ids: list[str] | None = None,
    source: str = "local",
) -> dict[str, Any] | None:
    """
    L0 Local Rule Engine (0 API cost, always prioritized before calling external AI).
    Handles deterministic file filtering, suffix/prefix renaming, extension changing,
    and single/selected file compression without external AI calls.
    Returns None when the command requires deeper NLU/multi-step planning by an AI provider.
    """
    text = command.strip()
    lower = text.lower()
    selected = selected_file_ids or []

    # 1) Size filtering: e.g. "200MB 초과 파일 보여줘", "100mb 이상 파일 검색"
    size_match = re.search(r"(\d+(?:\.\d+)?)\s*(kb|mb|gb)\s*(초과|이상|보다\s*큰|larger|over|above)", lower)
    if size_match:
        val = float(size_match.group(1))
        unit = size_match.group(2)
        mult = {"kb": 1024, "mb": 1024**2, "gb": 1024**3}[unit]
        min_bytes = int(val * mult)
        ext = None
        for candidate in ("pdf", "mp4", "mov", "png", "jpg", "zip"):
            if candidate in lower:
                ext = candidate
                break
        return {
            "complexity": "L0",
            "provider_used": "local_rule_engine",
            "spec": {
                "schema_version": "1.0",
                "intent": "search_by_size",
                "summary": f"{size_match.group(1)}{unit.upper()} 초과 파일 검색 (L0 로컬 규칙)",
                "steps": [
                    {
                        "action": AllowedAction.SEARCH_FILES.value,
                        "source": "google_drive" if source == "google_drive" else "local",
                        "filters": {
                            "min_size_bytes": min_bytes,
                            **({"extension": ext} if ext else {}),
                        },
                    }
                ],
                "requires_confirmation": False,
                "warnings": [],
            },
        }

    # 2) Extension search: e.g. "PDF만 찾아줘", "mp4 파일 보여줘", "find pdf files"
    ext_search = re.search(
        r"(?:^|[^A-Za-z0-9])(pdf|mp4|mov|mkv|png|jpg|jpeg|txt|csv|zip)(?:[^A-Za-z0-9]|$).*?(찾아|보여|검색|목록|find|list|search)",
        lower,
    )
    if ext_search:
        ext = ext_search.group(1)
        return {
            "complexity": "L0",
            "provider_used": "local_rule_engine",
            "spec": {
                "schema_version": "1.0",
                "intent": "search_by_extension",
                "summary": f".{ext} 파일 검색 (L0 로컬 규칙)",
                "steps": [
                    {
                        "action": AllowedAction.SEARCH_FILES.value,
                        "source": "google_drive" if source == "google_drive" else "local",
                        "filters": {"extension": ext},
                    }
                ],
                "requires_confirmation": False,
                "warnings": [],
            },
        }

    # 3) Selected-file deterministic actions (0 AI cost)
    if selected:
        # Suffix rename: e.g. "접미사 _backup 추가" or "파일명 뒤에 _v2 붙여"
        suffix_match = re.search(r"(?:접미사|뒤에|suffix)\s+['\"]?([A-Za-z0-9_\-\.]+)['\"]?", text, re.IGNORECASE)
        if suffix_match:
            suffix = suffix_match.group(1)
            steps = [
                {
                    "action": AllowedAction.RENAME_FILE.value,
                    "source": "google_drive" if source == "google_drive" else "local",
                    "file_id": fid,
                    "options": {"suffix": suffix},
                }
                for fid in selected
            ]
            return {
                "complexity": "L0",
                "provider_used": "local_rule_engine",
                "spec": {
                    "schema_version": "1.0",
                    "intent": "batch_rename_suffix",
                    "summary": f"선택한 {len(selected)}개 파일에 접미사 '{suffix}' 추가 (L0 로컬 규칙)",
                    "steps": steps,
                    "requires_confirmation": True,
                    "warnings": [],
                },
            }

        # Change extension string: e.g. "확장자를 mkv로 변경" / "change extension to txt"
        ext_change = re.search(r"(?:확장자(?:를)?|extension\s+to)\s+['\"]?\.?([A-Za-z0-9]{1,15})['\"]?", text, re.IGNORECASE)
        if ext_change:
            new_ext = ext_change.group(1).lower()
            steps = [
                {
                    "action": AllowedAction.CHANGE_EXTENSION.value,
                    "source": "google_drive" if source == "google_drive" else "local",
                    "file_id": fid,
                    "options": {"new_extension": new_ext},
                }
                for fid in selected
            ]
            return {
                "complexity": "L0",
                "provider_used": "local_rule_engine",
                "spec": {
                    "schema_version": "1.0",
                    "intent": "change_extension",
                    "summary": f"선택한 {len(selected)}개 파일의 확장자를 .{new_ext}로 변경 (L0 로컬 규칙)",
                    "steps": steps,
                    "requires_confirmation": True,
                    "warnings": [
                        "확장자 변경은 파일명 문자열만 변경하며 파일 내용(바이트)은 변환되지 않습니다."
                    ],
                },
            }

        # Compress MP4 / PDF on selected files
        if any(k in lower for k in ("mp4 압축", "영상 압축", "비디오 압축", "compress video", "compress mp4")):
            preset = "balanced"
            if "고압축" in lower or "작게" in lower or "high" in lower:
                preset = "high_compression"
            elif "빠르게" in lower or "fast" in lower:
                preset = "fast_compat"
            steps = [
                {
                    "action": AllowedAction.COMPRESS_VIDEO.value,
                    "source": "local",
                    "file_id": fid,
                    "options": {"preset": preset},
                }
                for fid in selected
            ]
            return {
                "complexity": "L0",
                "provider_used": "local_rule_engine",
                "spec": {
                    "schema_version": "1.0",
                    "intent": "compress_video",
                    "summary": f"선택한 {len(selected)}개 MP4 압축 ({preset}) (L0 로컬 규칙)",
                    "steps": steps,
                    "requires_confirmation": False,
                    "warnings": [],
                },
            }

        if any(k in lower for k in ("pdf 압축", "문서 압축", "compress pdf")):
            preset = "ebook"
            if "screen" in lower or "최소" in lower:
                preset = "screen"
            elif "printer" in lower or "고화질" in lower:
                preset = "printer"
            steps = [
                {
                    "action": AllowedAction.COMPRESS_PDF.value,
                    "source": "local",
                    "file_id": fid,
                    "options": {"preset": preset},
                }
                for fid in selected
            ]
            return {
                "complexity": "L0",
                "provider_used": "local_rule_engine",
                "spec": {
                    "schema_version": "1.0",
                    "intent": "compress_pdf",
                    "summary": f"선택한 {len(selected)}개 PDF 압축 ({preset}) (L0 로컬 규칙)",
                    "steps": steps,
                    "requires_confirmation": False,
                    "warnings": [],
                },
            }

    # 4) Direct URL download: e.g. "https://... 다운로드해줘"
    url_match = re.search(r"(https?://[^\s]+)", text)
    if url_match and any(k in lower for k in ("다운로드", "받아", "download")):
        return {
            "complexity": "L0",
            "provider_used": "local_rule_engine",
            "spec": {
                "schema_version": "1.0",
                "intent": "download_url",
                "summary": "외부 URL 다운로드 대기열 등록 (L0 로컬 규칙)",
                "steps": [
                    {
                        "action": AllowedAction.DOWNLOAD_FILE.value,
                        "source": "external_url",
                        "options": {"url": url_match.group(1)},
                    }
                ],
                "requires_confirmation": False,
                "warnings": [],
            },
        }

    return None
