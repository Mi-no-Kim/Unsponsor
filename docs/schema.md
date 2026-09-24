# DB 스키마

> 초안 단계 문서. 논의하면서 테이블을 하나씩 채워나간다. 각 테이블 설계의 근거는 `decisions.md`를 참고 — `project-spec.md` "기능" ↔ `feature.md` 관계와 같은 패턴으로, 이 문서는 "무엇"을, `decisions.md`는 "왜"를 담당한다.

## 공통 규칙

- 문자셋: utf8mb4 (다국어 제목·설명·이모지 대응)
- PK: 각 테이블 `id` BIGINT AUTO_INCREMENT. 외부 시스템 ID(YouTube 등)는 별도 UNIQUE 컬럼으로 보관.
- `created_at`/`updated_at`은 테이블 성격에 따라 둔다: 행이 수정되는 테이블은 둘 다, 추가만 되고 수정되지 않는 테이블(로그·증거 등)은 `created_at`만, 정적 룩업 테이블(`languages`)과 N:M 연결 테이블(`point_aspects`)은 두지 않는다.
- 고정된 값 집합이 필요한 컬럼은 그 컬럼에 쓰는 주체에 따라 정한다 (D-035). Spring만 쓰면 VARCHAR + Java enum으로 앱에서 강제한다. Python 파이프라인도 쓰면 DB가 값을 강제한다 — 값 목록이 고정이거나 드물게만 늘면 네이티브 ENUM(D-019), 계속 늘어날 수 있으면 룩업 테이블 + FK(예: language, D-021).
- 실제 DDL은 backend의 Flyway 마이그레이션(`backend/src/main/resources/db/migration/`)으로 적용한다. 파이프라인은 테이블을 만들지 않고 쓰기만 한다. 이 문서는 테이블 설계와 의미를 설명하는 문서라, 스키마를 바꿀 때는 이 문서와 마이그레이션을 함께 갱신한다 (D-031).
- 사이트에 노출되는 텍스트 컬럼은 `_ko`/`_en` 컬럼 쌍으로 둔다. 브랜드·시리즈·제품명 같은 고유명사는 대상이 아니다 (D-028).
- UNIQUE 키에 NULL 허용 컬럼이 포함되면 MySQL은 NULL끼리를 서로 다른 값으로 취급해 DB 제약만으로는 중복을 막지 못한다. 이런 테이블은 행을 추가하는 쪽이 삽입 전에 NULL-safe 비교(`<=>`)로 기존 행을 먼저 조회해, 있으면 기존 행을 쓰고 없을 때만 삽입한다. 해당 UNIQUE 항목에 (D-029)로 표시했다.

## 수집 원본

### languages

| 컬럼 | 타입           | 설명                             |
| ---- | -------------- | -------------------------------- |
| code | VARCHAR(10) PK | ISO 639-1 (예: `ko`, `en`, `ja`) |
| name | VARCHAR(50)    | 표시용                           |

### channels

| 컬럼                | 타입                            | 설명                                                              |
| ------------------- | ------------------------------- | ----------------------------------------------------------------- |
| id                  | BIGINT PK                       |                                                                   |
| youtube_channel_id  | VARCHAR(24) UNIQUE              | YouTube 채널 ID                                                   |
| uploads_playlist_id | VARCHAR(34)                     | `relatedPlaylists.uploads` (D-012)                                |
| name                | VARCHAR(255)                    |                                                                   |
| language_code       | VARCHAR(10) FK → languages.code | 채널 주 언어. `ko`면 국내로 간주해 SponsorBlock 스킵 (D-016 대체) |
| subscriber_count    | BIGINT NULL                     | 수집 시점 스냅샷                                                  |
| created_at          | DATETIME                        |                                                                   |
| updated_at          | DATETIME                        |                                                                   |

### videos

| 컬럼                       | 타입                            | 설명                                                                                                                                                             |
| -------------------------- | ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| id                         | BIGINT PK                       |                                                                                                                                                                  |
| youtube_video_id           | VARCHAR(11) UNIQUE              |                                                                                                                                                                  |
| channel_id                 | BIGINT FK → channels.id         |                                                                                                                                                                  |
| title                      | VARCHAR(500)                    |                                                                                                                                                                  |
| description                | TEXT NULL                       | 협찬 판별용 설명란 원문 (D-016)                                                                                                                                  |
| published_at               | DATETIME                        |                                                                                                                                                                  |
| language_code              | VARCHAR(10) FK → languages.code | 필터링/정렬용, 기본은 채널 값 상속                                                                                                                               |
| duration_seconds           | INT NULL                        | 필터링/정렬용                                                                                                                                                    |
| view_count                 | BIGINT NULL                     | 수집 시점 스냅샷                                                                                                                                                 |
| has_paid_product_placement | BOOLEAN NULL                    | `paidProductPlacementDetails`(유료 광고 포함 표시). 조회 실패면 NULL. 삽입형 광고만 있어도 켜지므로 협찬 리뷰 판별의 근거일 뿐 판별 결과가 아니다 (D-016, D-036) |
| created_at                 | DATETIME                        |                                                                                                                                                                  |
| updated_at                 | DATETIME                        |                                                                                                                                                                  |

## 제품

브랜드 → 시리즈 → 제품의 3단 구조로 정규화한다. 브랜드·시리즈는 각각 self-reference(`parent_brand_id`/`parent_series_id`)로 드문 하위 분기(서브 브랜드, 서브 시리즈)를 커버하고, 제품은 그 아래 완전히 독립된 행이다 — 예를 들어 "아이폰 17"과 "아이폰 17 Pro"는 부모-자식이 아니라 같은 시리즈 아래의 별개 제품이다. 용량·색상 같은 구매 옵션은 트리 노드로 만들지 않고 제품 행의 `options` JSON에 둔다 (D-020 v2).

### categories

| 컬럼       | 타입               | 설명                                   |
| ---------- | ------------------ | -------------------------------------- |
| id         | BIGINT PK          |                                        |
| name_ko    | VARCHAR(100)       | 표시용 한국어 (예: "스마트폰") (D-028) |
| name_en    | VARCHAR(100)       | 표시용 영어 (예: "Smartphone") (D-028) |
| slug       | VARCHAR(50) UNIQUE | (예: `smartphone`)                     |
| created_at | DATETIME           |                                        |
| updated_at | DATETIME           |                                        |

카테고리 전용 스펙 테이블(`phone_specs` 등)은 지금 함께 설계하지 않는다 — `reviews.category_tags`와 같은 이유로, 각 카테고리 착수 시점에 별도 설계한다 (project-spec.md 선례, D-023).

### brands

| 컬럼            | 타입                       | 설명                                                         |
| --------------- | -------------------------- | ------------------------------------------------------------ |
| id              | BIGINT PK                  |                                                              |
| parent_brand_id | BIGINT NULL FK → brands.id | 하위/서브 브랜드가 필요한 드문 경우만 사용, 대부분 NULL      |
| name            | VARCHAR(255)               | 표시용 (예: "Apple")                                         |
| marker          | VARCHAR(100)               | 정규화된 캐노니컬 마커 (예: `apple`), 원문 언어 무관 (D-020) |
| created_at      | DATETIME                   |                                                              |
| updated_at      | DATETIME                   |                                                              |

- UNIQUE(parent_brand_id, marker) — 같은 부모 아래 형제 브랜드 마커 중복 방지. `parent_brand_id`가 NULL인 최상위 브랜드끼리는 DB가 막지 못해 삽입 전 조회로 차단한다 (D-029)

### series

| 컬럼             | 타입                           | 설명                                                                                    |
| ---------------- | ------------------------------ | --------------------------------------------------------------------------------------- |
| id               | BIGINT PK                      |                                                                                         |
| brand_id         | BIGINT FK → brands.id          |                                                                                         |
| parent_series_id | BIGINT NULL FK → series.id     | 하위 시리즈가 필요한 경우만 사용 (예: Galaxy → Galaxy Z), 대부분 NULL                   |
| category_id      | BIGINT NULL FK → categories.id | 카테고리는 보통 이 레벨에서 확정 (브랜드는 여러 카테고리에 걸치지만 시리즈는 보통 하나) |
| name             | VARCHAR(255)                   | 표시용 (예: "iPhone")                                                                   |
| marker           | VARCHAR(100)                   | 정규화된 마커 (예: `iphone`)                                                            |
| full_name        | VARCHAR(500)                   | 브랜드+시리즈 체인을 이어붙인 표시용 이름 (편의)                                        |
| created_at       | DATETIME                       |                                                                                         |
| updated_at       | DATETIME                       |                                                                                         |

- UNIQUE(brand_id, parent_series_id, marker) — 같은 부모 아래 형제 시리즈 마커 중복 방지. `parent_series_id`가 NULL인 시리즈끼리는 DB가 막지 못해 삽입 전 조회로 차단한다 (D-029)

### products

| 컬럼        | 타입                           | 설명                                                                                                               |
| ----------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| id          | BIGINT PK                      |                                                                                                                    |
| series_id   | BIGINT FK → series.id          |                                                                                                                    |
| category_id | BIGINT NULL FK → categories.id | `series.category_id`를 복사 — 조인 없이 바로 필터링 (series와 값 불일치 없도록 갱신 시 함께 반영)                  |
| name        | VARCHAR(255)                   | 표시용 제품 구분명 (예: "17", "17 Pro")                                                                            |
| marker      | VARCHAR(100)                   | 정규화된 마커 (예: `17-pro`)                                                                                       |
| full_name   | VARCHAR(500)                   | 브랜드+시리즈+제품명을 전부 이어붙인 표시용 이름 (예: "iPhone 17 Pro")                                             |
| marker_key  | VARCHAR(500) UNIQUE            | 브랜드+시리즈 체인+제품 마커를 이어붙인 매칭 키. 파이프라인이 신규 리뷰를 처리할 때 이 컬럼 하나로 조회 (D-020 v2) |
| options     | JSON NULL                      | 용량·색상 등 구매 옵션                                                                                             |
| created_at  | DATETIME                       |                                                                                                                    |
| updated_at  | DATETIME                       |                                                                                                                    |

- UNIQUE(series_id, marker) — 같은 시리즈 아래 형제 제품 마커 중복 방지

## 리뷰 데이터

### video_transcripts

| 컬럼       | 타입                        | 설명                                                                      |
| ---------- | --------------------------- | ------------------------------------------------------------------------- |
| video_id   | BIGINT PK, FK → videos.id   | 영상 1개당 자막 1건 (1:1)                                                 |
| raw_text   | LONGTEXT                    | 자막 원문(STT 결과 포함). 화면에 노출하지 않음 (D-018)                    |
| source     | ENUM('library','bs4','stt') | 확보 경로 (D-015). Selenium 폴백이 추가되면 ALTER로 값을 추가한다 (D-035) |
| created_at | DATETIME                    |                                                                           |
| updated_at | DATETIME                    |                                                                           |

### ad_segments

| 컬럼          | 타입                       | 설명                                                       |
| ------------- | -------------------------- | ---------------------------------------------------------- |
| id            | BIGINT PK                  |                                                            |
| video_id      | BIGINT FK → videos.id      |                                                            |
| start_seconds | INT                        |                                                            |
| end_seconds   | INT                        |                                                            |
| source        | ENUM('llm','sponsorblock') | 탐지 수단. `llm`은 LLM ① 호출이 탐지한 구간 (D-016, D-035) |
| created_at    | DATETIME                   |                                                            |

### reviews

| 컬럼                | 타입                                         | 설명                                                                                                                                                                                                                                                                               |
| ------------------- | -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| id                  | BIGINT PK                                    |                                                                                                                                                                                                                                                                                    |
| video_id            | BIGINT FK → videos.id                        |                                                                                                                                                                                                                                                                                    |
| product_id          | BIGINT NULL FK → products.id                 | 매칭된 제품. 자동 매칭에 실패해 사람 승인을 기다리는 동안은 NULL (D-030)                                                                                                                                                                                                           |
| candidate_id        | BIGINT NULL FK → product_match_candidates.id | 승인 큐로 간 경우의 후보. 승인 후 `product_id`가 채워져도 이력으로 남긴다 (D-030)                                                                                                                                                                                                  |
| is_sponsored_review | BOOLEAN                                      | 이 제품 자체가 협찬받은 리뷰인지. LLM ① 호출이 설명란·유료 광고 표시·자막을 근거로 판별 (D-016, D-036, F-003)                                                                                                                                                                      |
| role                | ENUM('primary','secondary')                  | `primary`(그 영상의 주 리뷰 대상) / `secondary`(비교·언급된 부 대상) (D-022, D-035)                                                                                                                                                                                                |
| pros_ko             | TEXT NULL                                    | LLM이 이 리뷰에서 추출한 장점 요약 텍스트 (pros = 장점). `review_points`가 이 텍스트를 개별 관찰 단위로 분해한 것 (D-028)                                                                                                                                                          |
| pros_en             | TEXT NULL                                    | `pros_ko`의 영어 버전 (D-028)                                                                                                                                                                                                                                                      |
| cons_ko             | TEXT NULL                                    | LLM이 이 리뷰에서 추출한 단점 요약 텍스트 (cons = 단점). 분해 방식은 `pros_ko`와 동일 (D-028)                                                                                                                                                                                      |
| cons_en             | TEXT NULL                                    | `cons_ko`의 영어 버전 (D-028)                                                                                                                                                                                                                                                      |
| category_tags       | JSON NULL                                    | LLM이 리뷰별로 추출한 자유 형식 태그 배열(예: `["배터리 지속시간 우수", "발열 있음"]`). 카테고리마다 고정된 태그 스키마를 미리 정의하지 않는다 — 객관적 스펙(`phone_specs` 등)과 달리 주관적·자유 형식이라 카테고리별 매핑이 필요 없다 (D-023). 자연어 검색(F-007)의 입력으로 쓰임 |
| created_at          | DATETIME                                     |                                                                                                                                                                                                                                                                                    |
| updated_at          | DATETIME                                     |                                                                                                                                                                                                                                                                                    |

- UNIQUE(video_id, product_id) — 같은 영상·제품 조합은 리뷰 1건만 존재.
- UNIQUE(video_id, candidate_id) — 매칭 대기 중인 리뷰도 같은 영상·후보 조합은 1건만 존재 (D-030).
- 행은 LLM ① 호출과 매칭 단계에서 만들어지고(영상·제품 또는 후보·역할·협찬 여부), 장단점(`pros_*`/`cons_*`)은 LLM ② 호출이 채운다. 그 전까지는 NULL이다 (D-016).
- `product_id`와 `candidate_id` 중 적어도 하나는 채워져 있어야 한다. 후보가 승인되거나 거절 후 기존 제품으로 재연결되면(`resolved_product_id`), 그 후보를 가리키는 리뷰의 `product_id`를 `resolved_product_id`로 채운다. `product_id`가 NULL인 리뷰는 어떤 집계에도 들어가지 않는다 (D-030).
- 한 영상에 `role='primary'`인 행이 정확히 1개여야 한다는 제약은 두지 않는다 — A vs B 정면 비교 영상처럼 두 제품 모두가 주 리뷰 대상인 경우가 있을 수 있다 (D-022).
- `role='primary'` 리뷰만 제품 종합 평가(F-001, D-017)에 집계된다. `role='secondary'` 리뷰는 그 집계에서 제외되고, 대신 "비교 대상 제품 안내"(F-017)의 데이터 소스로 쓰인다.

## 집계

원본 리뷰(`reviews`)는 하나만 저장하고(D-017), 화면에 보여줄 요약과 비교·커버리지에 쓸 구조화 데이터는 별도 집계 테이블에 미리 계산해 저장한다. 리뷰가 추가·변경될 때 배치가 재계산한다.

### product_aggregates

제품 페이지에 보여줄 문장 요약(F-001). 협찬 포함/제외 두 버전을 각각 한 행으로 둔다.

| 컬럼                    | 타입                    | 설명                                      |
| ----------------------- | ----------------------- | ----------------------------------------- |
| id                      | BIGINT PK               |                                           |
| product_id              | BIGINT FK → products.id |                                           |
| excludes_sponsored      | BOOLEAN                 | true = 협찬 리뷰 제외 버전 (D-017, F-004) |
| pros_summary_ko         | TEXT                    | LLM이 종합한 장점 요약 문장 (D-028)       |
| pros_summary_en         | TEXT                    | 영어 버전 (D-028)                         |
| cons_summary_ko         | TEXT                    | 단점 요약 문장 (D-028)                    |
| cons_summary_en         | TEXT                    | 영어 버전 (D-028)                         |
| review_count            | INT                     | 이 집계에 포함된 리뷰 수(참고용)          |
| last_computed_at        | DATETIME                |                                           |
| created_at / updated_at | DATETIME                |                                           |

- UNIQUE(product_id, excludes_sponsored)
- 집계 대상은 `reviews.role='primary'`인 리뷰만(D-022). `excludes_sponsored=true`는 거기서 협찬 리뷰(`reviews.is_sponsored_review=true`)가 하나라도 있는 영상의 리뷰를 모두 뺀다 (D-036). 매칭 대기 중인 리뷰(`product_id` NULL)는 대상이 아니다 (D-030).

### 속성(aspect) 기반 구조화 데이터

제품별 종합 평가(F-001)조차 문장 요약만으로는 부족하다 — "배터리가 좋다/아쉽다" 같은 리뷰의 개별 관찰을 정규화된 속성 단위로 쌓아야 실제로 여러 리뷰가 하나로 "쌓인다" (D-024). 제품 간 비교(F-002)와 데이터 커버리지 고도화(F-010, PH-2)는 새 데이터 소스를 만들지 않고 F-001부터 쌓인 이 구조를 그대로 재사용한다. `product_aggregates`의 문장 요약과는 별개로 이 구조를 둔다.

#### aspects

| 컬럼                    | 타입                           | 설명                                                                                   |
| ----------------------- | ------------------------------ | -------------------------------------------------------------------------------------- |
| id                      | BIGINT PK                      |                                                                                        |
| parent_aspect_id        | BIGINT NULL FK → aspects.id    | 세부 속성을 상위 속성 아래 둘 때만 사용(예: "충전속도"의 부모는 "배터리"), 대부분 NULL |
| category_id             | BIGINT NULL FK → categories.id | 카테고리 종속 속성이면 지정, 공통이면 NULL                                             |
| name_ko                 | VARCHAR(100)                   | 캐노니컬 명칭 한국어 (예: "배터리") (D-028)                                            |
| name_en                 | VARCHAR(100)                   | 캐노니컬 명칭 영어 (예: "Battery") (D-028)                                             |
| marker                  | VARCHAR(100)                   | 정규화된 마커 — 원문 언어·표현 차이 무관 매칭 (D-020과 동일 패턴)                      |
| description             | TEXT NULL                      | 이 속성이 구체적으로 뭘 가리키는지                                                     |
| created_at / updated_at | DATETIME                       |                                                                                        |

- UNIQUE(category_id, marker) — 같은 카테고리(공통 속성이면 NULL) 안에서 마커 중복 방지. `brands`가 `parent_brand_id`로 묶는 것과 같은 패턴이되, 기준이 상위 노드가 아니라 카테고리다. `category_id`가 NULL인 공통 속성끼리는 DB가 막지 못해 삽입 전 조회로 차단한다 (D-029).

#### review_points

리뷰 하나에서 뽑아낸 개별 관찰 단위.

| 컬럼       | 타입                   | 설명                                                                                                                                |
| ---------- | ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| id         | BIGINT PK              |                                                                                                                                     |
| review_id  | BIGINT FK → reviews.id |                                                                                                                                     |
| polarity   | ENUM('pro','con')      | 이 관찰이 장점/단점 중 어느 쪽인지                                                                                                  |
| quote_ko   | TEXT NULL              | `video_transcripts`(자막 원문)에서 추출해 다듬은 인용문. 제품 상세 조회 화면에서 이 관찰의 근거로 원본 영상과 함께 보여준다 (D-028) |
| quote_en   | TEXT NULL              | `quote_ko`의 영어 버전 (D-028)                                                                                                      |
| created_at | DATETIME               |                                                                                                                                     |

#### point_aspects

`review_points` ↔ `aspects` N:M. 한 관찰이 여러 속성에 걸치는 경우(예: "발열이 배터리 때문"이면 배터리·발열 둘 다에 태깅)를 표현한다.

| 컬럼      | 타입                         | 설명 |
| --------- | ---------------------------- | ---- |
| point_id  | BIGINT FK → review_points.id |      |
| aspect_id | BIGINT FK → aspects.id       |      |

- PRIMARY KEY(point_id, aspect_id)

#### product_aspect_stats

| 컬럼               | 타입                    | 설명                             |
| ------------------ | ----------------------- | -------------------------------- |
| id                 | BIGINT PK               |                                  |
| product_id         | BIGINT FK → products.id |                                  |
| aspect_id          | BIGINT FK → aspects.id  |                                  |
| excludes_sponsored | BOOLEAN                 | `product_aggregates`와 동일 기준 |
| positive_count     | INT                     | 이 속성에 대한 `pro` 관찰 수     |
| negative_count     | INT                     | `con` 관찰 수                    |
| last_computed_at   | DATETIME                |                                  |

- UNIQUE(product_id, aspect_id, excludes_sponsored)
- 자식 속성(예: 충전속도)에 대한 관찰이 집계될 때 부모 속성(배터리) 카운트에도 함께 반영한다(롤업, `products.category_id` 복제와 같은 "조회 편의를 위한 중복" 패턴).
- `product_aggregates`와 동일하게, `reviews.role='primary'`인 리뷰에 달린 `review_points`만 집계 대상이다(D-022). `excludes_sponsored=true`는 거기서 협찬 리뷰가 하나라도 있는 영상의 리뷰를 모두 뺀다(D-017, D-036).

## 처리 큐

### video_processing_queue

백필 전용이 아니라, D-014의 "신규 영상 확인" 상시 운영 모드에서도 같은 테이블·워커를 계속 쓴다.

| 컬럼            | 타입                                                           | 설명                                                                                                                                  |
| --------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| id              | BIGINT PK                                                      |                                                                                                                                       |
| video_id        | BIGINT UNIQUE FK → videos.id                                   | 영상 1개당 큐 항목 1개 — 중복 등록 방지                                                                                               |
| stage           | ENUM('transcript','identify','summarize') DEFAULT 'transcript' | 지금 처리할 단계. `transcript`=자막/STT, `identify`=LLM ① 호출 + 매칭, `summarize`=LLM ② 호출 (D-016, D-019)                          |
| status          | ENUM('pending','processing','done','failed')                   | 지금 단계(`stage`)의 상태. 값 목록이 고정돼 늘어날 일이 없어 네이티브 ENUM 사용 — Python/Spring 어느 쪽이 쓰든 DB가 값을 강제 (D-019) |
| attempt_count   | INT DEFAULT 0                                                  | 지금까지 시도 횟수                                                                                                                    |
| next_attempt_at | DATETIME NULL                                                  | 이 시각 이후에만 재시도 가능(백오프). NULL이면 즉시 가능                                                                              |
| started_at      | DATETIME NULL                                                  | `processing`으로 바뀐 시각 — 멈춘 행 판단 기준                                                                                        |
| last_error      | TEXT NULL                                                      | 마지막 실패 사유                                                                                                                      |
| created_at      | DATETIME                                                       |                                                                                                                                       |
| updated_at      | DATETIME                                                       |                                                                                                                                       |

- 워커는 `SELECT ... WHERE status='pending' AND (next_attempt_at IS NULL OR next_attempt_at <= NOW()) ... FOR UPDATE SKIP LOCKED`로 집어가며, 짧은 트랜잭션 안에서 바로 `status='processing', started_at=NOW()`로 갱신 후 커밋한다. 실제 처리(자막 수집, LLM 호출 등)는 트랜잭션 밖에서 수행한다.
- 단계가 성공하면 다음 `stage`로 넘어가면서 `status='pending'`, `attempt_count=0`, `next_attempt_at=NULL`로 되돌린다. 마지막 단계(`summarize`)가 성공하면 `status='done'`이 된다 (D-019 v2).
- 멈춘 행 복구: 별도 배치가 주기적으로 `status='processing' AND started_at < NOW() - INTERVAL 30분`인 행을 찾아 실패 처리와 동일한 재시도/소진 로직으로 되돌린다.

## 승인 큐

D-020(제품 매칭)과 D-024(속성 등록)이 전제하는 "마커가 애매하면 사람이 검토" 워크플로를 위한 테이블. 후보(정규화된 마커 조합 1개)와 증거(그 후보가 등장한 영상·관찰)를 분리해, 같은 미등록 후보가 여러 곳에서 반복 추천돼도 승인은 한 번만 하면 되게 한다(D-025). 로그 형식이라 승인/거절돼도 행을 지우지 않고 `status`만 바꾼다(`video_processing_queue`와 동일 패턴).

### product_match_candidates

| 컬럼                     | 타입                                                             | 설명                                                                                                                                                                                                                                            |
| ------------------------ | ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| id                       | BIGINT PK                                                        |                                                                                                                                                                                                                                                 |
| brand_marker_candidate   | VARCHAR(100)                                                     | LLM이 뽑은 정규화 브랜드 마커 후보                                                                                                                                                                                                              |
| series_marker_candidate  | VARCHAR(100) NULL                                                |                                                                                                                                                                                                                                                 |
| product_marker_candidate | VARCHAR(100)                                                     |                                                                                                                                                                                                                                                 |
| status                   | ENUM('pending','approved','rejected')                            |                                                                                                                                                                                                                                                 |
| resolved_product_id      | BIGINT NULL FK → products.id                                     | 이 후보가 최종적으로 연결된 제품. 승인이든, 거절 후 사람이 기존 제품으로 수동 재연결한 경우든 상관없이 채워질 수 있다                                                                                                                           |
| reject_reason            | ENUM('not_a_product','wrong_marker','out_of_scope','other') NULL | `rejected`일 때만 채움. `not_a_product`=실존하지 않는 제품 추출(환각/오인식), `wrong_marker`=실제 제품이지만 마커 추출이 틀려 자동 매칭 실패(보통 `resolved_product_id`도 함께 채워짐), `out_of_scope`=카테고리 밖·리뷰 대상 아님, `other`=기타 |
| reviewed_at              | DATETIME NULL                                                    |                                                                                                                                                                                                                                                 |
| created_at               | DATETIME                                                         |                                                                                                                                                                                                                                                 |
| updated_at               | DATETIME                                                         |                                                                                                                                                                                                                                                 |

- UNIQUE(brand_marker_candidate, series_marker_candidate, product_marker_candidate) — 파이프라인은 이 키로 upsert한다: 이미 있으면 증거만 추가하고, 없으면 새 `pending` 행을 만든다. `series_marker_candidate`가 NULL인 후보끼리는 DB가 막지 못해, upsert 전에 NULL-safe 비교로 기존 후보를 먼저 조회한다 (D-029).

### product_match_evidence

후보 하나에 걸린 증거(영상) 1:N.

| 컬럼         | 타입                                    | 설명                                                 |
| ------------ | --------------------------------------- | ---------------------------------------------------- |
| id           | BIGINT PK                               |                                                      |
| candidate_id | BIGINT FK → product_match_candidates.id |                                                      |
| video_id     | BIGINT FK → videos.id                   | 이 후보가 등장한 영상                                |
| raw_name     | VARCHAR(500)                            | 그 영상 원문에 등장한 그대로의 제품명(검토자 참고용) |
| created_at   | DATETIME                                |                                                      |

- UNIQUE(candidate_id, video_id) — 같은 영상이 같은 후보에 중복으로 쌓이지 않도록

### aspect_candidates

| 컬럼               | 타입                                                                 | 설명                                                                                                                                 |
| ------------------ | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| id                 | BIGINT PK                                                            |                                                                                                                                      |
| category_id        | BIGINT NULL FK → categories.id                                       | `aspects.category_id`와 동일 기준(공통 속성 후보면 NULL)                                                                             |
| parent_aspect_id   | BIGINT NULL FK → aspects.id                                          | 상위 속성 아래 세부 속성 후보일 때만 사용(부모 속성은 이미 등록돼 있다고 전제)                                                       |
| marker_candidate   | VARCHAR(100)                                                         | 정규화된 마커 후보                                                                                                                   |
| name_candidate_ko  | VARCHAR(100)                                                         | 표시용 명칭 후보 한국어 (D-028)                                                                                                      |
| name_candidate_en  | VARCHAR(100)                                                         | 표시용 명칭 후보 영어 (D-028)                                                                                                        |
| status             | ENUM('pending','approved','rejected')                                |                                                                                                                                      |
| resolved_aspect_id | BIGINT NULL FK → aspects.id                                          | 이 후보가 최종적으로 연결된 속성. `product_match_candidates.resolved_product_id`와 같은 이유로 승인·거절과 독립적으로 채워질 수 있다 |
| reject_reason      | ENUM('not_a_real_aspect','wrong_marker','out_of_scope','other') NULL | `rejected`일 때만 채움. 의미는 `product_match_candidates.reject_reason`과 같은 패턴                                                  |
| reviewed_at        | DATETIME NULL                                                        |                                                                                                                                      |
| created_at         | DATETIME                                                             |                                                                                                                                      |
| updated_at         | DATETIME                                                             |                                                                                                                                      |

- UNIQUE(category_id, marker_candidate) — `aspects`의 UNIQUE(category_id, marker)와 동일 기준. `category_id`가 NULL인 후보끼리는 삽입 전 조회로 차단한다 (D-029)

### aspect_candidate_evidence

후보 하나에 걸린 증거(관찰) 1:N (D-025 v2).

| 컬럼         | 타입                             | 설명                                                                                                                                |
| ------------ | -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| id           | BIGINT PK                        |                                                                                                                                     |
| candidate_id | BIGINT FK → aspect_candidates.id |                                                                                                                                     |
| point_id     | BIGINT FK → review_points.id     | 이 속성 후보가 나온 관찰. 리뷰는 `review_points.review_id`로 따라간다. 후보가 승인되면 이 관찰에 `point_aspects`를 연결한다 (D-025) |
| quote        | TEXT NULL                        | 그 관찰의 원문 인용(검토자 참고용)                                                                                                  |
| created_at   | DATETIME                         |                                                                                                                                     |

- UNIQUE(candidate_id, point_id) — 같은 관찰이 같은 후보에 중복으로 쌓이지 않도록

## 다음 논의할 도메인

- (PH-3 이후) 크리에이터 통계 — `channels`와 분리된 별도 테이블
- 카테고리 착수 시점에 별도 설계: 카테고리별 스펙 테이블(`phone_specs` 등), `reviews.category_tags`의 실제 태그 값 체계
