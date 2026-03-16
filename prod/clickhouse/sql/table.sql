CREATE TABLE cortex.cortex_logs ON CLUSTER cluster01 (
    timestamp DateTime64(3, 'UTC') CODEC(Delta, ZSTD(1)),
    level LowCardinality(String),
    service_name LowCardinality(String),
    environment LowCardinality(String),
    hostname LowCardinality(String),
    message String CODEC(ZSTD(3)),
    log_type LowCardinality(String) DEFAULT 'internal',

    -- Request tracking
    correlation_id Nullable (String) CODEC (ZSTD (1)),

    -- HTTP details
    http_method LowCardinality (String),
    http_path String CODEC (ZSTD (1)),
    http_status Nullable (UInt16) CODEC (T64, ZSTD (1)),
    http_duration_ms Nullable (UInt32) CODEC (T64, ZSTD (1)),

    -- User context
    user_id Nullable (String) CODEC (ZSTD (1)),
    tenant_id Nullable (String) CODEC (ZSTD (1)),
    sub_tenant_id Nullable (String) CODEC (ZSTD (1)),

    -- Request data
    request_body Nullable (String) CODEC (ZSTD (3)),

    -- Client info
    client_ip Nullable (IPv6), user_agent LowCardinality (String),

    -- Error details
    error_type LowCardinality (String),
    error_message String CODEC (ZSTD (3)),
    stack_trace String CODEC (ZSTD (3)),

    -- Operational
    operation LowCardinality (String),
    result_count Nullable (UInt32),
    event LowCardinality (String),

    -- Kubernetes Context (FIXED: DEFAULT moved before CODEC)
    k8s_cluster LowCardinality (String) DEFAULT 'local-dev', -- No codec needed for LC usually, but if used, put after default
    k8s_node_name LowCardinality (String) DEFAULT 'local-dev',
    k8s_namespace LowCardinality (String) DEFAULT 'local-dev',
    k8s_pod_name String DEFAULT 'local-dev' CODEC (ZSTD (1)),
    k8s_pod_uid String DEFAULT 'local-dev' CODEC (ZSTD (1)),
    k8s_container_name LowCardinality (String) DEFAULT 'local-dev',
    k8s_container_image String DEFAULT 'local-dev' CODEC (ZSTD (1)),

    -- K8s Labels
    k8s_labels Map ( LowCardinality (String), String ) CODEC (ZSTD (1)),

    -- Vector Internal
    agent_version LowCardinality (String),

    -- Attributes
    attributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),

    INDEX idx_message message TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 1,
    INDEX idx_correlation_id correlation_id TYPE bloom_filter GRANULARITY 4,
    INDEX idx_user_id user_id TYPE bloom_filter GRANULARITY 4,
    INDEX idx_http_path http_path TYPE bloom_filter GRANULARITY 4
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/cortex/cortex_logs', '{replica}')
PARTITION BY toYYYYMM(timestamp)
ORDER BY (service_name, environment, level, timestamp)
TTL 
    toDateTime(timestamp) + INTERVAL 28 DAY WHERE level IN ('debug', 'info'),
    toDateTime(timestamp) + INTERVAL 30 DAY
;
