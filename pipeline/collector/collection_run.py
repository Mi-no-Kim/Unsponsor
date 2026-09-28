"""PH-1의 채널·영상 수집과 처리 큐 등록을 한 번에 실행한다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from collector.channel_seed_sync import ChannelSeedSyncResult, ChannelSeedSynchronizer
from collector.processing_queue_registration import (
    ProcessingQueueRegistrar,
    ProcessingQueueRegistrationResult,
)
from collector.video_metadata_sync import (
    VideoMetadataSyncResult,
    VideoMetadataSynchronizer,
)
from collector.video_seed_validation import prepare_video_seed_selection
from common.config import ChannelSeed, PipelineSettings, load_settings
from common.mysql import MySqlConnectionFactory
from common.youtube import YouTubeDataClient


class ChannelSynchronizer(Protocol):
    def synchronize(self, seeds: Sequence[ChannelSeed]) -> ChannelSeedSyncResult:
        """채널 시드를 동기화한다."""


class VideoSynchronizer(Protocol):
    def synchronize(self, video_ids: Sequence[str]) -> VideoMetadataSyncResult:
        """선정된 영상 메타데이터를 동기화한다."""


class QueueRegistrar(Protocol):
    def register(
        self, youtube_video_ids: Sequence[str]
    ) -> ProcessingQueueRegistrationResult:
        """동기화된 영상을 처리 큐에 등록한다."""


@dataclass(frozen=True)
class CollectionRunResult:
    """수집 실행의 사용자 노출용 요약 수치다."""

    channel_sync: ChannelSeedSyncResult
    video_sync: VideoMetadataSyncResult
    queue_registration: ProcessingQueueRegistrationResult

    @property
    def created_count(self) -> int:
        return self.queue_registration.created_count

    @property
    def updated_count(self) -> int:
        return self.channel_sync.synchronized_count + self.video_sync.synchronized_count

    @property
    def skipped_count(self) -> int:
        return (
            self.queue_registration.existing_count
            + self.queue_registration.missing_video_count
        )

    @property
    def failed_count(self) -> int:
        # 실행 중 오류는 예외로 끝내고 성공한 실행은 실패 수 0으로 보고한다.
        return 0


def run_collection(
    settings: PipelineSettings,
    channel_synchronizer: ChannelSynchronizer,
    video_synchronizer: VideoSynchronizer,
    queue_registrar: QueueRegistrar,
) -> CollectionRunResult:
    """채널 → 선정 영상 → 처리 큐 순서로 PH-1 수집을 실행한다."""

    channel_sync = channel_synchronizer.synchronize(settings.channels)
    selection = prepare_video_seed_selection(settings)
    video_sync = video_synchronizer.synchronize(selection.video_ids)
    queue_registration = queue_registrar.register(selection.video_ids)
    return CollectionRunResult(
        channel_sync=channel_sync,
        video_sync=video_sync,
        queue_registration=queue_registration,
    )


def _print_summary(result: CollectionRunResult) -> None:
    print(
        "Collection summary: "
        f"created={result.created_count}, "
        f"updated={result.updated_count}, "
        f"skipped={result.skipped_count}, "
        f"failed={result.failed_count}."
    )
    print(
        "Details: "
        f"channels synchronized={result.channel_sync.synchronized_count}, "
        f"videos synchronized={result.video_sync.synchronized_count}, "
        f"queues created={result.queue_registration.created_count}, "
        f"queues preserved={result.queue_registration.existing_count}."
    )
    if result.video_sync.unavailable_count:
        print(f"Skipped unavailable video seed(s): {result.video_sync.unavailable_count}.")
    if result.video_sync.unregistered_channel_count:
        print(
            "Skipped video seed(s) for an unregistered channel: "
            f"{result.video_sync.unregistered_channel_count}."
        )


def main() -> None:
    """비공개 로컬 설정으로 전체 PH-1 수집을 실행한다."""

    pipeline_root = Path(__file__).resolve().parents[1]
    repository_root = pipeline_root.parent
    settings = load_settings(repository_root, pipeline_root / "channels.local.json")
    youtube_client = YouTubeDataClient(settings.youtube)
    connection_factory = MySqlConnectionFactory(settings.mysql)
    result = run_collection(
        settings,
        ChannelSeedSynchronizer(youtube_client, connection_factory),
        VideoMetadataSynchronizer(youtube_client, connection_factory),
        ProcessingQueueRegistrar(connection_factory),
    )
    _print_summary(result)


if __name__ == "__main__":
    main()
