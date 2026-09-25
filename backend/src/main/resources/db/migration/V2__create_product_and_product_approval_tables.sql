CREATE TABLE categories (
  id BIGINT NOT NULL AUTO_INCREMENT,
  name_ko VARCHAR(100) NOT NULL,
  name_en VARCHAR(100) NOT NULL,
  slug VARCHAR(50) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_categories_slug UNIQUE (slug)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE brands (
  id BIGINT NOT NULL AUTO_INCREMENT,
  parent_brand_id BIGINT NULL,
  name VARCHAR(255) NOT NULL,
  marker VARCHAR(100) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_brands_parent_marker UNIQUE (parent_brand_id, marker),
  CONSTRAINT fk_brands_parent
    FOREIGN KEY (parent_brand_id) REFERENCES brands (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE series (
  id BIGINT NOT NULL AUTO_INCREMENT,
  brand_id BIGINT NOT NULL,
  parent_series_id BIGINT NULL,
  category_id BIGINT NULL,
  name VARCHAR(255) NOT NULL,
  marker VARCHAR(100) NOT NULL,
  full_name VARCHAR(500) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_series_brand_parent_marker
    UNIQUE (brand_id, parent_series_id, marker),
  CONSTRAINT fk_series_brand
    FOREIGN KEY (brand_id) REFERENCES brands (id),
  CONSTRAINT fk_series_parent
    FOREIGN KEY (parent_series_id) REFERENCES series (id),
  CONSTRAINT fk_series_category
    FOREIGN KEY (category_id) REFERENCES categories (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE products (
  id BIGINT NOT NULL AUTO_INCREMENT,
  series_id BIGINT NOT NULL,
  category_id BIGINT NULL,
  name VARCHAR(255) NOT NULL,
  marker VARCHAR(100) NOT NULL,
  full_name VARCHAR(500) NOT NULL,
  marker_key VARCHAR(500) NOT NULL,
  options JSON NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_products_marker_key UNIQUE (marker_key),
  CONSTRAINT uq_products_series_marker UNIQUE (series_id, marker),
  CONSTRAINT fk_products_series
    FOREIGN KEY (series_id) REFERENCES series (id),
  CONSTRAINT fk_products_category
    FOREIGN KEY (category_id) REFERENCES categories (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE product_match_candidates (
  id BIGINT NOT NULL AUTO_INCREMENT,
  brand_marker_candidate VARCHAR(100) NOT NULL,
  series_marker_candidate VARCHAR(100) NULL,
  product_marker_candidate VARCHAR(100) NOT NULL,
  status ENUM('pending', 'approved', 'rejected') NOT NULL,
  resolved_product_id BIGINT NULL,
  reject_reason ENUM('not_a_product', 'wrong_marker', 'out_of_scope', 'other') NULL,
  reviewed_at DATETIME NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_product_match_candidates_markers
    UNIQUE (brand_marker_candidate, series_marker_candidate, product_marker_candidate),
  CONSTRAINT fk_product_match_candidates_product
    FOREIGN KEY (resolved_product_id) REFERENCES products (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE product_match_evidence (
  id BIGINT NOT NULL AUTO_INCREMENT,
  candidate_id BIGINT NOT NULL,
  video_id BIGINT NOT NULL,
  raw_name VARCHAR(500) NOT NULL,
  created_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_product_match_evidence_candidate_video
    UNIQUE (candidate_id, video_id),
  CONSTRAINT fk_product_match_evidence_candidate
    FOREIGN KEY (candidate_id) REFERENCES product_match_candidates (id),
  CONSTRAINT fk_product_match_evidence_video
    FOREIGN KEY (video_id) REFERENCES videos (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
