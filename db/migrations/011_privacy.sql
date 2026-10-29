-- T22.2 LGPD: consentimento da política e registro de solicitações do titular. Idempotente.
CREATE TABLE IF NOT EXISTS privacy_consents (
 user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
 policy_version TEXT NOT NULL CHECK (length(policy_version) BETWEEN 1 AND 20),
 accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY (user_id, policy_version)
);
CREATE TABLE IF NOT EXISTS data_requests (
 id BIGSERIAL PRIMARY KEY,
 user_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
 kind TEXT NOT NULL CHECK (kind IN ('export','deletion')),
 status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','rejected')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_data_requests_user ON data_requests (user_id, created_at DESC);
