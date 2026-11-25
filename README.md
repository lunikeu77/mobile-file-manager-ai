# 통합 파일 관리 및 다중 AI 자동화 풀스택 웹 애플리케이션

첨부된 기획서(`모바일_파일관리앱_통합개발기획서_v2.docx`)의 요구사항·보안 정책·데이터 모델·수용 기준을 충족하며, **Render(render.com) Docker 단일 Web Service**에서 브라우저 탭을 닫아도 서버 백그라운드 워커가 실제 FFmpeg MP4 압축·PDF 압축·HTTP Range 다운로드·예약 작업을 안정적으로 실행하도록 설계된 풀스택 웹 애플리케이션입니다.

---

## 1. 주요 아키텍처 및 기능 (F-01 ~ F-07)

- **Backend**: Python 3.11+ / FastAPI / Uvicorn / SQLite (`aiosqlite`, WAL 모드) / asyncio 백그라운드 워커 & 스케줄러
- **Media & PDF Engine**:
  - **MP4 압축**: `ffmpeg` + `ffprobe` 기반 파일 스트리밍 처리, `MAX_CONCURRENT_JOBS=1` 제한으로 Render 무료 인스턴스(512MB RAM) OOM 방지, 완료 후 디코딩 무결성 검증, 원본 보존 및 실패 시 임시 파일 자동 정리.
  - **PDF 압축**: `pikepdf` (MPL-2.0) + `pypdf` (BSD-3-Clause) + `Ghostscript` (AGPL-3.0). 암호화 PDF 예외 처리, 전자서명·폼·링크 보존 우선 모드(`structure_only` / `preserve_interactive`) 제공.
- **Frontend**: React 18 + Vite + TypeScript + Tailwind CSS v4 (모바일/PC 반응형 10개 핵심 화면 SPA)
- **F-01 Google Drive API v3**: Web OAuth 2.0 (Authorization Code + PKCE), `drive.file` 최소 권한 기본 적용, 페이지네이션 & `file_cache`, Resumable Upload, 스트리밍 다운로드.
- **F-02 파일명·확장자 관리**: 단일/일괄 이름 변경 및 확장자 문자열 변경 (실제 포맷 재인코딩과 명확히 분리 + 경고 확인).
- **F-05 다운로드/전송 큐**: SSE 실시간 진행률, HTTP `Range` 이어받기(서버 지원 시), 일시정지/재개/취소/재시도.
- **F-06 다중 AI 라우팅 & 보안 검증**:
  - **L0 로컬 규칙 파서(0원, 우선 실행)**: 정형 검색/이름 변경/확장자 변경/압축/URL 다운로드.
  - **독립 어댑터**: `OpenRouterAdapter`, `GeminiAdapter`, `OpenAIAdapter` (API 키 Fernet 암호화 저장, `429`/`5xx` 백오프+jitter Failover, `401`/`403` 즉시 중단, `correlation_id` 추적, 로그 내 비밀값 자동 마스킹).
  - **엄격한 JSON 검증**: `schema_version="1.0"`, `additionalProperties=false`, 허용 `action` allowlist, Path Traversal 차단, 파일 ID 대조, `DELETE_FILE`/`OVERWRITE_FILE` 명시적 사용자 승인.
- **F-07 작업 큐 & 장애 복구**: `tasks`, `task_steps`, `file_cache`, `ai_provider_settings`, `ai_usage_events`, `download_jobs` 영속화, `idempotency_key` 중복 방지, 서버 재기동 시 중단된 `RUNNING` 작업 `NEEDS_USER_ACTION` 안전 복구.

---

## 2. 오픈소스 PDF/미디어 엔진 라이선스 고지

| 엔진 | 라이선스 | 비고 |
| --- | --- | --- |
| `ffmpeg` / `ffprobe` | LGPL v2.1+ / GPL | 시스템 패키지 설치, 상용 배포 시 코덱 빌드 옵션 확인 필요 |
| `pikepdf` | MPL-2.0 | 상용 배포 가능 (수정 시 파일 단위 공개 의무) |
| `pypdf` | BSD-3-Clause | 상용 배포 자유로움 |
| `Ghostscript` | AGPL-3.0 | 이미지 다운샘플링에 사용; 상용 폐쇄형 재배포 시 별도 라이선스 검토 필요 (`structure_only` 모드는 pikepdf만 사용) |

---

## 3. Render 배포 가이드 (GitHub → Render Web Service)

1. 이 저장소를 GitHub 리포지토리에 업로드합니다 (`.env`, `data/`, `.secret_key`는 `.gitignore`에 의해 제외됨).
2. [Render Dashboard](https://dashboard.render.com) → **New +** → **Web Service** 또는 **Blueprint**(`render.yaml`) 선택.
3. Runtime을 **Docker**로 선택하고 `Dockerfile`을 사용합니다 (`healthCheckPath: /health`).
4. 아래 환경 변수를 Render **Environment** 탭에 설정합니다:
   - `ENCRYPTION_SECRET`: 강력한 랜덤 32바이트 이상 문자열 (또는 `render.yaml`의 `generateValue: true` 사용)
   - `APP_BASE_URL`: 배포된 Render URL (예: `https://your-app.onrender.com`)
   - `MAX_CONCURRENT_JOBS`: `1` (512MB RAM OOM 방지 권장값)
   - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`: (`https://your-app.onrender.com/api/drive/oauth/callback`) — Google Drive 연동 시 설정 (또는 UI에서 직접 입력 가능)
   - `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`: 선택 사항 (UI 설정 화면에서 암호화 등록 권장)
5. 배포 완료 후 **Google Cloud Console** → OAuth 2.0 클라이언트 승인된 리디렉션 URI에 `https://<your-app>.onrender.com/api/drive/oauth/callback`을 등록합니다.

---

## 4. 로컬 개발 및 테스트 실행

```bash
# 1. Frontend 빌드
cd frontend
npm install
npm run build
cd ..

# 2. Backend 의존성 설치 및 서버 실행
pip install -r requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000

# 3. 통합 테스트 실행
pytest -v
```
