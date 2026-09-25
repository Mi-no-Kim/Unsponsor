package xyz.unsponsor.backend;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.JdbcTemplate;

@Import(TestcontainersConfiguration.class)
@SpringBootTest
class UnsponsorBackendApplicationTests {
	private static final Set<String> V1_TABLES = Set.of(
			"languages",
			"channels",
			"videos",
			"video_transcripts",
			"ad_segments",
			"video_processing_queue");

	@Autowired
	JdbcTemplate jdbcTemplate;

	@Test
	void contextLoads() {
	}

	@Test
	void appliesV1Migration() {
		List<String> appliedVersions = jdbcTemplate.queryForList(
				"SELECT version FROM flyway_schema_history WHERE success = TRUE",
				String.class);

		assertTrue(appliedVersions.contains("1"));
	}

	@Test
	void createsV1Tables() {
		List<String> tableNames = jdbcTemplate.queryForList(
				"""
				SELECT table_name
				FROM information_schema.tables
				WHERE table_schema = DATABASE()
				  AND table_name IN (
				    'languages',
				    'channels',
				    'videos',
				    'video_transcripts',
				    'ad_segments',
				    'video_processing_queue'
				  )
				""",
				String.class);

		assertEquals(V1_TABLES, new HashSet<>(tableNames));
	}

	@Test
	void insertsInitialLanguages() {
		List<String> languageCodes = jdbcTemplate.queryForList(
				"SELECT code FROM languages ORDER BY code",
				String.class);

		assertEquals(List.of("en", "ko"), languageCodes);
	}

}
