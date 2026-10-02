package db.migration;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.GZIPInputStream;
import java.util.zip.GZIPOutputStream;

import org.flywaydb.core.api.migration.BaseJavaMigration;
import org.flywaydb.core.api.migration.Context;

public final class V6__compact_transcript_storage extends BaseJavaMigration {
	private static final String FORMAT = "gzip_json_v1";
	private static final int MAX_COMPRESSED_BYTES = 8 * 1024 * 1024;
	private static final int MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024;
	private static final int MAX_SEGMENTS = 100_000;
	private static final int MAX_TEXT_BYTES = 65_535;
	private static final long MAX_TIMESTAMP_MS = 4_294_967_295L;

	@Override
	public Integer getChecksum() {
		return 1;
	}

	@Override
	public boolean canExecuteInTransaction() {
		return false;
	}

	@Override
	public void migrate(Context context) throws Exception {
		Connection connection = context.getConnection();
		Map<Long, LegacyTranscript> transcripts = readAndValidateLegacyRows(connection);
		Map<Long, byte[]> payloads = encodeAndVerifyPayloads(transcripts);

		try (Statement statement = connection.createStatement()) {
			statement.executeUpdate("""
					ALTER TABLE video_transcripts
					  ADD COLUMN transcript_format ENUM('gzip_json_v1') NULL AFTER video_id,
					  ADD COLUMN transcript_payload MEDIUMBLOB NULL AFTER transcript_format
					""");
		}

		try (PreparedStatement update = connection.prepareStatement("""
				UPDATE video_transcripts
				SET transcript_format = ?, transcript_payload = ?
				WHERE video_id = ?
				""")) {
			for (Map.Entry<Long, byte[]> entry : payloads.entrySet()) {
				update.setString(1, FORMAT);
				update.setBytes(2, entry.getValue());
				update.setLong(3, entry.getKey());
				if (update.executeUpdate() != 1) {
					throw new IllegalStateException("transcript payload migration update failed");
				}
			}
		}

		try (Statement statement = connection.createStatement();
				ResultSet result = statement.executeQuery("""
						SELECT COUNT(*)
						FROM video_transcripts
						WHERE transcript_format IS NULL
						   OR transcript_format <> 'gzip_json_v1'
						   OR transcript_payload IS NULL
						   OR OCTET_LENGTH(transcript_payload) NOT BETWEEN 1 AND 8388608
						""")) {
			result.next();
			if (result.getLong(1) != 0) {
				throw new IllegalStateException("transcript payload migration verification failed");
			}
		}

		try (Statement statement = connection.createStatement()) {
			statement.executeUpdate("""
					ALTER TABLE video_transcripts
					  MODIFY transcript_format ENUM('gzip_json_v1') NOT NULL,
					  MODIFY transcript_payload MEDIUMBLOB NOT NULL,
					  DROP COLUMN raw_text,
					  ADD CONSTRAINT chk_video_transcripts_payload_size
					    CHECK (OCTET_LENGTH(transcript_payload) BETWEEN 1 AND 8388608)
					""");
			statement.executeUpdate("DROP TABLE video_transcript_segments");
		}
	}

	private static Map<Long, LegacyTranscript> readAndValidateLegacyRows(Connection connection)
			throws Exception {
		Map<Long, LegacyTranscriptBuilder> builders = new LinkedHashMap<>();
		try (Statement statement = connection.createStatement();
				ResultSet result = statement.executeQuery("""
						SELECT transcript.video_id,
						       transcript.raw_text,
						       segment.sequence,
						       segment.start_ms,
						       segment.end_ms,
						       segment.text
						FROM video_transcripts transcript
						LEFT JOIN video_transcript_segments segment
						  ON segment.video_id = transcript.video_id
						ORDER BY transcript.video_id, segment.sequence
						""")) {
			while (result.next()) {
				long videoId = result.getLong(1);
				LegacyTranscriptBuilder builder = builders.computeIfAbsent(
						videoId,
						ignored -> new LegacyTranscriptBuilder(resultString(result, 2)));
				int sequence = result.getInt(3);
				if (result.wasNull()) {
					continue;
				}
				long startMs = result.getLong(4);
				long endMs = result.getLong(5);
				String text = result.getString(6);
				builder.add(new LegacySegment(sequence, startMs, endMs, text));
			}
		}

		Map<Long, LegacyTranscript> transcripts = new LinkedHashMap<>();
		for (Map.Entry<Long, LegacyTranscriptBuilder> entry : builders.entrySet()) {
			transcripts.put(entry.getKey(), entry.getValue().build());
		}
		return transcripts;
	}

	private static String resultString(ResultSet result, int column) {
		try {
			return result.getString(column);
		} catch (Exception error) {
			throw new IllegalStateException("legacy transcript could not be read", error);
		}
	}

	private static Map<Long, byte[]> encodeAndVerifyPayloads(
			Map<Long, LegacyTranscript> transcripts) throws Exception {
		Map<Long, byte[]> payloads = new LinkedHashMap<>();
		for (Map.Entry<Long, LegacyTranscript> entry : transcripts.entrySet()) {
			byte[] canonical = canonicalJson(entry.getValue().segments()).getBytes(StandardCharsets.UTF_8);
			if (canonical.length > MAX_UNCOMPRESSED_BYTES) {
				throw new IllegalStateException("legacy transcript is too large before compression");
			}
			byte[] compressed;
			try (ByteArrayOutputStream output = new ByteArrayOutputStream();
					GZIPOutputStream gzip = new GZIPOutputStream(output)) {
				gzip.write(canonical);
				gzip.finish();
				compressed = output.toByteArray();
			}
			if (compressed.length == 0 || compressed.length > MAX_COMPRESSED_BYTES) {
				throw new IllegalStateException("legacy transcript is too large after compression");
			}
			byte[] decoded;
			try (GZIPInputStream gzip = new GZIPInputStream(new ByteArrayInputStream(compressed))) {
				decoded = gzip.readNBytes(MAX_UNCOMPRESSED_BYTES + 1);
				if (decoded.length > MAX_UNCOMPRESSED_BYTES || gzip.read() != -1) {
					throw new IllegalStateException("transcript payload verification exceeded its limit");
				}
			}
			if (!Arrays.equals(canonical, decoded)) {
				throw new IllegalStateException("transcript payload verification failed");
			}
			payloads.put(entry.getKey(), compressed);
		}
		return payloads;
	}

	private static String canonicalJson(List<LegacySegment> segments) {
		StringBuilder json = new StringBuilder("{\"v\":1,\"segments\":[");
		for (int index = 0; index < segments.size(); index++) {
			if (index > 0) {
				json.append(',');
			}
			LegacySegment segment = segments.get(index);
			json.append('[')
					.append(segment.startMs())
					.append(',')
					.append(segment.endMs() - segment.startMs())
					.append(',');
			appendJsonString(json, segment.text());
			json.append(']');
		}
		return json.append("]}").toString();
	}

	private static void appendJsonString(StringBuilder json, String value) {
		json.append('"');
		for (int index = 0; index < value.length(); index++) {
			char character = value.charAt(index);
			switch (character) {
				case '"' -> json.append("\\\"");
				case '\\' -> json.append("\\\\");
				case '\b' -> json.append("\\b");
				case '\f' -> json.append("\\f");
				case '\n' -> json.append("\\n");
				case '\r' -> json.append("\\r");
				case '\t' -> json.append("\\t");
				default -> {
					if (character < 0x20) {
						json.append(String.format("\\u%04x", (int) character));
					} else {
						json.append(character);
					}
				}
			}
		}
		json.append('"');
	}

	private record LegacySegment(int sequence, long startMs, long endMs, String text) {
	}

	private record LegacyTranscript(List<LegacySegment> segments) {
	}

	private static final class LegacyTranscriptBuilder {
		private final String rawText;
		private final List<LegacySegment> segments = new ArrayList<>();
		private long previousStartMs = -1;

		private LegacyTranscriptBuilder(String rawText) {
			if (rawText == null) {
				throw new IllegalStateException("legacy transcript raw text is missing");
			}
			this.rawText = rawText;
		}

		private void add(LegacySegment segment) {
			if (segments.size() >= MAX_SEGMENTS
					|| segment.sequence() != segments.size()
					|| segment.startMs() < 0
					|| segment.startMs() > MAX_TIMESTAMP_MS
					|| segment.endMs() <= segment.startMs()
					|| segment.endMs() > MAX_TIMESTAMP_MS
					|| segment.startMs() < previousStartMs
					|| segment.text() == null
					|| !isNormalizedText(segment.text())
					|| segment.text().getBytes(StandardCharsets.UTF_8).length > MAX_TEXT_BYTES) {
				throw new IllegalStateException("legacy transcript segment contract is invalid");
			}
			segments.add(segment);
			previousStartMs = segment.startMs();
		}

		private static boolean isNormalizedText(String text) {
			if (text == null || text.isEmpty()) {
				return false;
			}
			boolean previousWasSpace = false;
			for (int index = 0; index < text.length();) {
				char character = text.charAt(index);
				if (Character.isSurrogate(character)) {
					if (!Character.isHighSurrogate(character)
							|| index + 1 >= text.length()
							|| !Character.isLowSurrogate(text.charAt(index + 1))) {
						return false;
					}
					index += 2;
					previousWasSpace = false;
					continue;
				}
				int codePoint = text.codePointAt(index);
				boolean whitespace = Character.isWhitespace(codePoint) || Character.isSpaceChar(codePoint);
				if (whitespace && (codePoint != ' ' || index == 0 || previousWasSpace)) {
					return false;
				}
				previousWasSpace = whitespace;
				index += Character.charCount(codePoint);
			}
			return !previousWasSpace;
		}

		private LegacyTranscript build() {
			if (segments.isEmpty()) {
				throw new IllegalStateException("legacy transcript has no segments");
			}
			StringBuilder reconstructed = new StringBuilder();
			for (LegacySegment segment : segments) {
				if (!reconstructed.isEmpty()) {
					reconstructed.append('\n');
				}
				reconstructed.append(segment.text());
			}
			if (!rawText.contentEquals(reconstructed)) {
				throw new IllegalStateException("legacy transcript representations diverge");
			}
			return new LegacyTranscript(List.copyOf(segments));
		}
	}
}
