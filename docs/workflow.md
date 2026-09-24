# 개발 워크플로 — Planning 프레임워크

## Planning 계층

```text
Feature
  ↓
Phase
  ↓
Issue
  ↓
Work Unit
```

각 계층은 서로 다른 크기와 목적을 가진다.

### Feature

사용자에게 어떤 기능과 동작을 제공할 것인가.

### Phase

프로젝트가 다음으로 도달해야 하는 큰 기능적 상태.

### Issue

그 Phase 안에서 완성해야 하는 하나의 의미 있는 기능적 결과 또는 시스템 변화.

### Work Unit

그 Issue를 구현하기 위한, 하나의 PR로 구현·검증·리뷰할 수 있는 가장 작은 의미 있는 변화 단위.

> PR은 Planning 단위가 아니다.

## 분해 기준 (Phase → Issue → Work Unit)

Phase 1의 범위 자체가 지나치게 넓었던 과거 실패(사이트의 60%를 요구)는 Phase 재분배로 이미 해결됐다 — phase.md의 "구성 원칙"과 PH-1의 좁은 범위(제품 1~2개, 채널 3~5개, 영상 10~20개) 정의가 그 결과다. 아래 Issue·Work Unit 분해 기준은 그 문제의 재발 방지가 아니라, 이미 좁혀진 Phase 안에서 작업을 관리 가능한 단위로 쪼개기 위한 별도의 판단 기준이다. 숫자로 강제하지 않는다 (D-005).

### Phase → Issue

- 한 Issue는 그 Phase의 목표에 직접 기여하는 독립적인 조각이어야 한다 — 다른 Phase 몫의 작업을 끌어오지 않는다.
- 한 Issue는 다른 아직 없는 Issue 없이도 독립적으로 완결·검증 가능해야 한다 — 화면까지 연결돼야 한다는 뜻은 아니고, 그 Issue의 산출물을 직접 확인(DB 조회, 스크립트 실행, API 직접 호출 등)할 수 있으면 된다. 선행 관계가 있으면 순서만 조정하고, 억지로 하나로 합치지 않는다.
- Phase 끝에 전체 파이프라인을 검증하는 별도 통합 Issue는 두지 않는다 — Issue를 다 풀고 마지막에 몰아 통합하면 Issue 하나씩 풀며 코드가 점진적으로 쌓인다는 원칙과 충돌한다. 각 Issue를 무엇으로 검증할지(실제 데이터/mock 등)는 이 문서에서 규칙으로 정하지 않고, 상황(가용 데이터, 이전 단계 완료 여부 등)에 맞게 판단한다.
- Goal 하나로 설명이 안 되는, 서로 무관한 기능은 Issue를 나눈다.
- Issue는 레이어(수집→처리→표시) 기준으로 나눈다 — 위 기준대로 레이어별 산출물을 직접 검증할 수 있어 화면까지 연결될 필요가 없다. 얇은 수직 슬라이스(walking skeleton)는 Phase 스코프를 좁히는 방식(phase.md의 구성 원칙)으로 이미 처리되는 개념이라, Issue 레벨에서 다시 쓸 필요가 없다. **단, 레이어 사이의 결합이 강해서**(예: 프론트엔드 작업 중 API 응답 형태를 바꿔야 하는 경우가 잦은 백엔드 API ↔ 프론트엔드 화면) **한쪽을 만들다가 다른 쪽을 자주 고쳐야 한다면, 그 두 레이어는 하나의 Issue로 유지한다.** 되돌아가 고칠 필요가 거의 없는 레이어(산출물 스키마가 이미 확정된 파이프라인 단계 등)만 별도 Issue로 나눈다.
- Issue 하나가 지나치게 많은 Work Unit(체감상 10개 이상)을 요구할 것 같으면 더 쪼갤 수 있는지 확인한다.

### Issue → Work Unit

- 한 Work Unit은 리뷰어가 맥락 전환 없이 한 번에 볼 수 있는 분량이어야 한다. 정확한 줄 수 기준은 두지 않고, "리뷰하다가 처음 뭘 보고 있었는지 잊어버릴 것 같다" 싶으면 쪼갠다.
- 한 Work Unit은 하나의 논리적 변경만 담는다 — `commit-convention.md`의 "type 하나로 요약 안 되면 커밋을 나눈다" 원칙과 같은 맥락이다.
- Work Unit 하나는 그 자체로 빌드가 깨지지 않아야 한다 (기능이 미완성이어도 컴파일·빌드는 통과).
- Work Unit 간 순서 의존이 있으면 PR 설명(관련 Issue/Work Unit)에 선행 Work Unit을 명시한다.

## 진행 중인 Issue 수정

Issue는 작업하는 도중에 수정하거나 추가할 수 있다. 화면처럼 진행하면서 구체화되는 Issue는 처음부터 Scope를 다 확정하지 못하는 게 정상이다. 대신 무엇이 바뀌었는지 흔적을 남긴다.

- Scope·Done When을 바꾸면 Issue 본문을 직접 고쳐 현재 기준이 항상 본문에 있게 하고, 무엇을 왜 바꿨는지 Issue 코멘트로 남긴다.
- Work Unit이 새로 필요해지면 Sub-issue로 추가한다 (번호는 새로 매긴다). 필요 없어진 Work Unit은 지우지 않고 사유를 남겨 닫는다 — `pr-template-issue.md`의 "명시적 종료·취소 / Scope에서 제거" 항목에 대응한다.
- 바꾸려는 내용이 Issue의 Goal 자체를 바꾸거나 다른 Phase 몫의 작업을 끌어오면, 기존 Issue 수정으로 처리하지 않고 새 Issue로 분리할지 먼저 확인한다 (위 "분해 기준").
- 변경에 Protected Change가 포함되면 `rules.md` 1번대로 승인받은 뒤 반영한다.

## Git 모델과의 Mapping

Planning 모델과 Git 모델은 분리하고, 아래 규칙으로 연결한다.

| Planning    | Git                                      |
| ----------- | ---------------------------------------- |
| Phase       | Milestone                                |
| Issue       | GitHub Issue                             |
| Work Unit   | GitHub Sub-issue                         |
| (구현 단위) | PR — Work Unit(Sub-issue) 하나당 PR 하나 |

Milestone 하나가 Phase 하나에 대응하고, 그 안의 Issue들이 Phase 안에서 완성해야 할 결과 단위이며, 각 Issue 아래 Sub-issue가 실제 구현 최소 단위(Work Unit)다. Sub-issue 하나는 PR 하나로 구현·검증·리뷰된다.

Sub-issue에는 Milestone을 붙이지 않는다. Milestone에는 Issue만 넣고, Sub-issue는 부모 Issue를 통해 그 Phase에 속한다.

버그 리포트로 연 Issue는 기본적으로 Milestone에 배정하지 않는다. Milestone은 계획된 Phase 진행 상태를 나타내는 용도라, 반응적으로 발생한 버그 수정을 특정 Phase 진행률에 포함시키면 실제 진행 상황이 왜곡된다. 버그인지 여부는 Issue 라벨(`bug`)로 구분한다 (D-001).

### 라벨

| 대상      | 라벨                                                                                          | 붙이는 기준                                                           |
| --------- | --------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Issue     | `enhancement`                                                                                 | 계획된 Issue 전부 — 기능·설정·문서 등 작업 종류와 관계없다            |
| Issue     | `bug`                                                                                         | main에 merge된 기능의 버그로 연 Issue (버그 템플릿이 자동으로 붙인다) |
| Work Unit | `work:feat` / `work:fix` / `work:test` / `work:refactor` / `work:docs` / `work:chore` 중 하나 | 그 Work PR의 squash 커밋 type과 같게                                  |

- Work Unit 라벨은 하나만 붙인다. Work PR은 squash되어 커밋 하나가 되므로, 그 커밋의 type(`commit-convention.md`)이 곧 라벨이다. type 하나로 요약이 안 되면 라벨을 여러 개 붙이지 않고 Work Unit을 나눈다 (위 "분해 기준").
- 코드 변경과 함께 고친 문서(예: 기능을 구현하면서 `schema.md`나 `decisions.md`를 갱신)는 그 코드 변경의 type을 따른다. `work:docs`는 문서만 바꾼 Work Unit에만 붙인다 — `commit-convention.md`의 `docs`("문서만 변경")와 같은 기준이다.
- GitHub 기본 라벨(`documentation`, `question` 등)은 지우지 않고 그대로 둔다.

라벨 규칙의 배경은 D-040.

### 버그 처리

| 상황                                         | 새 Issue                      | 처리                                                                                  |
| -------------------------------------------- | ----------------------------- | ------------------------------------------------------------------------------------- |
| 지금 작업 중인 Issue 범위 안에서 발견한 버그 | 만들지 않음                   | 같은 Issue에 새 Work Unit으로 추가하거나, 지금 Work PR 안에서 리뷰 반영 수정으로 처리 |
| 이미 main에 merge된 기능의 버그              | 새 GitHub Issue (버그 템플릿) | 일반 Issue와 같은 흐름: `work/*` → `issue/*`(squash) → `main`                         |

새로 만든 버그 Issue도 일반 Issue와 같은 I 번호를 쓴다 — 같은 브랜치 규칙(`issue/i-{이슈번호}-...`)을 타기 때문이다. 일반 Issue와는 `bug` 라벨로 구분하고, Milestone에는 배정하지 않는다 (D-001, D-033).

## 브랜치·머지 전략

- Work Unit 브랜치는 Issue 브랜치에 **squash merge**한다.
- Issue 브랜치는 main에 **일반 merge**(squash 아님)한다.
- PR 본문에는 닫을 Issue를 `Closes #N`(GitHub `#` 번호)으로 적는다. Issue PR은 main으로 머지되므로 GitHub 기본 기능으로 Issue가 닫힌다. Work PR은 `issue/*`로 머지돼 GitHub 기본 기능이 동작하지 않으므로, GitHub Actions 워크플로가 머지 시점에 본문의 `Closes #N`을 읽어 해당 Work Unit(Sub-issue)을 닫는다. 이 워크플로는 CI와 함께 구성한다 (D-034).
- Issue 번호와 Work Unit 번호는 각각 전체 프로젝트 기준으로 계속 증가하는 누적 번호를 쓴다 (Phase나 Issue가 바뀌어도 리셋하지 않음). 이 번호는 GitHub `#` 번호와 별개로 매기는 프로젝트 번호다 — GitHub는 Issue·Sub-issue·PR이 `#` 번호 하나를 같이 써서, `#` 번호만으로는 Issue와 Work Unit을 구분할 수 없기 때문이다 (D-032).

### 브랜치 네이밍

| 대상             | 형식                                          | 예시                          |
| ---------------- | --------------------------------------------- | ----------------------------- |
| Issue 브랜치     | `issue/i-{이슈번호}-{슬러그}`                 | `issue/i-023-product-search`  |
| Work Unit 브랜치 | `work/i-{이슈번호}-w-{워크유닛번호}-{슬러그}` | `work/i-023-w-068-search-api` |

### 제목 표기

GitHub Issue·Sub-issue 제목 앞에 프로젝트 번호를 붙인다. 번호 자릿수는 브랜치 네이밍과 같게 쓴다 (D-032).

| 대상      | 형식                                     | 예시                                      |
| --------- | ---------------------------------------- | ----------------------------------------- |
| Issue     | `[I-{이슈번호}] {제목}`                  | `[I-003] 자막/STT 확보`                   |
| Work Unit | `[I-{이슈번호}-W-{워크유닛번호}] {제목}` | `[I-003-W-012] 자막 라이브러리 추출 구현` |

GitHub 기능(Sub-issue 연결, PR의 Issue 링크)에는 GitHub `#` 번호를 쓰고, 커밋 footer에는 프로젝트 번호를 쓴다 (`commit-convention.md`).

## 관련 문서

- 기능(Feature) 목록과 제품 스펙: `project-spec.md`
