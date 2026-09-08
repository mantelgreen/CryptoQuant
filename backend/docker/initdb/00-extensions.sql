-- TimescaleDB + pgvector 扩展
-- 注意：扩展必须以超级用户执行。docker-compose 中已用 root。

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 给应用账号授权
GRANT ALL PRIVILEGES ON DATABASE cryptoquant_db TO root;
