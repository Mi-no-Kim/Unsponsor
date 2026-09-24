# Work PR 템플릿 (`work/*` → `issue/*`)

> 머지 방식: **squash merge** (`workflow.md` "브랜치·머지 전략")

## 관련 Issue / Work Unit

<!-- 제목 번호와 GitHub 번호를 함께 적는다. Work Unit은 `Closes #N`으로 적어야 머지 후 Actions가 자동으로 닫는다 (D-034). 예: [I-003-W-012] Closes #45 (부모 Issue: [I-003] #40) -->
<!-- 선행 Work Unit이 있으면 함께 적는다. 예: 선행 — [I-003-W-011] #44 -->

## 변경 내용

<!-- 무엇을 왜 바꿨는지 -->

## 확인 사항

- [ ] Work Unit의 Done When 항목 충족 확인
- [ ] Protected Change(DB 스키마·API 계약·Dependency 등)가 포함되어 있다면 사전에 승인받았다 (`rules.md` 참고)
- [ ] 관련 CI 통과
- [ ] claude.ai 프로젝트 문서를 수정했다면, 레포의 `docs/` 사본과 내용이 일치하는지 다시 확인했다 (`rules.md` 7번)

## 테스트

<!-- 어떻게 검증했는지 -->
