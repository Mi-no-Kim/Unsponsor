# Pipeline 로컬 실행

## W-019: yt-dlp·PoToken 자막 폴백 준비

`YtDlpTranscriptExtractor`는 먼저 토큰 없이 자동 자막을 받는다. 이 경로가 실패하면
bgutil Provider를 **현재 파이프라인 작업 동안에만** `127.0.0.1`에 실행해 `yt-dlp`의
PoToken 요청을 처리하고, 작업이 끝나면 종료한다. 상시 Provider나 외부 포트는 만들지
않는다.

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
