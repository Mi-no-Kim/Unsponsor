# Pipeline 로컬 실행

## W-025: HTTP 429 안전한 폴백 계약

yt-dlp 자막 요청은 수동 자막과 원본 언어 자동 자막만 받는다. 자동 번역 자막은
`youtube:skip=translated_subs`로 제외한다. 각 요청에는 다음 보수적 기본값을 항상 준다.

- yt-dlp·추출기·조각 재시도 횟수: `0`
- 일반 요청 사이 대기: 2초
- 자막 다운로드 전 대기: 35초

HTTP 429는 세부 오류·URL·영상 식별자를 남기지 않고 `rate_limited` 결과로만 전달한다.
이 결과가 나오면 같은 실행에서 Provider를 시작하거나, 이후 통합 단계의 STT 오디오
다운로드를 시작하면 안 된다. 다음 예약 실행은 처음 경로부터 다시 시작한다.

PoToken Provider·쿠키·고정 EIP가 429를 해결하는지 여부는 이 Work의 결론이 아니다.
인증된 단일 canary는 별도 인프라 승인이 난 뒤에만 수행한다.

## W-019: yt-dlp·PoToken 자막 폴백 준비

`YtDlpTranscriptExtractor`는 먼저 토큰 없이 자동 자막을 받는다. 429가 아닌 실패에서만
bgutil Provider를 **현재 파이프라인 작업 동안에만** `127.0.0.1`에 실행해 `yt-dlp`의
PoToken 요청을 처리하고, 작업이 끝나면 종료한다. 429 뒤에는 Provider를 시작하지
않으며, 상시 Provider나 외부 포트는 만들지 않는다.

필요 조건은 다음과 같다.

1. Python 의존성 설치: `uv sync`
2. Node.js 22 이상 설치
3. Python 패키지와 같은 버전의 bgutil Provider 소스를 별도 작업 디렉터리에 준비

```powershell
git clone --depth 1 --branch 2.0.0 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git C:\tools\bgutil-ytdlp-pot-provider
Set-Location C:\tools\bgutil-ytdlp-pot-provider\server
npm ci
npx tsc
```

통합 단계에서는 아래처럼 빌드된 `server` 경로를 전달한다. 자막 원문은 콘솔에 출력하지
않고 성공 여부와 길이만 운영 요약에 기록한다.

```python
from pathlib import Path

from transcript.ytdlp_extractor import YtDlpTranscriptExtractor

extractor = YtDlpTranscriptExtractor(
    provider_home=Path(r"C:\tools\bgutil-ytdlp-pot-provider\server")
)
```

Provider 원본과 Python 플러그인은 같은 주 버전으로 함께 갱신한다.

## W-022: 일일 yt-dlp·PoToken canary

`transcript.ytdlp_canary`는 일반 경로를 먼저 검사하고, 그 결과가 429가 아닐 때만
Provider 경로를 검사한다.

1. 일반 `yt-dlp` 자동 자막 경로
2. `web` 클라이언트와 작업 수명에 한정된 bgutil Provider를 쓰는 PoToken 경로

일반 경로가 `rate_limited`면 Provider 요청을 추가로 보내지 않고 Provider 경로는
`skipped_rate_limited`로 끝낸다. 이는 Provider의 효과를 부정하는 결론이 아니라, 이미
제한된 IP에 같은 실행에서 추가 요청을 보내지 않는 안전장치다.

공개 검사 영상 ID는 호출 시에만 전달한다. 코드·출력 JSON·로그에는 영상 ID, 자막 원문,
PoToken, 자막 URL 쿼리를 쓰지 않는다. 결과에는 안전한 오류 코드, 각 경로의 소요 시간과
자막 글자 수, 잠긴/설치된 버전만 포함한다.

```powershell
$uv = 'C:\Users\aspom\AppData\Roaming\Python\Python314\Scripts\uv.exe'
$canaryVideoId = '<공개 검사 영상 ID 11자>'

# 최초 한 번 또는 lock 변경 뒤에 실행한다.
& $uv sync --locked

& .\.venv\Scripts\python.exe -m transcript.ytdlp_canary `
  --video-id $canaryVideoId `
  --provider-home C:\tools\bgutil-ytdlp-pot-provider\server
```

`uv sync --locked`로 `uv.lock`과 일치시킨 `.venv`의 Python을 직접 실행해야 아래 종료
코드가 스케줄러까지 보존된다. `uv run`은 자식 명령의 비영(0) 종료 코드를 모두 `1`로
바꾸므로, 일일 스케줄러 명령으로 쓰지 않는다. 종료 코드는 다음과 같다.

| 코드 | 의미                                                       | 조치                                               |
| ---- | ---------------------------------------------------------- | -------------------------------------------------- |
| `0`  | 두 자막 경로와 upstream 버전 조회가 정상                   | 조치 없음                                          |
| `2`  | Provider 준비·의존성 일치·자막 추출·upstream 조회 중 실패  | JSON의 안전한 오류 코드를 보고 재현·원인을 확인    |
| `3`  | 자막 경로는 정상이지만 공식 yt-dlp 안정 릴리스가 더 새로움 | 호환성 검증 Work를 열고, 자동 업데이트는 하지 않음 |

일일 스케줄러는 이 명령의 JSON 한 줄과 종료 코드만 수집하고, `0`이면 알리지 않는다. `2`나
`3`일 때만 Codex가 원인 후보와 다음 조치를 정리한다. 스케줄러를 AWS에 배포하는 작업은 이
Work 범위가 아니며, 별도 인프라 승인 후 결정한다.
