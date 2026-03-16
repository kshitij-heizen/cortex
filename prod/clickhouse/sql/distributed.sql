CREATE TABLE IF NOT EXISTS cortex.cortex_logs_distributed ON CLUSTER cluster01 
AS cortex.cortex_logs
ENGINE = Distributed(cluster01, cortex, cortex_logs, rand());
