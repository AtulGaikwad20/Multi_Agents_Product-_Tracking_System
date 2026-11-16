-- AI Agent Playground: research history shared across devices.
-- Run this once on your MySQL database. It only ADDS a table; it does not touch
-- existing tables such as `users` or a legacy `products` table.
CREATE TABLE IF NOT EXISTS research_runs (
    run_id         CHAR(32)      NOT NULL PRIMARY KEY,
    user_id        INT           NOT NULL,
    query_text     VARCHAR(500)  NOT NULL,
    language       VARCHAR(12)   NOT NULL DEFAULT 'en',
    status         VARCHAR(20)   NOT NULL,
    parent_run_id  CHAR(32)      NULL,
    location_label VARCHAR(200)  NULL,
    record_json    LONGTEXT      NOT NULL,
    created_at     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_runs_user_updated (user_id, updated_at),
    CONSTRAINT fk_runs_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
