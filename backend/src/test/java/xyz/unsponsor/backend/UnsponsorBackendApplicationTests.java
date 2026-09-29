package xyz.unsponsor.backend;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.stream.Stream;

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
	private static final Set<String> V2_TABLES = Set.of(
			"categories",
			"brands",
			"series",
			"products",
			"product_match_candidates",
			"product_match_evidence");
	private static final Set<String> V3_TABLES = Set.of(
			"reviews",
			"product_aggregates",
			"aspects",
			"review_points",
			"point_aspects",
			"product_aspect_stats",
			"aspect_candidates",
			"aspect_candidate_evidence");
	private static final Set<String> V4_TABLES = Set.of("video_transcript_segments");
	private static final Set<String> SCHEMA_TABLES = Stream.of(V1_TABLES, V2_TABLES, V3_TABLES, V4_TABLES)
			.flatMap(Set::stream)
			.collect(Collectors.toUnmodifiableSet());

	@Autowired
	JdbcTemplate jdbcTemplate;

	@Test
	void contextLoads() {
	}

	@Test
	void appliesMigrations() {
		List<String> appliedVersions = jdbcTemplate.queryForList(
				"SELECT version FROM flyway_schema_history WHERE success = TRUE ORDER BY installed_rank",
				String.class);

		assertEquals(List.of("1", "2", "3", "4"), appliedVersions);
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
	void createsV2Tables() {
		List<String> tableNames = jdbcTemplate.queryForList(
				"""
				SELECT table_name
				FROM information_schema.tables
				WHERE table_schema = DATABASE()
				  AND table_name IN (
				    'categories',
				    'brands',
				    'series',
				    'products',
				    'product_match_candidates',
				    'product_match_evidence'
				  )
				""",
				String.class);

		assertEquals(V2_TABLES, new HashSet<>(tableNames));
	}

	@Test
	void createsV3Tables() {
		List<String> tableNames = jdbcTemplate.queryForList(
				"""
				SELECT table_name
				FROM information_schema.tables
				WHERE table_schema = DATABASE()
				  AND table_name IN (
				    'reviews',
				    'product_aggregates',
				    'aspects',
				    'review_points',
				    'point_aspects',
				    'product_aspect_stats',
				    'aspect_candidates',
				    'aspect_candidate_evidence'
				  )
				""",
				String.class);

		assertEquals(V3_TABLES, new HashSet<>(tableNames));
	}

	@Test
	void createsV4TranscriptSegmentTable() {
		List<String> tableNames = jdbcTemplate.queryForList(
				"""
				SELECT table_name
				FROM information_schema.tables
				WHERE table_schema = DATABASE()
				  AND table_name = 'video_transcript_segments'
				""",
				String.class);

		assertEquals(V4_TABLES, new HashSet<>(tableNames));
	}

	@Test
	void givesTranscriptSegmentsTheirTimelineColumns() {
		List<String> columnNames = jdbcTemplate.queryForList(
				"""
				SELECT column_name
				FROM information_schema.columns
				WHERE table_schema = DATABASE()
				  AND table_name = 'video_transcript_segments'
				""",
				String.class);

		assertEquals(
				Set.of("id", "video_id", "sequence", "start_ms", "end_ms", "text", "created_at"),
				new HashSet<>(columnNames));
	}

	@Test
	void createsAllSchemaTables() {
		List<String> tableNames = jdbcTemplate.queryForList(
				"""
				SELECT table_name
				FROM information_schema.tables
				WHERE table_schema = DATABASE()
				  AND table_type = 'BASE TABLE'
				  AND table_name <> 'flyway_schema_history'
				""",
				String.class);

		assertEquals(SCHEMA_TABLES, new HashSet<>(tableNames));
	}

	@Test
	void insertsInitialLanguages() {
		List<String> languageCodes = jdbcTemplate.queryForList(
				"SELECT code FROM languages ORDER BY code",
				String.class);

		assertEquals(List.of("en", "ko"), languageCodes);
	}

}
