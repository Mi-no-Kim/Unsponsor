package xyz.unsponsor.backend;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;

import org.flywaydb.core.Flyway;
import org.flywaydb.core.api.FlywayException;
import org.flywaydb.core.api.MigrationVersion;
import org.junit.jupiter.api.Test;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.mysql.MySQLContainer;
import org.testcontainers.utility.DockerImageName;

@Testcontainers
class TranscriptCompactionMigrationTests {
	@Container
	private static final MySQLContainer MYSQL = new MySQLContainer(
			DockerImageName.parse("mysql:8.4"));

	@Test
	void abortsBeforeSchemaDeletionWhenLegacyRepresentationsDiverge() throws Exception {
		Flyway.configure()
				.dataSource(MYSQL.getJdbcUrl(), MYSQL.getUsername(), MYSQL.getPassword())
				.target(MigrationVersion.fromVersion("5"))
				.load()
				.migrate();

		try (Connection connection = MYSQL.createConnection("");
				Statement statement = connection.createStatement()) {
			statement.executeUpdate("""
					INSERT INTO channels (
					  youtube_channel_id, uploads_playlist_id, name, language_code, created_at, updated_at
					) VALUES (
					  'UCaaaaaaaaaaaaaaaaaaaaaa', 'UUbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
					  'migration', 'ko', UTC_TIMESTAMP(), UTC_TIMESTAMP()
					)
					""");
			statement.executeUpdate("""
					INSERT INTO videos (
					  youtube_video_id, channel_id, title, published_at, language_code, created_at, updated_at
					) VALUES (
					  'verify00011', 1, 'migration', UTC_TIMESTAMP(), 'ko', UTC_TIMESTAMP(), UTC_TIMESTAMP()
					)
					""");
			statement.executeUpdate("""
					INSERT INTO video_transcripts (video_id, raw_text, source, created_at, updated_at)
					VALUES (1, 'first representation', 'library', UTC_TIMESTAMP(), UTC_TIMESTAMP())
					""");
			statement.executeUpdate("""
					INSERT INTO video_transcript_segments (
					  video_id, sequence, start_ms, end_ms, text, created_at
					) VALUES (1, 0, 0, 1000, 'different representation', UTC_TIMESTAMP())
					""");
		}

		Flyway flyway = Flyway.configure()
				.dataSource(MYSQL.getJdbcUrl(), MYSQL.getUsername(), MYSQL.getPassword())
				.load();
		assertThrows(FlywayException.class, flyway::migrate);

		try (Connection connection = MYSQL.createConnection("");
				Statement statement = connection.createStatement()) {
			assertEquals(0, count(statement, """
					SELECT COUNT(*)
					FROM information_schema.columns
					WHERE table_schema = DATABASE()
					  AND table_name = 'video_transcripts'
					  AND column_name = 'transcript_payload'
					"""));
			assertEquals(1, count(statement, """
					SELECT COUNT(*)
					FROM information_schema.columns
					WHERE table_schema = DATABASE()
					  AND table_name = 'video_transcripts'
					  AND column_name = 'raw_text'
					"""));
			assertEquals(1, count(statement, """
					SELECT COUNT(*)
					FROM information_schema.tables
					WHERE table_schema = DATABASE()
					  AND table_name = 'video_transcript_segments'
					"""));
			assertEquals(1, count(statement, "SELECT COUNT(*) FROM video_transcripts"));
			assertEquals(1, count(statement, "SELECT COUNT(*) FROM video_transcript_segments"));
		}
	}

	private static int count(Statement statement, String query) throws Exception {
		try (ResultSet result = statement.executeQuery(query)) {
			result.next();
			return result.getInt(1);
		}
	}
}
