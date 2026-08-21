-- =============================================================================
-- VIN Decoder - PostgreSQL schema
--
-- The ORM in app/db/models.py can create these tables itself via
-- Base.metadata.create_all (which is what the SQLite dev fallback uses). This
-- file is the canonical DDL for a real PostgreSQL deployment: it adds the
-- things the portable ORM definition cannot express - JSONB, GIN indexes,
-- partial indexes, CHECK constraints and generated columns.
--
--   createdb vin_decoder
--   psql -d vin_decoder -f app/db/schema_postgres.sql
--
-- Then set: DATABASE_URL=postgresql+psycopg://user:pass@host:5432/vin_decoder
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- data_sources: one row per provider the system knows about.
-- Seeded from the provider registry at application startup.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_sources (
    id             SERIAL PRIMARY KEY,
    name           VARCHAR(64)  NOT NULL UNIQUE,
    label          VARCHAR(128) NOT NULL,
    kind           VARCHAR(16)  NOT NULL,
    priority       INTEGER      NOT NULL DEFAULT 100,
    cost_per_call  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    enabled        BOOLEAN      NOT NULL DEFAULT TRUE,
    description    TEXT,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_data_sources_kind CHECK (kind IN ('FREE', 'COMMERCIAL', 'LOCAL')),
    CONSTRAINT ck_data_sources_cost CHECK (cost_per_call >= 0)
);

COMMENT ON TABLE  data_sources IS 'Registered VIN data providers and their cost class.';
COMMENT ON COLUMN data_sources.priority IS 'Lower runs first; also breaks ties during merge.';

-- -----------------------------------------------------------------------------
-- vehicles: the merged, canonical answer for one VIN. This table IS the cache.
-- A row inside its TTL is served without contacting any external provider.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vehicles (
    id                     BIGSERIAL PRIMARY KEY,
    vin                    CHAR(17)     NOT NULL UNIQUE,

    valid                  BOOLEAN      NOT NULL DEFAULT FALSE,
    check_digit_valid      BOOLEAN,
    status                 VARCHAR(16)  NOT NULL DEFAULT 'OK',

    year                   SMALLINT,
    make                   VARCHAR(64),
    model                  VARCHAR(128),
    trim                   VARCHAR(128),
    body_type              VARCHAR(64),
    vehicle_type           VARCHAR(64),

    engine_displacement_l  NUMERIC(4, 1),
    engine_cylinders       SMALLINT,
    engine_type            VARCHAR(64),
    horsepower             INTEGER,
    fuel                   VARCHAR(48),
    drivetrain             VARCHAR(16),
    transmission           VARCHAR(48),

    manufacturer           VARCHAR(128),
    plant_country          VARCHAR(64),

    overall_confidence     VARCHAR(16)  NOT NULL DEFAULT 'UNKNOWN',
    discrepancy_count      INTEGER      NOT NULL DEFAULT 0,

    -- Full serialized VehicleRecord, so a cache hit reconstructs the exact
    -- response (provenance, alternatives and all) without re-merging.
    record                 JSONB        NOT NULL DEFAULT '{}'::jsonb,

    first_decoded_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_decoded_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    lookup_count           INTEGER      NOT NULL DEFAULT 0,
    total_cost             DOUBLE PRECISION NOT NULL DEFAULT 0.0,

    CONSTRAINT ck_vehicles_vin_charset  CHECK (vin ~ '^[A-HJ-NPR-Z0-9]{17}$'),
    CONSTRAINT ck_vehicles_year         CHECK (year IS NULL OR year BETWEEN 1900 AND 2100),
    CONSTRAINT ck_vehicles_cylinders    CHECK (engine_cylinders IS NULL OR engine_cylinders BETWEEN 1 AND 16),
    CONSTRAINT ck_vehicles_horsepower   CHECK (horsepower IS NULL OR horsepower BETWEEN 1 AND 2000),
    CONSTRAINT ck_vehicles_confidence   CHECK (overall_confidence IN ('HIGH','MEDIUM','LOW','UNKNOWN')),
    CONSTRAINT ck_vehicles_status       CHECK (status IN ('OK','PARTIAL','INVALID_VIN','NOT_FOUND','ERROR'))
);

CREATE INDEX IF NOT EXISTS ix_vehicles_last_decoded   ON vehicles (last_decoded_at DESC);
CREATE INDEX IF NOT EXISTS ix_vehicles_make_model_year ON vehicles (make, model, year);
CREATE INDEX IF NOT EXISTS ix_vehicles_year            ON vehicles (year);
CREATE INDEX IF NOT EXISTS ix_vehicles_fuel            ON vehicles (fuel);

-- Surfacing problem records is a frequent query; a partial index keeps it cheap.
CREATE INDEX IF NOT EXISTS ix_vehicles_with_discrepancies
    ON vehicles (last_decoded_at DESC) WHERE discrepancy_count > 0;

-- Ad-hoc questions against the stored record ("which vehicles list a turbo?").
CREATE INDEX IF NOT EXISTS ix_vehicles_record_gin ON vehicles USING GIN (record jsonb_path_ops);

COMMENT ON TABLE  vehicles IS 'Merged canonical vehicle records; doubles as the VIN cache.';
COMMENT ON COLUMN vehicles.record IS 'Serialized VehicleRecord including field-level provenance.';

-- -----------------------------------------------------------------------------
-- vehicle_specifications: one row per resolved field.
-- Normalized so questions like "which fields do we most often have to buy?"
-- are answerable in SQL rather than by scanning JSON.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vehicle_specifications (
    id            BIGSERIAL PRIMARY KEY,
    vehicle_id    BIGINT       NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,

    field_name    VARCHAR(64)  NOT NULL,
    label         VARCHAR(128),
    value_text    TEXT,
    value_number  DOUBLE PRECISION,

    source_name   VARCHAR(64)  NOT NULL,
    source_kind   VARCHAR(16)  NOT NULL DEFAULT 'FREE',
    confidence    VARCHAR(16)  NOT NULL DEFAULT 'UNKNOWN',
    origin        VARCHAR(16)  NOT NULL DEFAULT 'ENRICHED',
    disputed      BOOLEAN      NOT NULL DEFAULT FALSE,
    note          TEXT,

    -- Losing candidates, retained so the audit trail survives.
    alternatives  JSONB        NOT NULL DEFAULT '[]'::jsonb,

    retrieved_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_spec_vehicle_field UNIQUE (vehicle_id, field_name),
    CONSTRAINT ck_spec_confidence CHECK (confidence IN ('HIGH','MEDIUM','LOW','UNKNOWN')),
    CONSTRAINT ck_spec_origin     CHECK (origin IN ('VIN_DECODED','ENRICHED'))
);

CREATE INDEX IF NOT EXISTS ix_spec_vehicle          ON vehicle_specifications (vehicle_id);
CREATE INDEX IF NOT EXISTS ix_spec_field_confidence ON vehicle_specifications (field_name, confidence);
CREATE INDEX IF NOT EXISTS ix_spec_source           ON vehicle_specifications (source_name);
CREATE INDEX IF NOT EXISTS ix_spec_disputed         ON vehicle_specifications (field_name) WHERE disputed;

COMMENT ON TABLE vehicle_specifications IS 'Field-level values with source, confidence and origin.';

-- -----------------------------------------------------------------------------
-- source_discrepancies: recorded disagreements between providers.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_discrepancies (
    id               BIGSERIAL PRIMARY KEY,
    vehicle_id       BIGINT      NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,

    field_name       VARCHAR(64) NOT NULL,
    label            VARCHAR(128),
    selected_value   TEXT,
    selected_source  VARCHAR(64),
    conflicting      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    severity         VARCHAR(16) NOT NULL DEFAULT 'warning',
    message          TEXT,
    detected_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved         BOOLEAN     NOT NULL DEFAULT FALSE,

    CONSTRAINT ck_discrepancy_severity CHECK (severity IN ('warning', 'critical'))
);

CREATE INDEX IF NOT EXISTS ix_discrepancy_vehicle ON source_discrepancies (vehicle_id);
CREATE INDEX IF NOT EXISTS ix_discrepancy_field   ON source_discrepancies (field_name);
CREATE INDEX IF NOT EXISTS ix_discrepancy_open    ON source_discrepancies (detected_at DESC) WHERE NOT resolved;

COMMENT ON TABLE source_discrepancies IS 'Provider disagreements, retained rather than silently resolved.';

-- -----------------------------------------------------------------------------
-- provider_responses: raw payloads, kept so a normalization fix can be replayed
-- against stored data instead of re-querying (and re-paying for) every VIN.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS provider_responses (
    id               BIGSERIAL PRIMARY KEY,
    vin              VARCHAR(17) NOT NULL,
    provider_name    VARCHAR(64) NOT NULL,
    success          BOOLEAN     NOT NULL DEFAULT FALSE,
    status_code      INTEGER,
    latency_ms       INTEGER     NOT NULL DEFAULT 0,
    error            TEXT,
    error_code       VARCHAR(48),
    fields_returned  INTEGER     NOT NULL DEFAULT 0,
    cost             DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    raw_response     JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_provider_responses_vin      ON provider_responses (vin);
CREATE INDEX IF NOT EXISTS ix_provider_responses_provider ON provider_responses (provider_name, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_provider_responses_created  ON provider_responses (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_provider_responses_failures ON provider_responses (provider_name, created_at DESC) WHERE NOT success;

-- -----------------------------------------------------------------------------
-- vin_lookups: audit log of every decode request, cached or not.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vin_lookups (
    id              BIGSERIAL PRIMARY KEY,
    vin             VARCHAR(32) NOT NULL,
    raw_input       VARCHAR(64),
    valid           BOOLEAN     NOT NULL DEFAULT FALSE,
    status          VARCHAR(16) NOT NULL DEFAULT 'OK',
    cache_hit       BOOLEAN     NOT NULL DEFAULT FALSE,
    provider_calls  INTEGER     NOT NULL DEFAULT 0,
    cost            DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    elapsed_ms      INTEGER     NOT NULL DEFAULT 0,
    client_ip       VARCHAR(64),
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_vin_lookups_vin     ON vin_lookups (vin);
CREATE INDEX IF NOT EXISTS ix_vin_lookups_created ON vin_lookups (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_vin_lookups_client  ON vin_lookups (client_ip, created_at DESC);

-- -----------------------------------------------------------------------------
-- api_usage: per-provider, per-day counters. Backs the usage dashboard and the
-- daily commercial-call ceiling.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_usage (
    id                SERIAL PRIMARY KEY,
    provider_name     VARCHAR(64) NOT NULL,
    usage_date        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    calls             INTEGER     NOT NULL DEFAULT 0,
    successes         INTEGER     NOT NULL DEFAULT 0,
    failures          INTEGER     NOT NULL DEFAULT 0,
    cache_hits        INTEGER     NOT NULL DEFAULT 0,
    total_cost        DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    total_latency_ms  BIGINT      NOT NULL DEFAULT 0,

    CONSTRAINT uq_usage_provider_date UNIQUE (provider_name, usage_date),
    CONSTRAINT ck_usage_counts CHECK (calls >= 0 AND successes >= 0 AND failures >= 0)
);

CREATE INDEX IF NOT EXISTS ix_api_usage_date ON api_usage (usage_date DESC);

COMMIT;

-- =============================================================================
-- Operational views
-- =============================================================================

-- Which fields the free tier fails to supply - i.e. what a paid provider would
-- actually be buying you.
CREATE OR REPLACE VIEW v_field_coverage AS
SELECT
    s.field_name,
    s.label,
    COUNT(*)                                                       AS times_resolved,
    COUNT(*) FILTER (WHERE s.confidence = 'HIGH')                  AS high_confidence,
    COUNT(*) FILTER (WHERE s.disputed)                             AS disputed,
    COUNT(*) FILTER (WHERE s.origin = 'VIN_DECODED')               AS from_vin,
    COUNT(DISTINCT s.source_name)                                  AS distinct_sources,
    ROUND(100.0 * COUNT(*) FILTER (WHERE s.confidence = 'HIGH') / NULLIF(COUNT(*), 0), 1)
                                                                   AS pct_high_confidence
FROM vehicle_specifications s
GROUP BY s.field_name, s.label
ORDER BY times_resolved DESC;

-- Provider reliability and spend at a glance.
CREATE OR REPLACE VIEW v_provider_health AS
SELECT
    p.provider_name,
    COUNT(*)                                          AS calls,
    COUNT(*) FILTER (WHERE p.success)                 AS successes,
    COUNT(*) FILTER (WHERE NOT p.success)             AS failures,
    ROUND(100.0 * COUNT(*) FILTER (WHERE p.success) / NULLIF(COUNT(*), 0), 1) AS success_rate,
    ROUND(AVG(p.latency_ms))                          AS avg_latency_ms,
    MAX(p.latency_ms)                                 AS max_latency_ms,
    ROUND(SUM(p.cost)::numeric, 4)                    AS total_cost,
    MAX(p.created_at)                                 AS last_called_at
FROM provider_responses p
GROUP BY p.provider_name
ORDER BY calls DESC;

-- Cache effectiveness: the number that justifies the caching layer.
CREATE OR REPLACE VIEW v_cache_effectiveness AS
SELECT
    DATE_TRUNC('day', l.created_at)                          AS day,
    COUNT(*)                                                 AS lookups,
    COUNT(*) FILTER (WHERE l.cache_hit)                       AS cache_hits,
    ROUND(100.0 * COUNT(*) FILTER (WHERE l.cache_hit) / NULLIF(COUNT(*), 0), 1)
                                                             AS hit_rate_pct,
    ROUND(SUM(l.cost)::numeric, 4)                           AS spend,
    ROUND(AVG(l.elapsed_ms))                                 AS avg_elapsed_ms
FROM vin_lookups l
GROUP BY 1
ORDER BY 1 DESC;

-- Open disagreements needing a human decision.
CREATE OR REPLACE VIEW v_open_discrepancies AS
SELECT
    v.vin, v.year, v.make, v.model,
    d.field_name, d.label, d.severity,
    d.selected_value, d.selected_source, d.conflicting, d.detected_at
FROM source_discrepancies d
JOIN vehicles v ON v.id = d.vehicle_id
WHERE NOT d.resolved
ORDER BY (d.severity = 'critical') DESC, d.detected_at DESC;

-- =============================================================================
-- Cache maintenance
--
-- Cached rows never expire on their own; the application checks CACHE_TTL_HOURS
-- at read time. Run this from cron if you want stale rows physically removed:
--
--   DELETE FROM vehicles WHERE last_decoded_at < NOW() - INTERVAL '90 days';
--   DELETE FROM provider_responses WHERE created_at < NOW() - INTERVAL '30 days';
--   DELETE FROM vin_lookups WHERE created_at < NOW() - INTERVAL '180 days';
-- =============================================================================
