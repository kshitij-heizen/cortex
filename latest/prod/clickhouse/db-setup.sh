# 1. Create database
kubectl exec -it chi-cluster01-cluster01-0-0-0 -n clickhouse \
  -- clickhouse-client -q "$(cat prod/clickhouse/sql/database.sql)"

# 2. Create replicated table
kubectl exec -it chi-cluster01-cluster01-0-0-0 -n clickhouse \
  -- clickhouse-client --multiquery < prod/clickhouse/sql/table.sql

# 3. Create distributed table
kubectl exec -it chi-cluster01-cluster01-0-0-0 -n clickhouse \
  -- clickhouse-client -q "$(cat prod/clickhouse/sql/distributed.sql)"

# 4. Check
kubectl exec -it chi-cluster01-cluster01-0-0-0 -n clickhouse -- clickhouse-client -q "SHOW TABLES FROM cortex"