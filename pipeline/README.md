# Pipeline 로컬 실행

## W-030: DataImpulse 라이브러리 우선 transcript 큐 실행

현재 `transcript/pending` 큐는 다음 순서로 처리한다.

1. DataImpulse Residential Proxy가 강제된 `youtube-transcript-api`
2. 라이브러리의 429 이외 안전한 실패에서 `yt-dlp`
3. `yt-dlp`가 명시적으로 PoToken을 요구할 때만 on-demand Provider

```cmd
.venv\Scripts\python.exe -m transcript.stage_runner --provider-home C:\tools\bgutil-ytdlp-pot-provider\server --cookie-file "%LOCALAPPDATA%\Unsponsor\secrets\youtube-cookies.txt"
```

VS Code에서는 로컬 전용 task `Pipeline: transcript queue (library → yt-dlp → PoToken)`으로 같은
모듈과 인자를 실행할 수 있다. `.vscode/tasks.json`은 Git 제외 대상이며 DataImpulse 비밀값을
담지 않는다. 명령과 task 모두 큐 재설정·재등록·재처리 대상을 고르지 않고, 실행 시점에 이미
`transcript/pending`인 항목만 기존 점유·재시도·멈춤 복구 정책으로 처리한다.

DataImpulse 설정은 W-029와 같은 루트 `.env` 변수를 재사용한다. 라이브러리는 이 설정으로 만든
전용 프록시 세션에서만 호출되고 직접 egress로 요청하지 않는다. 설정 누락·프록시 인증 실패는
민감 원문 없이 안전한 실패로 집계한 뒤 `yt-dlp`로 폴백한다. DataImpulse 자격 증명은
라이브러리에만 전달하고, `--cookie-file`은 `yt-dlp`에만 전달한다. `--provider-home`을 지정해도
`yt-dlp`가 PoToken을 명시적으로 요구하기 전에는 Provider를 시작하지 않는다.

어느 활성 외부 경로에서든 `rate_limited`가 나오면 현재 항목을 안전하게 전이하고, 같은 영상의
추가 폴백·새 Provider 시작·해당 실행의 다음 큐 점유를 중단한다. 성공 결과는 실제 경로에 따라
`video_transcripts.source`를 `library` 또는 `yt_dlp`로 저장하며, 원문과 시간 세그먼트는 기존처럼
한 트랜잭션으로 교체한다. 실행 요약에는 두 경로의 성공·안전한 실패 건수와 큐 상태 수치만 남고,
프록시 URL·자격 증명·영상 ID·자막 원문은 일반 출력과 제한된 오류 로그에 남지 않는다.

초기 데이터셋을 다시 검사하기 위한 DB 상태 변경·재등록·초기화 기능은 제공하지 않는다. 실제
초기 데이터셋 실행은 사용자가 DB 준비와 실행을 명시적으로 확인한 뒤에만 수행한다.

## W-029: DataImpulse Residential Proxy canary

큐 실행 경로를 바꾸기 전에, DataImpulse 프록시를 통한 `youtube-transcript-api`의 실제 접근과
자격 증명 마스킹을 별도로 검증한다. 이 canary는 DB·처리 큐·쿠키·yt-dlp·PoToken Provider를
사용하지 않는다.

DataImpulse 대시보드는 다음 설정을 사용한다.

- Residential Proxy의 Default Targeting
- South Korea 국가 단위 타기팅
- Rotating, HTTP/HTTPS, DNS hostname
- Rotation interval·Anonymous filter·Exclude ASN은 설정하지 않음

루트 `.env`에는 Proxy Access 상단의 기본 Login과 Password만 넣는다. Proxy List 또는 Basic URL에
표시되는 `login:password@hostname:port` 전체 문자열과 `__cr.*`가 붙은 login은 넣지 않는다. 실제
값은 Git·Issue·PR·일반 로그에 기록하지 않는다.

```dotenv
DATAIMPULSE_PROXY_USERNAME=<Proxy Access 기본 Login>
DATAIMPULSE_PROXY_PASSWORD=<Proxy Access Password>
DATAIMPULSE_PROXY_COUNTRY=kr
```

W-021에서 라이브러리 성공이 확인된 공개 자막 영상 중 3~5건을 골라 예시 파일을 복사한 로컬
입력에 넣는다. 실제 영상 ID 파일은 Git에서 제외된다.

```cmd
copy dataimpulse-canary.example.json dataimpulse-canary.local.json
```

```cmd
.venv\Scripts\python.exe -m transcript.dataimpulse_canary --video-id-file dataimpulse-canary.local.json
```

영상마다 별도 HTTP 세션으로 최대 2회만 시도한다. 두 번째 시도까지 `rate_limited`이면 남은
영상은 요청하지 않는다. 출력 JSON에는 순번별 안전 코드·시도 횟수·소요 시간·자막 글자 수와
전체 판정만 포함하며, 영상 ID·자막 원문·프록시 URL·자격 증명은 포함하지 않는다. 상세 오류는
기본값 `%LOCALAPPDATA%\Unsponsor\logs\dataimpulse-canary-errors.log`에만 기록되고 같은 민감값을
마스킹한다.

| 종료 코드 | 판정          | 의미                                                             |
| --------- | ------------- | ---------------------------------------------------------------- |
| `0`       | `PASS`        | 3~5건이 첫 시도에 모두 성공                                      |
| `3`       | `CONDITIONAL` | 모두 성공했지만 한 건 이상이 제한된 두 번째 시도에서 회복        |
| `2`       | `FAIL`        | 최종 실패·반복 429·설정 오류 또는 요청하지 않은 남은 항목이 있음 |

실제 canary 3건은 모두 첫 시도에 성공했고 재시도·429·접근 제한·인증 오류 없이 `PASS`였다.
이 결과를 근거로 W-030에서 라이브러리를 현재 큐의 첫 경로로 재도입했다.

## W-028: yt-dlp 단일 transcript 단계 전환 — 이전 기준

W-029 검증 전에는 큐의 `transcript/pending` 작업을 yt-dlp·PoToken 단일 경로로 처리했다.
W-030 이후 일반 실행 명령은 같지만, 현재 경로 순서는 위 W-030 절을 따른다. 성공한 자막의
원문·출처·시간 세그먼트는 한 DB 트랜잭션으로 교체하고, 그 뒤에만 큐를 `identify/pending`으로
넘기는 계약은 유지한다.

```cmd
.venv\Scripts\python.exe -m transcript.stage_runner --provider-home C:\tools\bgutil-ytdlp-pot-provider\server --cookie-file "%LOCALAPPDATA%\Unsponsor\secrets\youtube-cookies.txt"
```

적용 전에는 아래 명령으로 기존 자막 `source` 행이 새 enum에 안전하게 포함되는지 먼저 확인한다.
이 명령은 출처별 행 수와 적용 가능 여부만 출력하며, 영상 ID와 자막 원문은 읽거나 출력하지 않는다.

```cmd
.venv\Scripts\python.exe -m transcript.migration_preflight
```

`safe_to_migrate=True`일 때에만 Flyway V5를 로컬 DB에 적용한다. `False`라면 기존 행을 임의로
변경하거나 삭제하지 않고, 별도 결정으로 보존 방식을 정한다.

`--cookie-file`은 Netscape 형식의 레포 밖 YouTube 쿠키 파일을 `yt-dlp`에만 전달한다.
DB·일반 실행 요약에는 전달하거나 기록하지 않는다. Provider가 준비되지 않은 환경에서는
`--provider-home`을 생략할 수 있다. 이 경우 PoToken이 필요한 yt-dlp 자막은
`po_token_required`로만 끝난다. `rate_limited_stop=1`이면 HTTP 429를 받은 현재 영상만
안전하게 전이하고, 아직 점유하지 않은 다른 영상은 요청하지 않은 채 `pending`으로 남긴다.
현재 실행 요약에는 `library`·`yt_dlp` 경로별 성공·안전한 실패 건수와 큐 상태 수치만 출력한다.

로컬 직접 egress에서 IP 차단이 확인된 `youtube-transcript-api`는 W-029의 DataImpulse canary
`PASS` 뒤 W-030에서 프록시 전용 경로로만 재도입했다. 직접 egress는 계속 허용하지 않는다.

기본 제한 오류 로그는 `%LOCALAPPDATA%\Unsponsor\logs\transcript-errors.log`이며,
`--error-log <경로>`로 로컬 전용 파일을 바꿀 수 있다. 외부 오류 원문·traceback·영상 ID·URL·자막
원문은 기록하지 않고 구성요소명과 안전 코드만 남긴다. 쿠키·Authorization·Proxy-Authorization·
PoToken·프록시 자격 증명은 방어적으로 계속 마스킹한다.

W-021의 32건 실제 큐 실행은 당시 라이브러리 경로와 저장 성공을 확인한 일회성 배치 통합
기준선이다. 현재 자막 경로의 DB 계약을 확인하기 위해 기존 큐를 비우거나 재처리하지 않는다. 아래
검증은 현재 로컬 MySQL의 단일 트랜잭션 안에서 가짜 영상으로 yt-dlp 성공, 자막 부재, 429 중단, 멈춘 행
복구, 최신 자막·세그먼트 교체를 확인한 뒤 **항상 롤백**한다. 현재 행·자막·큐 상태는 커밋되지
않으며 외부 YouTube에도 요청하지 않는다. MySQL의 auto-increment 값에는 작은 번호 공백이 생길 수
있으므로, 로컬 워커를 함께 실행하지 않는 상태에서만 사용한다.

```cmd
.venv\Scripts\python.exe -m scripts.verify_transcript_stage_db --confirm-rollback-transaction
```

이 명령은 설정된 MySQL 호스트가 loopback이 아니면 중단하며, 결과에는 영상 ID·자막 원문을
출력하지 않는다.

## W-025: HTTP 429 안전한 폴백 계약

yt-dlp 자막 요청은 수동 자막과 원본 언어 자동 자막만 받는다. 자동 번역 자막은
`youtube:skip=translated_subs`로 제외한다. 각 요청에는 다음 보수적 기본값을 항상 준다.

- yt-dlp·추출기·조각 재시도 횟수: `0`
- 일반 요청 사이 대기: 2초
- 자막 다운로드 전 대기: 35초

HTTP 429는 세부 오류·URL·영상 식별자를 남기지 않고 `rate_limited` 결과로만 전달한다.
이 결과가 나오면 같은 실행에서 Provider를 시작하면 안 되며, transcript 단계 실행기는
아직 점유하지 않은 다른 영상도 요청하지 않고 종료한다. 다음 예약 실행은 처음 경로부터
다시 시작한다.

PoToken Provider·고정 EIP가 429를 해결하는지 여부는 이 Work의 결론이 아니다. 쿠키를 쓴
로컬 큐 실행은 W-021의 실제 저장·실패 경로를 검증하는 것이며, AWS 운영 채택은 별도 검증한다.

## W-020: faster-whisper STT 최종 폴백 — 취소

D-015 v3에 따라 PH-1 자막 경로에서는 오디오 다운로드·FFmpeg 변환·faster-whisper STT를
사용하지 않는다. 공개 검사에서 추출 품질이 PH-1의 한국어 리뷰 근거 기준에 미치지 못했고,
공개 영상의 오디오 요청도 인증 요구라는 추가 운영 경계를 만들었다. 자막 부재 영상의 지연
재확인 정책은 별도 Work에서 결정한다.

W-026에서 STT 구현·의존성·검증 파일을 제거했다. PH-1 실행 경로에는 이 경로가 없다.

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
