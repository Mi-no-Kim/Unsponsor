# Issue PR 템플릿 (`issue/*` → `main`)

> 머지 방식: **일반 merge** — squash 아님 (`workflow.md` "브랜치·머지 전략")

## 관련 Issue

<!-- 제목 번호와 GitHub 번호를 함께 적는다. `Closes #N`으로 적으면 main에 머지될 때 GitHub가 Issue를 자동으로 닫는다 (D-034). 예: [I-003] Closes #40 -->

## 변경 내용

<!-- 이 Issue에서 무엇을 완성했는지 (포함된 Work Unit 요약) -->

## 확인 사항

- [ ] Protected Change(DB 스키마·API 계약·Dependency 등)가 포함되어 있다면 사전에 승인받았다 (`rules.md` 참고)
- [ ] 관련 CI 통과
- [ ] Done When 항목 충족 확인
- [ ] Issue의 "검증 방법"대로 확인했고, 결과를 아래 "테스트"에 남겼다
- [ ] 진행 중 Scope·Done When이 바뀌었다면 Issue 본문과 코멘트에 반영됐다 (`workflow.md` "진행 중인 Issue 수정")
- [ ] 포함된 Work Unit이 모두 merge / 명시적 종료·취소 / Scope에서 제거 중 하나로 처리됨

## 테스트

<!-- Issue의 "검증 방법"에 적은 방법으로 어떻게 확인했는지와 그 결과 -->
