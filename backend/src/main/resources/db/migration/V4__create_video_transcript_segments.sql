CREATE TABLE video_transcript_segments (
  id BIGINT NOT NULL AUTO_INCREMENT,
  video_id BIGINT NOT NULL,
  sequence INT UNSIGNED NOT NULL,
  start_ms INT UNSIGNED NOT NULL,
  end_ms INT UNSIGNED NOT NULL,
  text TEXT NOT NULL,
  created_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_video_transcript_segments_video_sequence
    UNIQUE (video_id, sequence),
  CONSTRAINT chk_video_transcript_segments_timestamp_order
    CHECK (end_ms > start_ms),
  CONSTRAINT fk_video_transcript_segments_video
    FOREIGN KEY (video_id) REFERENCES videos (id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
