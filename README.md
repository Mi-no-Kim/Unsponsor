# Unsponsor

대한민국 테크 유튜브 리뷰 영상에서 광고·협찬 구간을 걷어내고 장단점만 추출해, 제품을 비교·검색할 수 있게 해주는 클린 리뷰 검색 엔진.

## 상태

기획 단계. 설계 문서와 backend/pipeline/frontend 초기 뼈대(스캐폴딩)만 있고, 기능 코드는 아직 없다.

## 문서

- [`docs/project-spec.md`](docs/project-spec.md) — 프로젝트 스펙 (목표, 기능, 기술 스택, 아키텍처)
- [`docs/feature.md`](docs/feature.md) — Feature 목록 (기능 상세)
- [`docs/schema.md`](docs/schema.md) — DB 스키마
- [`docs/workflow.md`](docs/workflow.md) — Planning·Git 구조, 분해 기준
- [`docs/phase.md`](docs/phase.md) — Phase 로드맵
- [`docs/decisions.md`](docs/decisions.md) — 결정 로그
- [`docs/rules.md`](docs/rules.md) — AI 협업 규칙 (Protected Change, Git, 리뷰 기준 등)
- [`docs/commit-convention.md`](docs/commit-convention.md) — 커밋 메시지 규칙
- [`docs/pr-template-work.md`](docs/pr-template-work.md) — Work PR 템플릿
- [`docs/pr-template-issue.md`](docs/pr-template-issue.md) — Issue PR 템플릿
- [`docs/review-template.md`](docs/review-template.md) — 리뷰 체크리스트

## AI 작업 가이드

상황별로 먼저 확인해야 하는 문서:

| 상황 | 확인할 문서 |
| --- | --- |
| Git 상태변경(commit/push/PR) | `rules.md` 3번, `commit-convention.md` |
| PR 생성 | `pr-template-work.md` 또는 `pr-template-issue.md` |
| Feature 새로 정의/변경 | `feature.md` |
| Issue/Work Unit 새로 만들기 | `workflow.md` "분해 기준" |
| 설계 판단이 필요한 순간 | `rules.md` 2번(기록 대상), `decisions.md` |
| 코드/문서 리뷰 | `review-template.md` |
| Protected Change(DB·API·Dependency 등) | `rules.md` 1번 — 승인 전 확정·실행 금지 |
| 문서 자체를 수정할 때 | `rules.md` 6번 |

## 로컬 실행

(아직 없음 — 코드 작성이 시작되면 채운다)
