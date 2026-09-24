# 리뷰 체크리스트

`rules.md` 4번(리뷰)의 심각도·결과 기준을 실제로 적용할 때 쓰는 체크리스트다. 새 세션(새 컨텍스트)에서 리뷰할 때 이 순서로 확인한다.

## 시작 전

- [ ] 리뷰 대상이 어떤 Issue/Work Unit인지 확인했다 (관련 Phase, Decision 포함)
- [ ] 구현에 쓴 세션과 다른 세션(새 대화)에서 진행한다 — Protected Change나 규모가 큰 작업일수록 필수

## 체크리스트

- [ ] Done When(Issue) 또는 Goal(Work Unit) 항목이 실제로 충족됐다
- [ ] Protected Change(DB 스키마, API 계약, Dependency 등)가 포함돼 있다면 사전 승인받은 내용과 일치한다
- [ ] 기록 대상 기준(2-of-3)에 해당하는 판단이 `decisions.md`에 빠짐없이 기록됐다
- [ ] 테스트가 있다면 통과했고, 없다면 왜 없어도 되는지 설명 가능하다
- [ ] 커밋 메시지가 `commit-convention.md` 형식을 따른다
- [ ] 변경 범위가 관련 Issue/Work Unit의 Scope를 벗어나지 않는다 (벗어났다면 별도로 논의됐는지 확인)

## 결과 보고 형식

```
결과: PASS / CHANGES REQUIRED / BLOCKED

- [BLOCKER] ...
- [MAJOR] ...
- [MINOR] ...
- [SUGGESTION] ...
```

심각도·결과 정의는 `rules.md` 4번 참고.
