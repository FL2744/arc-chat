CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(40) PRIMARY KEY,
    subject_hash CHAR(64) NOT NULL UNIQUE,
    preferred_email TEXT,
    display_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    disabled_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS projects (
    id VARCHAR(40) PRIMARY KEY,
    slug VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(160) NOT NULL,
    kind VARCHAR(32) NOT NULL,
    audience VARCHAR(16) NOT NULL,
    allowed_providers TEXT[] NOT NULL,
    default_provider VARCHAR(64) NOT NULL DEFAULT 'auto',
    data_classification VARCHAR(16) NOT NULL DEFAULT 'low',
    manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS project_memberships (
    project_id VARCHAR(40) NOT NULL REFERENCES projects(id),
    user_id VARCHAR(40) NOT NULL REFERENCES users(id),
    role VARCHAR(32) NOT NULL CHECK (role IN ('student','instructor','researcher','project_admin','platform_admin','service')),
    membership_source VARCHAR(24) NOT NULL DEFAULT 'direct' CHECK (membership_source IN ('direct','course_group')),
    source_group TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, user_id)
);

CREATE TABLE IF NOT EXISTS course_group_mappings (
    auth_group TEXT NOT NULL,
    project_id VARCHAR(40) NOT NULL REFERENCES projects(id),
    role VARCHAR(32) NOT NULL CHECK (role IN ('student','instructor','researcher','project_admin','platform_admin','service')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (auth_group, project_id)
);

CREATE TABLE IF NOT EXISTS user_group_snapshots (
    user_id VARCHAR(40) PRIMARY KEY REFERENCES users(id),
    groups_sha256 CHAR(64) NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS applications (
    id VARCHAR(40) PRIMARY KEY,
    project_id VARCHAR(40) NOT NULL REFERENCES projects(id),
    manifest JSONB NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS applications_project_idx ON applications(project_id) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS workspaces (
    id VARCHAR(40) PRIMARY KEY,
    owner_id VARCHAR(40) NOT NULL REFERENCES users(id),
    project_id VARCHAR(40) NOT NULL REFERENCES projects(id),
    application_id VARCHAR(40) REFERENCES applications(id),
    provider_id VARCHAR(64) NOT NULL,
    kind VARCHAR(24) NOT NULL,
    state VARCHAR(24) NOT NULL,
    provider_state VARCHAR(80) NOT NULL DEFAULT '',
    display_name VARCHAR(160) NOT NULL DEFAULT '',
    job_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    provider_resource_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    endpoint_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    artifact_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    deployment_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS workspaces_project_recent_idx ON workspaces(project_id, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS workspaces_owner_recent_idx ON workspaces(owner_id, updated_at DESC) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS provider_resources (
    id VARCHAR(40) PRIMARY KEY,
    provider_id VARCHAR(64) NOT NULL,
    provider_resource_id TEXT NOT NULL,
    resource_kind VARCHAR(32) NOT NULL,
    owner_id VARCHAR(40) REFERENCES users(id),
    project_id VARCHAR(40) REFERENCES projects(id),
    workspace_id VARCHAR(40) REFERENCES workspaces(id),
    state VARCHAR(80) NOT NULL,
    allocation VARCHAR(128) NOT NULL DEFAULT '',
    application_type VARCHAR(80) NOT NULL DEFAULT '',
    cluster VARCHAR(128) NOT NULL DEFAULT '',
    name VARCHAR(160) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (provider_id, provider_resource_id)
);
CREATE INDEX IF NOT EXISTS provider_resources_workspace_idx ON provider_resources(workspace_id);

CREATE TABLE IF NOT EXISTS jobs (
    id VARCHAR(40) PRIMARY KEY,
    workspace_id VARCHAR(40) REFERENCES workspaces(id),
    provider_resource_id VARCHAR(40) REFERENCES provider_resources(id),
    provider_id VARCHAR(64) NOT NULL,
    state VARCHAR(80) NOT NULL,
    name VARCHAR(160) NOT NULL DEFAULT '',
    submitted_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    command_sha256 CHAR(64),
    resource_request JSONB NOT NULL DEFAULT '{}'::jsonb,
    log_reference TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS jobs_workspace_idx ON jobs(workspace_id);

CREATE TABLE IF NOT EXISTS endpoints (
    id VARCHAR(40) PRIMARY KEY,
    workspace_id VARCHAR(40) REFERENCES workspaces(id),
    provider_resource_id VARCHAR(40) REFERENCES provider_resources(id),
    provider_id VARCHAR(64) NOT NULL,
    hostname TEXT,
    state VARCHAR(80) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS artifacts (
    id VARCHAR(40) PRIMARY KEY,
    project_id VARCHAR(40) NOT NULL REFERENCES projects(id),
    workspace_id VARCHAR(40) REFERENCES workspaces(id),
    owner_id VARCHAR(40) NOT NULL REFERENCES users(id),
    type VARCHAR(64) NOT NULL,
    media_type VARCHAR(160) NOT NULL,
    content_sha256 CHAR(64),
    external_reference TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS deployments (
    id VARCHAR(40) PRIMARY KEY,
    project_id VARCHAR(40) NOT NULL REFERENCES projects(id),
    application_id VARCHAR(40) NOT NULL REFERENCES applications(id),
    created_by VARCHAR(40) NOT NULL REFERENCES users(id),
    provider_id VARCHAR(64) NOT NULL,
    environment VARCHAR(24) NOT NULL,
    state VARCHAR(80) NOT NULL,
    image_digest TEXT,
    manifest_revision TEXT,
    endpoint_id VARCHAR(40) REFERENCES endpoints(id),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS deployment_revisions (
    id VARCHAR(40) PRIMARY KEY,
    deployment_id VARCHAR(40) NOT NULL REFERENCES deployments(id),
    source_commit TEXT,
    image_digest TEXT,
    manifest_revision TEXT,
    initiated_by VARCHAR(40) NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS provider_connections (
    id VARCHAR(40) PRIMARY KEY,
    provider_id VARCHAR(64) NOT NULL,
    owner_id VARCHAR(40) NOT NULL REFERENCES users(id),
    credential_ref TEXT NOT NULL,
    state VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS audit_events (
    id VARCHAR(40) PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_id VARCHAR(40) REFERENCES users(id),
    project_id VARCHAR(40) REFERENCES projects(id),
    object_type VARCHAR(64) NOT NULL,
    object_id VARCHAR(40),
    action VARCHAR(64) NOT NULL,
    result VARCHAR(32) NOT NULL,
    request_id VARCHAR(80) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS audit_project_recent_idx ON audit_events(project_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    actor_id VARCHAR(40) NOT NULL REFERENCES users(id),
    key_hash CHAR(64) NOT NULL,
    request_hash CHAR(64) NOT NULL,
    status_code INTEGER,
    response_body JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (actor_id, key_hash)
);

CREATE TABLE IF NOT EXISTS platform_events (
    event_seq BIGSERIAL PRIMARY KEY,
    id VARCHAR(40) NOT NULL UNIQUE,
    actor_id VARCHAR(40) NOT NULL REFERENCES users(id),
    project_id VARCHAR(40) NOT NULL REFERENCES projects(id),
    event_type VARCHAR(80) NOT NULL,
    object_type VARCHAR(64) NOT NULL,
    object_id VARCHAR(40) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS platform_events_actor_seq_idx ON platform_events(actor_id, event_seq);

CREATE TABLE IF NOT EXISTS rate_limits (
    actor_key CHAR(64) NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    request_count INTEGER NOT NULL,
    PRIMARY KEY (actor_key, window_start)
);

CREATE OR REPLACE FUNCTION reject_append_only_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'append-only table cannot be updated or deleted';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events;
CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION reject_append_only_change();

INSERT INTO schema_migrations(version) VALUES (1) ON CONFLICT (version) DO NOTHING;
