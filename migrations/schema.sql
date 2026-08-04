-- Control Room — MySQL Schema
-- Safe to re-run: all statements use IF NOT EXISTS.
-- Run this after creating the database in cPanel.

SET NAMES utf8mb4;
SET time_zone = '+00:00';

CREATE TABLE IF NOT EXISTS users (
    id          INT UNSIGNED    AUTO_INCREMENT PRIMARY KEY,
    email       VARCHAR(255)    NOT NULL,
    name        VARCHAR(255),
    google_sub  VARCHAR(255),
    role        ENUM('super_admin','editor','member') NOT NULL DEFAULT 'member',
    is_active   TINYINT(1)      NOT NULL DEFAULT 1,
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at DATETIME,
    UNIQUE KEY uq_email (email),
    UNIQUE KEY uq_google_sub (google_sub)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Scoped permissions for editor-role users (per subproject)
CREATE TABLE IF NOT EXISTS user_project_permissions (
    id           INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    user_id      INT UNSIGNED NOT NULL,
    project_slug VARCHAR(100) NOT NULL,
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user_project (user_id, project_slug),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Per-user notification preferences (fully opt-in, per spec decision 12)
CREATE TABLE IF NOT EXISTS notification_preferences (
    id                   INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    user_id              INT UNSIGNED NOT NULL,
    cadence              ENUM('instant','daily','weekly','biweekly') NOT NULL DEFAULT 'daily',
    holds_enabled        TINYINT(1) NOT NULL DEFAULT 1,
    approvals_enabled    TINYINT(1) NOT NULL DEFAULT 1,
    pitch_updates_enabled TINYINT(1) NOT NULL DEFAULT 1,
    updated_at           DATETIME   NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user (user_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Global app settings key-value store (including mode: test/live)
CREATE TABLE IF NOT EXISTS app_settings (
    key_name   VARCHAR(100) PRIMARY KEY,
    value      TEXT,
    updated_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    updated_by INT UNSIGNED,
    FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Hold dates wrapping Google Calendar events (Session H wires the gcal_event_id)
CREATE TABLE IF NOT EXISTS holds (
    id            INT UNSIGNED  AUTO_INCREMENT PRIMARY KEY,
    gcal_event_id VARCHAR(255),
    title         VARCHAR(500)  NOT NULL,
    hold_date     DATE          NOT NULL,
    end_date      DATE,
    status        ENUM('tentative','confirmed','cancelled') NOT NULL DEFAULT 'tentative',
    project_slug  VARCHAR(100),
    created_by    INT UNSIGNED,
    created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_hold_date (hold_date),
    KEY idx_hold_status (status),
    KEY idx_gcal_event_id (gcal_event_id),
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Who can see and get notified about a hold
CREATE TABLE IF NOT EXISTS hold_participants (
    id      INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    hold_id INT UNSIGNED NOT NULL,
    user_id INT UNSIGNED NOT NULL,
    UNIQUE KEY uq_hold_user (hold_id, user_id),
    FOREIGN KEY (hold_id) REFERENCES holds(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Threaded notes on a hold
CREATE TABLE IF NOT EXISTS hold_notes (
    id             INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    hold_id        INT UNSIGNED NOT NULL,
    author_id      INT UNSIGNED,
    parent_note_id INT UNSIGNED,
    body           TEXT         NOT NULL,
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_hold_notes_hold (hold_id),
    FOREIGN KEY (hold_id)        REFERENCES holds(id)      ON DELETE CASCADE,
    FOREIGN KEY (author_id)      REFERENCES users(id)      ON DELETE SET NULL,
    FOREIGN KEY (parent_note_id) REFERENCES hold_notes(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Immutable audit trail: what was approved, by whom, exact content (spec: "Approval audit trail")
CREATE TABLE IF NOT EXISTS approval_logs (
    id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    approver_id INT UNSIGNED,
    action      VARCHAR(100) NOT NULL,
    entity_type VARCHAR(100) NOT NULL,
    entity_id   VARCHAR(255),
    details     JSON,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_approver (approver_id),
    KEY idx_entity (entity_type, entity_id),
    KEY idx_approval_created (created_at),
    FOREIGN KEY (approver_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Per-platform call tracking for the shared rate-limit layer
CREATE TABLE IF NOT EXISTS api_rate_tracking (
    id           INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    platform     VARCHAR(100) NOT NULL,
    window_start DATETIME     NOT NULL,
    call_count   INT UNSIGNED NOT NULL DEFAULT 0,
    KEY idx_platform_window (platform, window_start)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Outbound API task queue (processed by cron → flask process-queue)
CREATE TABLE IF NOT EXISTS api_task_queue (
    id            INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    platform      VARCHAR(100) NOT NULL,
    task_type     VARCHAR(100) NOT NULL,
    payload       JSON         NOT NULL,
    status        ENUM('pending','processing','completed','failed','cancelled') NOT NULL DEFAULT 'pending',
    priority      TINYINT UNSIGNED NOT NULL DEFAULT 5,
    retry_count   TINYINT UNSIGNED NOT NULL DEFAULT 0,
    max_retries   TINYINT UNSIGNED NOT NULL DEFAULT 3,
    scheduled_at  DATETIME,
    started_at    DATETIME,
    completed_at  DATETIME,
    error_message TEXT,
    created_by    INT UNSIGNED,
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_queue_status_scheduled (status, scheduled_at),
    KEY idx_queue_platform (platform),
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
