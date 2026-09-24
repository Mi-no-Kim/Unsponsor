---
name: 버그 리포트
about: main에 merge된 기능의 버그를 기록한다 — 작업 중인 Issue 범위 안의 버그는 그 Issue의 Work Unit으로 처리한다 (workflow.md "버그 처리")
title: "[I-###] "
labels: bug
---

## 증상

<!-- 무엇이 잘못됐는지 -->
<!-- 예: 협찬 리뷰 판별 로직에서 설명란에 "협찬" 키워드가 있어도 is_sponsored_review가 항상 false로 저장됨 -->

## 재현 방법

1.
2.
3.

<!-- 예:
1. 설명란에 "이 영상은 ○○의 협찬을 받아 제작되었습니다"가 포함된 영상을 파이프라인에 넣는다
2. summarizer 배치를 실행한다
3. DB에서 해당 영상의 is_sponsored_review 값을 확인한다
-->

## 기대 동작

<!-- 원래 어떻게 동작해야 하는지 -->
<!-- 예: is_sponsored_review = true로 저장되어야 함 -->

## 실제 동작

<!-- 지금 실제로 어떻게 동작하는지 -->
<!-- 예: is_sponsored_review = false로 저장됨 -->

## 관련 Phase / Decision

<!-- 관련 있으면 PH-###, D-### 참조. 없으면 생략 -->

## 추가 정보

<!-- 로그, 스크린샷, 환경 등 -->
