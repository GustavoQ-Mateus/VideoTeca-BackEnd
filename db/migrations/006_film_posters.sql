CREATE TABLE IF NOT EXISTS film_posters (
 film_id BIGINT PRIMARY KEY REFERENCES films(id) ON DELETE CASCADE,
 content BYTEA NOT NULL CHECK(octet_length(content) <= 5242880),
 media_type TEXT NOT NULL CHECK(media_type IN ('image/png','image/jpeg','image/webp'))
);
