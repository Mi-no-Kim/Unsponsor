# 커밋 메시지 규칙

`rules.md`의 Conventional Commits 규칙(`type: description`)을 실제로 쓸 때 참고하는 세부 문서다.

## 제목 형식

```
type(scope): description
```

- `description`은 한글로 쓰고, 명령형보다 "~함/~추가/~수정" 같은 간결한 서술형을 쓴다.
- 한 커밋에 여러 변경이 섞여 있으면 안 된다 — type 하나로 요약이 안 되면 커밋을 나눈다.

## 타입

| type       | 의미                                  |
| ---------- | ------------------------------------- |
| `feat`     | 새 기능 추가                          |
| `fix`      | 버그 수정                             |
| `test`     | 테스트 추가·수정                      |
| `refactor` | 동작 변화 없는 구조 개선              |
| `docs`     | 문서만 변경 (`docs/`, `README.md` 등) |
| `chore`    | 빌드 설정, 의존성, 기타 잡무성 변경   |

## Scope (선택)

모노레포라 어느 컴포넌트를 바꿨는지 제목만 보고 알 수 있으면 로그를 훑기 편하다. `backend`/`frontend`/`pipeline` — 레포 폴더 이름을 그대로 쓰고 줄이지 않는다. 줄임말을 따로 정하는 비용(예: pipeline을 뭐라고 줄일지)보다 폴더명 그대로 쓰는 쪽이 더 명확하고 기억하기 쉽다.

- 컴포넌트 하나만 바꾼 커밋: `feat(backend): ...`, `fix(pipeline): ...`, `chore(frontend): ...`
- 여러 컴포넌트에 걸친 변경: 가능하면 컴포넌트별로 커밋을 나눈다. 정말 하나로 묶어야 하는 경우(예: 여러 컴포넌트에 걸친 설정 변경)에는 scope를 생략한다.
- 특정 컴포넌트에 속하지 않는 변경(`docs/`, 루트 설정 파일 등)도 scope를 생략한다.

## 본문 (필요할 때)

제목만으로 "왜 바꿨는지"가 설명이 안 되면 본문을 추가한다. "무엇을 바꿨는지"는 diff로 보이니 본문은 "왜"에 집중한다. 사소한 변경(단순 오타, 설정값 하나)은 본문 없이 제목만으로 충분하다.

본문 아래에 관련 Issue/Work Unit을 참조하는 footer를 남긴다:

```
Refs: I-023
Refs: I-023, W-068
```

## 예시

```
feat(backend): 채널 수동 등록 API 추가

Refs: I-012, W-034
```

```
fix(pipeline): SponsorBlock 세그먼트가 없을 때 빈 배열 처리 안 되던 문제 수정

빈 세그먼트를 null로 반환하는 케이스를 놓쳐서 자막 필터링 단계에서 예외가 났음.

Refs: I-018, W-051
```

```
docs: decisions.md에 D-004 추가
```

```
chore(frontend): eslint 설정 추가
```

## 관련 문서

- 커밋 단위·실행 권한: `rules.md`
- PR 본문 형식: `pr-template-work.md`, `pr-template-issue.md`
