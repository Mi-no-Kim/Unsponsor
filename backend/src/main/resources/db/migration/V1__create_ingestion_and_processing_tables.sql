CREATE TABLE languages (
  code VARCHAR(10) NOT NULL,
  name VARCHAR(50) NOT NULL,
  PRIMARY KEY (code)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

INSERT INTO languages (code, name)
VALUES
  ('ko', '한국어'),
  ('en', 'English');

CREATE TABLE channels (
  id BIGINT NOT NULL AUTO_INCREMENT,
  youtube_channel_id VARCHAR(24) NOT NULL,
  uploads_playlist_id VARCHAR(34) NOT NULL,
  name VARCHAR(255) NOT NULL,
  language_code VARCHAR(10) NOT NULL,
  subscriber_count BIGINT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_channels_youtube_channel_id UNIQUE (youtube_channel_id),
  CONSTRAINT fk_channels_language
    FOREIGN KEY (language_code) REFERENCES languages (code)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE videos (
  id BIGINT NOT NULL AUTO_INCREMENT,
  youtube_video_id VARCHAR(11) NOT NULL,
  channel_id BIGINT NOT NULL,
  title VARCHAR(500) NOT NULL,
  description TEXT NULL,
  published_at DATETIME NOT NULL,
  language_code VARCHAR(10) NOT NULL,
  duration_seconds INT NULL,
  view_count BIGINT NULL,
  has_paid_product_placement BOOLEAN NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_videos_youtube_video_id UNIQUE (youtube_video_id),
  CONSTRAINT fk_videos_channel
    FOREIGN KEY (channel_id) REFERENCES channels (id),
  CONSTRAINT fk_videos_language
    FOREIGN KEY (language_code) REFERENCES languages (code)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE video_transcripts (
  video_id BIGINT NOT NULL,
  raw_text LONGTEXT NOT NULL,
  source ENUM('library', 'bs4', 'stt') NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (video_id),
  CONSTRAINT fk_video_transcripts_video
    FOREIGN KEY (video_id) REFERENCES videos (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE ad_segments (
  id BIGINT NOT NULL AUTO_INCREMENT,
  video_id BIGINT NOT NULL,
  start_seconds INT NOT NULL,
  end_seconds INT NOT NULL,
  source ENUM('llm', 'sponsorblock') NOT NULL,
  created_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT fk_ad_segments_video
    FOREIGN KEY (video_id) REFERENCES videos (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE video_processing_queue (
  id BIGINT NOT NULL AUTO_INCREMENT,
  video_id BIGINT NOT NULL,
  stage ENUM('transcript', 'identify', 'summarize') NOT NULL DEFAULT 'transcript',
  status ENUM('pending', 'processing', 'done', 'failed') NOT NULL,
  attempt_count INT NOT NULL DEFAULT 0,
  next_attempt_at DATETIME NULL,
  started_at DATETIME NULL,
  last_error TEXT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_video_processing_queue_video UNIQUE (video_id),
  CONSTRAINT fk_video_processing_queue_video
    FOREIGN KEY (video_id) REFERENCES videos (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
