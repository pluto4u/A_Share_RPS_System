PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS themes (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    demand_driver TEXT NOT NULL DEFAULT '',
    thesis TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'research',
    as_of_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    code TEXT PRIMARY KEY,
    theme_code TEXT NOT NULL REFERENCES themes(code),
    parent_code TEXT REFERENCES nodes(code),
    name TEXT NOT NULL,
    layer_order INTEGER NOT NULL DEFAULT 0,
    node_type TEXT NOT NULL DEFAULT 'component',
    description TEXT NOT NULL DEFAULT '',
    bottleneck_type TEXT NOT NULL DEFAULT '',
    expansion_lead_months REAL NOT NULL DEFAULT 0,
    certification_months REAL NOT NULL DEFAULT 0,
    supply_concentration REAL NOT NULL DEFAULT 50,
    substitution_difficulty REAL NOT NULL DEFAULT 50,
    capacity_elasticity REAL NOT NULL DEFAULT 50,
    demand_certainty REAL NOT NULL DEFAULT 50,
    pricing_power REAL NOT NULL DEFAULT 50,
    as_of_date TEXT NOT NULL,
    CHECK (supply_concentration BETWEEN 0 AND 100),
    CHECK (substitution_difficulty BETWEEN 0 AND 100),
    CHECK (capacity_elasticity BETWEEN 0 AND 100),
    CHECK (demand_certainty BETWEEN 0 AND 100),
    CHECK (pricing_power BETWEEN 0 AND 100)
);

CREATE TABLE IF NOT EXISTS node_relations (
    upstream_code TEXT NOT NULL REFERENCES nodes(code),
    downstream_code TEXT NOT NULL REFERENCES nodes(code),
    relation_type TEXT NOT NULL DEFAULT 'supplies',
    criticality REAL NOT NULL DEFAULT 50,
    substitution_difficulty REAL NOT NULL DEFAULT 50,
    lead_time_months REAL NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (upstream_code, downstream_code, relation_type),
    CHECK (criticality BETWEEN 0 AND 100),
    CHECK (substitution_difficulty BETWEEN 0 AND 100)
);

CREATE TABLE IF NOT EXISTS companies (
    security_id TEXT PRIMARY KEY,
    ts_code TEXT UNIQUE,
    ticker TEXT NOT NULL,
    name TEXT NOT NULL,
    market TEXT NOT NULL,
    country TEXT NOT NULL,
    market_cap_bucket TEXT NOT NULL DEFAULT 'unknown',
    description TEXT NOT NULL DEFAULT '',
    as_of_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company_exposures (
    security_id TEXT NOT NULL REFERENCES companies(security_id),
    node_code TEXT NOT NULL REFERENCES nodes(code),
    role TEXT NOT NULL DEFAULT 'supplier',
    exposure_purity REAL NOT NULL DEFAULT 50,
    technical_barrier REAL NOT NULL DEFAULT 50,
    capacity_scarcity REAL NOT NULL DEFAULT 50,
    pricing_power REAL NOT NULL DEFAULT 50,
    customer_stickiness REAL NOT NULL DEFAULT 50,
    bottleneck_ownership REAL NOT NULL DEFAULT 50,
    mapping_confidence REAL NOT NULL DEFAULT 50,
    evidence_quality REAL NOT NULL DEFAULT 50,
    thesis TEXT NOT NULL DEFAULT '',
    disconfirming_conditions TEXT NOT NULL DEFAULT '',
    as_of_date TEXT NOT NULL,
    PRIMARY KEY (security_id, node_code),
    CHECK (exposure_purity BETWEEN 0 AND 100),
    CHECK (technical_barrier BETWEEN 0 AND 100),
    CHECK (capacity_scarcity BETWEEN 0 AND 100),
    CHECK (pricing_power BETWEEN 0 AND 100),
    CHECK (customer_stickiness BETWEEN 0 AND 100),
    CHECK (bottleneck_ownership BETWEEN 0 AND 100),
    CHECK (mapping_confidence BETWEEN 0 AND 100),
    CHECK (evidence_quality BETWEEN 0 AND 100)
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_code TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    title TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    source_tier TEXT NOT NULL DEFAULT 'secondary',
    as_of_date TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 50,
    summary TEXT NOT NULL DEFAULT '',
    CHECK (confidence BETWEEN 0 AND 100)
);

CREATE TABLE IF NOT EXISTS viewpoints (
    viewpoint_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_code TEXT NOT NULL,
    source_category TEXT NOT NULL DEFAULT 'research_note',
    source_name TEXT NOT NULL DEFAULT '',
    source_ref TEXT NOT NULL DEFAULT '',
    stance TEXT NOT NULL DEFAULT 'neutral',
    horizon TEXT NOT NULL DEFAULT 'unspecified',
    claim_type TEXT NOT NULL DEFAULT 'general',
    as_of_date TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 50,
    adoption_status TEXT NOT NULL DEFAULT 'watching',
    viewpoint TEXT NOT NULL DEFAULT '',
    verification_conditions TEXT NOT NULL DEFAULT '',
    disconfirming_conditions TEXT NOT NULL DEFAULT '',
    CHECK (confidence BETWEEN 0 AND 100)
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_code TEXT NOT NULL,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    expected_date TEXT,
    probability REAL NOT NULL DEFAULT 50,
    impact REAL NOT NULL DEFAULT 50,
    status TEXT NOT NULL DEFAULT 'watching',
    disconfirming_signal TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    CHECK (probability BETWEEN 0 AND 100),
    CHECK (impact BETWEEN 0 AND 100)
);

CREATE TABLE IF NOT EXISTS signal_observations (
    trade_date TEXT NOT NULL,
    security_code TEXT NOT NULL,
    source_pool TEXT NOT NULL,
    close REAL,
    rps_50 REAL,
    rps_120 REAL,
    rps_250 REAL,
    ret20 REAL,
    vs_ma60 REAL,
    is_mapped INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (trade_date, security_code, source_pool)
);

CREATE TABLE IF NOT EXISTS research_queue (
    security_code TEXT PRIMARY KEY,
    first_seen_date TEXT NOT NULL,
    last_seen_date TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    latest_pool TEXT NOT NULL,
    latest_rps_score REAL,
    status TEXT NOT NULL DEFAULT 'pending_mapping',
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_batches (
    batch_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_date TEXT NOT NULL,
    analysis_date TEXT NOT NULL,
    price_date TEXT,
    status TEXT NOT NULL DEFAULT 'prepared',
    source_count INTEGER NOT NULL DEFAULT 0,
    member_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_batch_members (
    batch_id TEXT NOT NULL REFERENCES research_batches(batch_id),
    security_id TEXT NOT NULL,
    name TEXT NOT NULL,
    market TEXT NOT NULL,
    asset_type TEXT NOT NULL DEFAULT 'stock',
    source_count INTEGER NOT NULL DEFAULT 1,
    source_ids TEXT NOT NULL DEFAULT '',
    source_refs TEXT NOT NULL DEFAULT '',
    primary_theme_code TEXT,
    primary_node_code TEXT,
    industry_name TEXT NOT NULL DEFAULT '',
    mapping_confidence REAL NOT NULL DEFAULT 50,
    classification_notes TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (batch_id, security_id),
    CHECK (mapping_confidence BETWEEN 0 AND 100)
);

CREATE TABLE IF NOT EXISTS research_batch_placements (
    batch_id TEXT NOT NULL REFERENCES research_batches(batch_id),
    security_id TEXT NOT NULL,
    node_code TEXT NOT NULL REFERENCES nodes(code),
    placement_type TEXT NOT NULL DEFAULT 'primary',
    mapping_confidence REAL NOT NULL DEFAULT 50,
    notes TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (batch_id, security_id, node_code),
    CHECK (mapping_confidence BETWEEN 0 AND 100)
);

CREATE TABLE IF NOT EXISTS trend_snapshots (
    batch_id TEXT NOT NULL REFERENCES research_batches(batch_id),
    trade_date TEXT NOT NULL,
    price_trade_date TEXT,
    security_id TEXT NOT NULL,
    data_status TEXT NOT NULL DEFAULT 'ok',
    close REAL,
    ret5 REAL,
    ret20 REAL,
    ret60 REAL,
    ret120 REAL,
    rps_50 REAL,
    rps_120 REAL,
    rps_250 REAL,
    vs_ma20 REAL,
    vs_ma60 REAL,
    vs_ma120 REAL,
    high_250_ratio REAL,
    drawdown20 REAL,
    amount20_avg REAL,
    in_l1 INTEGER NOT NULL DEFAULT 0,
    in_l2 INTEGER NOT NULL DEFAULT 0,
    in_huoche INTEGER NOT NULL DEFAULT 0,
    in_early INTEGER NOT NULL DEFAULT 0,
    in_early_l1 INTEGER NOT NULL DEFAULT 0,
    in_super INTEGER NOT NULL DEFAULT 0,
    trend_state TEXT NOT NULL DEFAULT 'unknown',
    entry_state TEXT NOT NULL DEFAULT 'data_gap',
    trend_score REAL,
    decision_reason TEXT NOT NULL DEFAULT '',
    computed_at TEXT NOT NULL,
    PRIMARY KEY (batch_id, trade_date, security_id)
);

CREATE INDEX IF NOT EXISTS idx_nodes_theme ON nodes(theme_code, layer_order);
CREATE INDEX IF NOT EXISTS idx_exposures_node ON company_exposures(node_code);
CREATE INDEX IF NOT EXISTS idx_companies_ts_code ON companies(ts_code);
CREATE INDEX IF NOT EXISTS idx_evidence_entity ON evidence(entity_type, entity_code);
CREATE INDEX IF NOT EXISTS idx_viewpoints_entity ON viewpoints(entity_type, entity_code, as_of_date);
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_type, entity_code);
CREATE INDEX IF NOT EXISTS idx_signals_security ON signal_observations(security_code, trade_date);
CREATE INDEX IF NOT EXISTS idx_research_queue_status ON research_queue(status, last_seen_date);
CREATE INDEX IF NOT EXISTS idx_batch_members_theme ON research_batch_members(batch_id, primary_theme_code);
CREATE INDEX IF NOT EXISTS idx_batch_placements_node ON research_batch_placements(batch_id, node_code);
CREATE INDEX IF NOT EXISTS idx_trend_snapshots_batch ON trend_snapshots(batch_id, trade_date, entry_state);
