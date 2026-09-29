"""yt-dlp와 PoToken 자막 경로를 원문 없이 진단하는 canary 명령이다."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from typing import Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen

from transcript.library_extractor import TranscriptExtractionResult, TranscriptFailure
from transcript.ytdlp_extractor import YtDlpTranscriptExtractor


_DEFAULT_LANGUAGE_CODES = ("ko", "en")
_OFFICIAL_RELEASE_URL = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SAFE_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z._+-]+$")
_NODE_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_RELEASE_DATE_PATTERN = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})")


class _CanaryExtractor(Protocol):
    def extract_token_free(
        self, video_id: str, *, language_codes: Sequence[str]
    ) -> TranscriptExtractionResult:
        """PoToken 없이 자막을 추출한다."""

    def extract_with_provider(
        self, video_id: str, *, language_codes: Sequence[str]
    ) -> TranscriptExtractionResult:
        """Provider를 사용해 자막을 추출한다."""


ExtractorFactory = Callable[[Path | None, str], _CanaryExtractor]
LockedVersionReader = Callable[[Path], Mapping[str, str]]
InstalledVersionReader = Callable[[str], str | None]
NodeVersionReader = Callable[[str], str | None]
ProviderVersionReader = Callable[[Path], str | None]
UpstreamVersionFetcher = Callable[[], str | None]
MonotonicClock = Callable[[], float]


@dataclass(frozen=True)
class CanaryCheck:
    """원문·요청 URL을 포함하지 않는 경로별 진단 결과다."""

    code: str
    duration_ms: int
    text_length: int | None

    @property
    def is_success(self) -> bool:
        return self.code == "ok"


@dataclass(frozen=True)
class CanaryVersions:
    """업데이트 판단에 필요한 버전만 보관한다."""

    yt_dlp_locked: str | None
    yt_dlp_installed: str | None
    bgutil_plugin_locked: str | None
    bgutil_plugin_installed: str | None
    node: str | None
    provider_source: str | None


@dataclass(frozen=True)
class CanaryReport:
    """스케줄러가 소비할 수 있는 안전한 canary 결과다."""

    environment_code: str
    versions: CanaryVersions
    token_free: CanaryCheck
    potoken: CanaryCheck
    upstream_code: str
    upstream_latest_version: str | None

    @property
    def exit_code(self) -> int:
        if self.environment_code != "ok":
            return 2
        if not self.token_free.is_success or not self.potoken.is_success:
            return 2
        if self.upstream_code == "upstream_version_check_failed":
            return 2
        if self.upstream_code == "upstream_version_available":
            return 3
        return 0

    def to_dict(self) -> dict[str, object]:
        """원문·PoToken·검사 영상 ID 없이 JSON 결과를 구성한다."""

        return {
            "environment": {"code": self.environment_code},
            "versions": asdict(self.versions),
            "token_free": asdict(self.token_free),
            "potoken": asdict(self.potoken),
            "upstream": {
                "code": self.upstream_code,
                "latest_version": self.upstream_latest_version,
            },
        }


class YtDlpCanary:
    """두 자막 경로와 업스트림 버전을 안전한 요청 순서로 확인한다."""

    def __init__(
        self,
        *,
        provider_home: Path,
        node_executable: str = "node",
        lock_path: Path | None = None,
        extractor_factory: ExtractorFactory | None = None,
        locked_version_reader: LockedVersionReader | None = None,
        installed_version_reader: InstalledVersionReader | None = None,
        node_version_reader: NodeVersionReader | None = None,
        provider_version_reader: ProviderVersionReader | None = None,
        upstream_version_fetcher: UpstreamVersionFetcher | None = None,
        monotonic_clock: MonotonicClock = time.monotonic,
    ) -> None:
        self._provider_home = Path(provider_home)
        self._node_executable = node_executable
        self._lock_path = (
            Path(lock_path)
            if lock_path is not None
            else Path(__file__).resolve().parents[1] / "uv.lock"
        )
        self._extractor_factory = extractor_factory or _create_extractor
        self._locked_version_reader = locked_version_reader or _read_locked_versions
        self._installed_version_reader = installed_version_reader or _read_installed_version
        self._node_version_reader = node_version_reader or _read_node_version
        self._provider_version_reader = provider_version_reader or _read_provider_version
        self._upstream_version_fetcher = (
            upstream_version_fetcher or _fetch_official_release_version
        )
        self._monotonic_clock = monotonic_clock

    def run(
        self,
        video_id: str,
        *,
        language_codes: Sequence[str] = _DEFAULT_LANGUAGE_CODES,
    ) -> CanaryReport:
        """입력 영상의 두 경로를 검사하고 안전한 요약만 반환한다."""

        if not _VIDEO_ID_PATTERN.fullmatch(video_id):
            raise ValueError("video_id must be an 11-character YouTube video ID")

        versions = self._read_versions()
        environment_code = _environment_code(
            versions, provider_ready=_provider_build_is_ready(self._provider_home)
        )
        token_free = self._run_token_free(video_id, language_codes)
        potoken = self._run_potoken(
            video_id,
            language_codes,
            environment_code=environment_code,
            token_free=token_free,
        )
        upstream_code, upstream_latest_version = self._check_upstream(versions)
        return CanaryReport(
            environment_code=environment_code,
            versions=versions,
            token_free=token_free,
            potoken=potoken,
            upstream_code=upstream_code,
            upstream_latest_version=upstream_latest_version,
        )

    def _read_versions(self) -> CanaryVersions:
        locked_versions = self._locked_version_reader(self._lock_path)
        return CanaryVersions(
            yt_dlp_locked=_safe_version(locked_versions.get("yt-dlp")),
            yt_dlp_installed=_safe_version(self._installed_version_reader("yt-dlp")),
            bgutil_plugin_locked=_safe_version(
                locked_versions.get("bgutil-ytdlp-pot-provider")
            ),
            bgutil_plugin_installed=_safe_version(
                self._installed_version_reader("bgutil-ytdlp-pot-provider")
            ),
            node=_safe_version(self._node_version_reader(self._node_executable)),
            provider_source=_safe_version(
                self._provider_version_reader(self._provider_home)
            ),
        )

    def _run_token_free(
        self, video_id: str, language_codes: Sequence[str]
    ) -> CanaryCheck:
        return self._run_extraction(
            lambda: self._extractor_factory(None, self._node_executable).extract_token_free(
                video_id, language_codes=language_codes
            ),
            _token_free_failure_code,
        )

    def _run_potoken(
        self,
        video_id: str,
        language_codes: Sequence[str],
        *,
        environment_code: str,
        token_free: CanaryCheck,
    ) -> CanaryCheck:
        if token_free.code == "rate_limited":
            return CanaryCheck("skipped_rate_limited", duration_ms=0, text_length=None)
        if environment_code != "ok":
            return CanaryCheck(environment_code, duration_ms=0, text_length=None)
        return self._run_extraction(
            lambda: self._extractor_factory(
                self._provider_home, self._node_executable
            ).extract_with_provider(video_id, language_codes=language_codes),
            _potoken_failure_code,
        )

    def _run_extraction(
        self,
        extract: Callable[[], TranscriptExtractionResult],
        failure_code: Callable[[TranscriptFailure | None], str],
    ) -> CanaryCheck:
        started_at = self._monotonic_clock()
        try:
            result = extract()
        except Exception:
            return CanaryCheck(
                "unexpected_error",
                duration_ms=_duration_ms(started_at, self._monotonic_clock()),
                text_length=None,
            )

        duration_ms = _duration_ms(started_at, self._monotonic_clock())
        if result.is_success:
            return CanaryCheck("ok", duration_ms=duration_ms, text_length=len(result.text or ""))
        return CanaryCheck(
            failure_code(result.failure), duration_ms=duration_ms, text_length=None
        )

    def _check_upstream(self, versions: CanaryVersions) -> tuple[str, str | None]:
        try:
            latest_version = _safe_version(self._upstream_version_fetcher())
        except Exception:
            return "upstream_version_check_failed", None
        if latest_version is None or versions.yt_dlp_locked is None:
            return "upstream_version_check_failed", latest_version
        if _release_is_newer(latest_version, versions.yt_dlp_locked):
            return "upstream_version_available", latest_version
        return "ok", latest_version


def _create_extractor(provider_home: Path | None, node_executable: str) -> YtDlpTranscriptExtractor:
    return YtDlpTranscriptExtractor(
        provider_home=provider_home,
        node_executable=node_executable,
    )


def _read_locked_versions(lock_path: Path) -> Mapping[str, str]:
    """uv.lock의 직접 의존성 버전을 원문 없이 읽는다."""

    try:
        import tomllib

        payload = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    packages = payload.get("package")
    if not isinstance(packages, list):
        return {}
    versions: dict[str, str] = {}
    for package in packages:
        if not isinstance(package, dict):
            continue
        name = package.get("name")
        version = package.get("version")
        if isinstance(name, str) and isinstance(version, str):
            versions[name] = version
    return versions


def _read_installed_version(distribution_name: str) -> str | None:
    try:
        return metadata.version(distribution_name)
    except metadata.PackageNotFoundError:
        return None


def _read_node_version(node_executable: str) -> str | None:
    try:
        completed = subprocess.run(
            [node_executable, "--version"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _read_provider_version(provider_home: Path) -> str | None:
    try:
        package = json.loads((provider_home / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = package.get("version") if isinstance(package, dict) else None
    return version if isinstance(version, str) else None


def _fetch_official_release_version() -> str | None:
    request = Request(
        _OFFICIAL_RELEASE_URL,
        headers={"Accept": "application/vnd.github+json"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.load(response)
    except (OSError, URLError, json.JSONDecodeError, ValueError):
        return None
    version = payload.get("tag_name") if isinstance(payload, dict) else None
    return version.removeprefix("v") if isinstance(version, str) else None


def _safe_version(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized if _SAFE_VERSION_PATTERN.fullmatch(normalized) else None


def _provider_build_is_ready(provider_home: Path) -> bool:
    return (provider_home / "build" / "main.js").is_file()


def _environment_code(versions: CanaryVersions, *, provider_ready: bool) -> str:
    if not provider_ready:
        return "provider_unready"
    if not _node_is_supported(versions.node):
        return "node_runtime_unsupported"
    if not _versions_match(versions.yt_dlp_locked, versions.yt_dlp_installed):
        return "dependency_version_mismatch"
    if not _versions_match(
        versions.bgutil_plugin_locked, versions.bgutil_plugin_installed
    ):
        return "dependency_version_mismatch"
    if not _major_versions_match(
        versions.bgutil_plugin_installed, versions.provider_source
    ):
        return "provider_version_mismatch"
    return "ok"


def _node_is_supported(version: str | None) -> bool:
    match = _NODE_VERSION_PATTERN.fullmatch(version or "")
    return match is not None and int(match.group(1)) >= 22


def _versions_match(expected: str | None, actual: str | None) -> bool:
    return expected is not None and expected == actual


def _major_versions_match(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    return left.split(".", 1)[0] == right.split(".", 1)[0]


def _token_free_failure_code(failure: TranscriptFailure | None) -> str:
    if failure is TranscriptFailure.RATE_LIMITED:
        return "rate_limited"
    if failure is TranscriptFailure.INVALID_RESPONSE:
        return "invalid_transcript"
    return "subtitle_download_failed"


def _potoken_failure_code(failure: TranscriptFailure | None) -> str:
    if failure is TranscriptFailure.RATE_LIMITED:
        return "rate_limited"
    if failure is TranscriptFailure.INVALID_RESPONSE:
        return "invalid_transcript"
    if failure is TranscriptFailure.PO_TOKEN_REQUIRED:
        return "token_mint_failed"
    return "subtitle_download_failed"


def _duration_ms(started_at: float, finished_at: float) -> int:
    return max(0, round((finished_at - started_at) * 1000))


def _release_is_newer(latest_version: str, locked_version: str) -> bool:
    latest_date = _release_date(latest_version)
    locked_date = _release_date(locked_version)
    return latest_date is not None and locked_date is not None and latest_date > locked_date


def _release_date(version: str) -> tuple[int, int, int] | None:
    match = _RELEASE_DATE_PATTERN.match(version)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a safe yt-dlp and PoToken transcript canary."
    )
    parser.add_argument("--video-id", required=True, help="Public canary video ID")
    parser.add_argument(
        "--provider-home",
        required=True,
        type=Path,
        help="Built bgutil Provider server directory",
    )
    parser.add_argument(
        "--node-executable",
        default="node",
        help="Node.js executable used to start the Provider",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """JSON 한 줄과 종료 코드만 출력하는 CLI 진입점이다."""

    args = _parse_arguments(argv)
    if not _VIDEO_ID_PATTERN.fullmatch(args.video_id):
        raise SystemExit("Configuration error: --video-id must be an 11-character ID")

    report = YtDlpCanary(
        provider_home=args.provider_home,
        node_executable=args.node_executable,
    ).run(args.video_id)
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
