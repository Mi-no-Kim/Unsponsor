CREATE TABLE reviews (
  id BIGINT NOT NULL AUTO_INCREMENT,
  video_id BIGINT NOT NULL,
  product_id BIGINT NULL,
  candidate_id BIGINT NULL,
  is_sponsored_review BOOLEAN NOT NULL,
  role ENUM('primary', 'secondary') NOT NULL,
  pros_ko TEXT NULL,
  pros_en TEXT NULL,
  cons_ko TEXT NULL,
  cons_en TEXT NULL,
  category_tags JSON NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_reviews_video_product UNIQUE (video_id, product_id),
  CONSTRAINT uq_reviews_video_candidate UNIQUE (video_id, candidate_id),
  CONSTRAINT chk_reviews_product_or_candidate
    CHECK (product_id IS NOT NULL OR candidate_id IS NOT NULL),
  CONSTRAINT fk_reviews_video
    FOREIGN KEY (video_id) REFERENCES videos (id),
  CONSTRAINT fk_reviews_product
    FOREIGN KEY (product_id) REFERENCES products (id),
  CONSTRAINT fk_reviews_candidate
    FOREIGN KEY (candidate_id) REFERENCES product_match_candidates (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE product_aggregates (
  id BIGINT NOT NULL AUTO_INCREMENT,
  product_id BIGINT NOT NULL,
  excludes_sponsored BOOLEAN NOT NULL,
  pros_summary_ko TEXT NOT NULL,
  pros_summary_en TEXT NOT NULL,
  cons_summary_ko TEXT NOT NULL,
  cons_summary_en TEXT NOT NULL,
  review_count INT NOT NULL,
  last_computed_at DATETIME NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_product_aggregates_product_sponsored
    UNIQUE (product_id, excludes_sponsored),
  CONSTRAINT fk_product_aggregates_product
    FOREIGN KEY (product_id) REFERENCES products (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE aspects (
  id BIGINT NOT NULL AUTO_INCREMENT,
  parent_aspect_id BIGINT NULL,
  category_id BIGINT NULL,
  name_ko VARCHAR(100) NOT NULL,
  name_en VARCHAR(100) NOT NULL,
  marker VARCHAR(100) NOT NULL,
  description TEXT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_aspects_category_marker UNIQUE (category_id, marker),
  CONSTRAINT fk_aspects_parent
    FOREIGN KEY (parent_aspect_id) REFERENCES aspects (id),
  CONSTRAINT fk_aspects_category
    FOREIGN KEY (category_id) REFERENCES categories (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE review_points (
  id BIGINT NOT NULL AUTO_INCREMENT,
  review_id BIGINT NOT NULL,
  polarity ENUM('pro', 'con') NOT NULL,
  quote_ko TEXT NULL,
  quote_en TEXT NULL,
  created_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT fk_review_points_review
    FOREIGN KEY (review_id) REFERENCES reviews (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE point_aspects (
  point_id BIGINT NOT NULL,
  aspect_id BIGINT NOT NULL,
  PRIMARY KEY (point_id, aspect_id),
  CONSTRAINT fk_point_aspects_point
    FOREIGN KEY (point_id) REFERENCES review_points (id),
  CONSTRAINT fk_point_aspects_aspect
    FOREIGN KEY (aspect_id) REFERENCES aspects (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE product_aspect_stats (
  id BIGINT NOT NULL AUTO_INCREMENT,
  product_id BIGINT NOT NULL,
  aspect_id BIGINT NOT NULL,
  excludes_sponsored BOOLEAN NOT NULL,
  positive_count INT NOT NULL,
  negative_count INT NOT NULL,
  last_computed_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_product_aspect_stats_product_aspect_sponsored
    UNIQUE (product_id, aspect_id, excludes_sponsored),
  CONSTRAINT fk_product_aspect_stats_product
    FOREIGN KEY (product_id) REFERENCES products (id),
  CONSTRAINT fk_product_aspect_stats_aspect
    FOREIGN KEY (aspect_id) REFERENCES aspects (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE aspect_candidates (
  id BIGINT NOT NULL AUTO_INCREMENT,
  category_id BIGINT NULL,
  parent_aspect_id BIGINT NULL,
  marker_candidate VARCHAR(100) NOT NULL,
  name_candidate_ko VARCHAR(100) NOT NULL,
  name_candidate_en VARCHAR(100) NOT NULL,
  status ENUM('pending', 'approved', 'rejected') NOT NULL,
  resolved_aspect_id BIGINT NULL,
  reject_reason ENUM('not_a_real_aspect', 'wrong_marker', 'out_of_scope', 'other') NULL,
  reviewed_at DATETIME NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_aspect_candidates_category_marker
    UNIQUE (category_id, marker_candidate),
  CONSTRAINT fk_aspect_candidates_category
    FOREIGN KEY (category_id) REFERENCES categories (id),
  CONSTRAINT fk_aspect_candidates_parent
    FOREIGN KEY (parent_aspect_id) REFERENCES aspects (id),
  CONSTRAINT fk_aspect_candidates_resolved
    FOREIGN KEY (resolved_aspect_id) REFERENCES aspects (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE aspect_candidate_evidence (
  id BIGINT NOT NULL AUTO_INCREMENT,
  candidate_id BIGINT NOT NULL,
  point_id BIGINT NOT NULL,
  quote TEXT NULL,
  created_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_aspect_candidate_evidence_candidate_point
    UNIQUE (candidate_id, point_id),
  CONSTRAINT fk_aspect_candidate_evidence_candidate
    FOREIGN KEY (candidate_id) REFERENCES aspect_candidates (id),
  CONSTRAINT fk_aspect_candidate_evidence_point
    FOREIGN KEY (point_id) REFERENCES review_points (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
