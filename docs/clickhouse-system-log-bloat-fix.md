# ClickHouse System Log Table Disk Bloat Fix

## Problem
ClickHouse system tables (`trace_log`, `text_log`, `metric_log`, etc.) repeatedly fill up the disk (~32-48GB), causing 100% disk usage and pod crashes.

## Root Cause Analysis

### 1. Incorrect Configuration Method
The Altinity clickhouse-operator's `spec.configuration.settings` maps to **query-level settings**, NOT server-level config. Putting entries like:
```yaml
settings:
  trace_log/enabled: false
  text_log/enabled: false
```
has **NO effect** on system log tables because these are server-level configurations.

### 2. Operator Auto-Generated Configs
The operator auto-generates `01-clickhouse-0X-*.xml` files with 30-day TTL for:
- `trace_log` (main culprit - grows to 40GB+)
- `query_log` 
- `part_log`

### 3. User vs Server Settings
`max_memory_usage` and similar are **user-level settings** that must go in `spec.configuration.users` (e.g., `default/max_memory_usage`), NOT in `spec.configuration.settings`. Putting them in settings causes:
```
Code: 137. DB::Exception: UNKNOWN_ELEMENT_IN_CONFIG
```

## Solution

### 1. Use `spec.configuration.files` for Server-Level Config
Add a `files` section that injects server-level XML into `/etc/clickhouse-server/config.d/`:

```yaml
configuration:
  files:
    # Uses 02- prefix to override operator's auto-generated 01- configs
    02-disable-system-logs.xml: |
      <yandex>
          <trace_log remove="1"/>
          <text_log remove="1"/>
          <metric_log remove="1"/>
          <asynchronous_metric_log remove="1"/>
          <query_thread_log remove="1"/>
          <session_log remove="1"/>
          <processors_profile_log remove="1"/>
          <query_log replace="1">
              <database>system</database>
              <table>query_log</table>
              <engine>Engine = MergeTree PARTITION BY event_date ORDER BY event_time TTL event_date + INTERVAL 3 DAY</engine>
              <flush_interval_milliseconds>7500</flush_interval_milliseconds>
          </query_log>
          <part_log replace="1">
              <database>system</database>
              <table>part_log</table>
              <engine>Engine = MergeTree PARTITION BY event_date ORDER BY event_time TTL event_date + INTERVAL 3 DAY</engine>
              <flush_interval_milliseconds>7500</flush_interval_milliseconds>
          </part_log>
      </yandex>
```

**Key points:**
- Use `remove="1"` to completely disable bloated tables
- Use `replace="1"` to keep essential tables with shorter TTL (3 days instead of 30)
- Use `02-` prefix to override the operator's `01-` auto-generated configs

### 2. Move Memory Settings to Users Section
Move user-level settings from `settings` to `users`:

```yaml
configuration:
  users:
    default/max_memory_usage: 8000000000
    default/max_memory_usage_for_user: 8000000000
    default/max_memory_usage_for_all_queries: 16000000000
    default/memory_overcommit_ratio_denominator: 2
```

### 3. Clean Existing Data
Use `TRUNCATE` (not `DELETE`) to clean existing data:

```sql
-- TRUNCATE is immediate, DELETE is a slow async mutation that can fail on full disks
TRUNCATE TABLE IF EXISTS system.trace_log;
TRUNCATE TABLE IF EXISTS system.text_log;
TRUNCATE TABLE IF EXISTS system.metric_log;
TRUNCATE TABLE IF EXISTS system.asynchronous_metric_log;
TRUNCATE TABLE IF EXISTS system.query_thread_log;
TRUNCATE TABLE IF EXISTS system.session_log;
TRUNCATE TABLE IF EXISTS system.processors_profile_log;
```

## Implementation Steps

1. **Update ClickHouse config** with the `files` section and move memory settings to `users`
2. **Apply config**: `kubectl apply -f prod/clickhouse/clickhouse.yaml`
3. **Wait for pods to restart** (operator will roll both replicas)
4. **TRUNCATE existing data** on both replicas:
   ```bash
   kubectl exec -n clickhouse chi-cluster01-cluster01-0-0-0 -- clickhouse-client --user admin --password 'fkdsjoij23e4' --query "TRUNCATE TABLE IF EXISTS system.trace_log; TRUNCATE TABLE IF EXISTS system.text_log; ..."
   kubectl exec -n clickhouse chi-cluster01-cluster01-0-1-0 -- clickhouse-client --user admin --password 'fkdsjoij23e4' --query "TRUNCATE TABLE IF EXISTS system.trace_log; TRUNCATE TABLE IF EXISTS system.text_log; ..."
   ```
5. **Verify disk space**:
   ```sql
   SELECT name, formatReadableSize(free_space) as free, formatReadableSize(total_space) as total, round(100 * (1 - free_space/total_space), 2) as used_percent FROM system.disks;
   ```

## Results

Before fix:
- Disk usage: 100% (0 free)
- `trace_log`: 40 GB
- `text_log`: 7.3 GB
- Other system logs: ~1.5 GB

After fix:
- Disk usage: ~1% (96-97 GB free)
- System tables: < 500 MB total
- All pods running normally

## Prevention

The fix prevents recurrence by:
- Disabling bloated system logs at server level
- Keeping essential logs with short TTL (3 days)
- Proper configuration structure that survives pod restarts

## Access Details

- **Namespace**: `clickhouse`
- **Pods**: `chi-cluster01-cluster01-0-0-0`, `chi-cluster01-cluster01-0-1-0`
- **Admin credentials**: `admin/fkdsjoij23e4`
- **Default credentials**: `default/gdfsgrt3t2234fd`

## References

- [Altinity ClickHouse Operator Documentation](https://github.com/Altinity/clickhouse-operator)
- [ClickHouse System Tables Documentation](https://clickhouse.com/docs/en/operations/system-tables)
