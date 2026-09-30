ALTER TABLE video_transcripts
  MODIFY source ENUM('library', 'yt_dlp') NOT NULL;
