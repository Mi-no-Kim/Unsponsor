# 개발 규칙

AI(Claude)와 협업할 때 지키는 규칙이다. `project-spec.md`(무엇을 만드는가), `workflow.md`(Planning·Git 구조)와 함께 읽는다. 중요한 판단은 사용자가 하고, AI는 세부 작업을 압축해서 처리한다는 게 기본 방향이다.

## 1. Protected Change — 승인 없이 확정·실행하지 않음

다음 변경은 AI가 설계·제안까지는 하되, 사용자 승인 전에는 공식 변경으로 확정하거나 실행하지 않는다. "새로 추가하는 것"도 대상이다 — 기존 걸 안 건드렸다는 이유로 이 목록을 우회하지 않는다.

- **DB 스키마** — 테이블/컬럼 생성·삭제·rename, 타입 변경, PK/FK/unique/index 변경
- **API·외부 Contract** — 엔드포인트 추가/삭제, request/response 스키마 변경, 인증 요구사항 변경
- **Dependency** — 추가/교체/제거 (production뿐 아니라 dev, test, build 의존성 포함)
- **파괴적 데이터 작업** — 삭제, bulk update, 되돌리기 어려운 migration
- **인증/보안 경계** — 로그인 방식, 접근 제어, 토큰 정책, 암호화 방식 변경
- **외부 인프라·배포 구조** — AWS 리소스 추가/제거, 배포 방식 변경
- **Public Config** — 환경변수 rename, 외부 연동 config 포맷 변경

## 2. 결정 기록이 필요한지 판단

다음 중 2개 이상 해당하면 즉흥적으로 넘어가지 않고 `decisions.md`에 기록할 결정 후보로 본다.

- 여러 파일·기능에 영향을 주는가
- 나중에 바꾸면 비용이 큰가
- 기록하지 않으면 나중에 또 고민하게 될 가능성이 큰가

Protected Change와는 별개 기준이다 — Protected Change는 항상 승인이 필요하지만, 결정 기록 대상인지는 이 기준으로 따로 판단한다. 형식·상태 정의는 `decisions.md` 참고.

## 3. Git

- 상태를 바꾸지 않는 조회 명령(status·log·diff·show·blame 등)은 허가 없이 실행한다.
- 상태를 바꾸는 명령(add·commit·branch·push·reset·rebase·checkout 경로·restore·stash·fetch·gc 등)은 지시가 있을 때만 실행한다.
- 다음은 명시적 지시 없이는 절대 실행하지 않는다: `push --force`(`--force-with-lease` 포함), `reset --hard`, `clean -fd`/`-fdx`, `branch -D`, 기타 shared history를 바꾸는 작업.
- 커밋 메시지는 Conventional Commits 형식을 쓴다: `type: description` (`feat`/`fix`/`test`/`refactor`/`docs`/`chore`). 세부 규칙과 타입별 예시는 `commit-convention.md` 참고. `fix`, `update`, `temp` 같은 의미 없는 메시지는 쓰지 않는다.
- **push·PR 생성은 그 행동을 사용자가 명시적으로 지시했을 때만 실행한다.** 하나의 지시가 다음 단계까지 자동으로 포함하지 않는다 — 예를 들어 "push해줘"는 push만 하고, 이어서 "pr 작성해줘"라고 지시받아야 PR도 생성한다. **commit은 예외로, 사용자의 별도 지시 없이 AI가 자율적으로 실행할 수 있다** (D-002).
- PR을 생성하기 전에는 `pr-template-work.md` 또는 `pr-template-issue.md`의 확인 사항 체크리스트가 실제로 충족됐는지 확인한다. 충족되지 않은 항목이 있으면 PR을 만들지 않고, 무엇이 비어있는지 사용자에게 보고한다.
- 커밋 메시지와 PR 본문은 해당 템플릿(`commit-convention.md`, `pr-template-work.md`/`pr-template-issue.md`) 형식을 그대로 따른다 (D-003).

## 4. 리뷰

중요한 변경은 구현에 쓴 세션과는 별도로 — 가능하면 새 대화(새 컨텍스트)에서 — 리뷰를 받는 걸 권장한다. 같은 세션이 쓴 코드를 같은 세션이 검토하면 자기 판단을 그대로 정당화하기 쉽기 때문이다. 모든 변경에 강제하지는 않고, Protected Change나 규모가 큰 작업에 우선 적용한다.

리뷰 시 문제의 심각도:

| 등급       | 의미                                                                     |
| ---------- | ------------------------------------------------------------------------ |
| BLOCKER    | 지금 상태로 두면 안 되는 심각한 문제 (보안, 데이터 손상, 빌드/실행 불가) |
| MAJOR      | 기능적으로 의미 있는 결함, 요구사항 미충족                               |
| MINOR      | 당장 기능엔 문제없지만 실제로 고칠 필요가 있는 구체적 약점               |
| SUGGESTION | 선택적 개선 제안. 지금 막을 필요는 없음                                  |

리뷰 결과: **PASS**(BLOCKER·MAJOR 없음) / **CHANGES REQUIRED**(BLOCKER나 MAJOR가 남아있음) / **BLOCKED**(리뷰 자체를 완료할 근거가 부족함).

## 5. CI (나중에 CI를 붙일 때 기본값)

지금 당장 적용할 건 아니고, CI를 설정하는 시점에 기본값으로 참고한다.

- Format: Required
- Lint: 있으면 Required
- Type Check: 해당하면 Required (예: TypeScript)
- Build: 해당하면 Required
- Unit Test: 있으면 Required
- Integration Test: 있으면 Required

존재하지 않는 도구·테스트를 이 기준을 맞추기 위해 새로 추가하지 않는다 — 필요하면 별도로 논의한다 (Dependency 추가는 Protected Change).

## 6. 문서 수정 원칙

`project-spec.md`, `workflow.md`, `decisions.md`를 수정할 때 기존 설명·이유·예외를 근거 없이 지우지 않는다. 의미가 바뀌는 수정인지 애매하면 의미가 바뀌는 것으로 보고 먼저 확인받는다. 오타 수정, 링크 정리처럼 의미를 바꾸지 않는 수정은 바로 반영해도 된다.

## 7. 문서 동기화

claude.ai 프로젝트 문서(`decisions.md`, `feature.md`, `project-spec.md`, `phase.md` 등)가 원본이고, 레포의 `docs/` 사본은 그걸 코드와 함께 버전 관리하기 위한 미러다 (`project-spec.md` "레포 구조" 참고). 원본을 수정했으면 별도 지시를 기다리지 않고, 같은 턴 안에서 레포 쪽 사본도 반드시 함께 동기화한다 — 지시가 없었다는 이유로 미러링을 미루지 않는다.

두 사본이 어긋난 채로 방치되면 어느 쪽이 최신인지 혼란이 생기므로, Work Unit을 마무리하고 PR을 만들 때도 두 문서가 실제로 일치하는지 한 번 더 확인한다 (`pr-template-work.md` 확인 사항 체크리스트 참고).
