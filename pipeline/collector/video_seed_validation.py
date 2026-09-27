"""PH-1에서 사람이 고른 영상 시드를 외부 호출 없이 검증한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from common.config import ConfigurationError, PipelineSettings, load_settings


@dataclass(frozen=True)
class VideoSeedSelection:
    """검증된 영상 시드의 결정적 순서다."""

    video_ids: tuple[str, ...] = field(repr=False)

    @property
    def count(self) -> int:
        return len(self.video_ids)


def prepare_video_seed_selection(settings: PipelineSettings) -> VideoSeedSelection:
    """로컬 설정의 영상 ID 순서를 보존한 채 선정 결과를 반환한다."""

    if settings.selected_video_ids is None:
        raise ConfigurationError("selected_video_ids is required for the PH-1 video dry-run")

    return VideoSeedSelection(video_ids=settings.selected_video_ids)


def main() -> None:
    """비공개 영상 시드를 검증하고 외부 호출 없이 개수만 출력한다."""

    pipeline_root = Path(__file__).resolve().parents[1]
    try:
        settings = load_settings(
            pipeline_root.parent,
            pipeline_root / "channels.local.json",
        )
        selection = prepare_video_seed_selection(settings)
    except ConfigurationError as error:
        raise SystemExit(f"Configuration error: {error}") from error

    print(f"Selected {selection.count} PH-1 video seed(s). No API or database calls were made.")


if __name__ == "__main__":
    main()
