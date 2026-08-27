-- The Portal — MySQL Schema
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

-- Local cache of HubSpot Company objects (populated by `flask sync-hubspot` / nightly cron).
-- The Pitch Machine kanban reads from here; never hits HubSpot on page load.
-- reach_out_* are DATE values (planned send dates), not timestamps.
CREATE TABLE IF NOT EXISTS hubspot_companies (
    id                  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    hubspot_id          VARCHAR(50)  NOT NULL,
    name                VARCHAR(500) NOT NULL DEFAULT '',
    description         LONGTEXT,
    website             VARCHAR(500),
    domain              VARCHAR(255),
    hubspot_owner_id    VARCHAR(50),
    reach_out_1         DATE,
    reach_out_2_checkin DATE,
    reach_out_2         DATE,
    hs_lead_status      VARCHAR(100),
    lifecyclestage      VARCHAR(100),
    notes_last_contacted DATETIME,
    hs_lastmodifieddate  DATETIME,
    last_synced_at      DATETIME     NOT NULL,
    UNIQUE KEY uq_hubspot_id (hubspot_id),
    KEY idx_hsc_reach_out_1 (reach_out_1),
    KEY idx_hsc_lead_status (hs_lead_status),
    KEY idx_hsc_synced (last_synced_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Warm contact flags (HubSpot is at 10/10 custom property cap — no room for a new property).
-- Populated during the one-time historical gig spreadsheet import (Session F).
CREATE TABLE IF NOT EXISTS warm_contacts (
    id                  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    hubspot_contact_id  VARCHAR(100) NOT NULL,
    tagged_at           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    tagged_by           INT UNSIGNED,
    UNIQUE KEY uq_hs_contact (hubspot_contact_id),
    FOREIGN KEY (tagged_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Dropbox file cache for knowledge-sync (PITCH_MACHINE_RULES.md, research playbook, etc.)
CREATE TABLE IF NOT EXISTS dropbox_sync (
    id        INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    path      VARCHAR(500)  NOT NULL,
    content   LONGTEXT,
    synced_at DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_path (path)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Touch 1 pitch drafts awaiting Erich's explicit approval (Touch 2/3 go through api_task_queue)
-- hubspot_contact_id stores the HubSpot *company* ID — festivals are COMPANY objects.
-- The column name is a legacy artifact; don't rename.
CREATE TABLE IF NOT EXISTS pitch_approvals (
    id                  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    hubspot_contact_id  VARCHAR(100) NOT NULL,
    company_name        VARCHAR(500),
    pitch_type          VARCHAR(50)  NOT NULL DEFAULT 'Festival',
    touch_number        TINYINT UNSIGNED NOT NULL DEFAULT 1,
    draft_subject       VARCHAR(500),
    draft_body          TEXT         NOT NULL,
    research_notes      MEDIUMTEXT,
    to_email            VARCHAR(500),
    cc_email            VARCHAR(500),
    status              ENUM('pending','approved','rejected','sent') NOT NULL DEFAULT 'pending',
    approved_by         INT UNSIGNED,
    approved_at         DATETIME,
    sent_at             DATETIME,
    error_message       TEXT,
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_pa_status (status),
    KEY idx_pa_contact (hubspot_contact_id),
    FOREIGN KEY (approved_by) REFERENCES users(id) ON DELETE SET NULL
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

-- ─── Item 2 migration: pitch type configuration ──────────────────────────────
-- db.create_all() on startup creates this automatically on fresh installs.
-- Run manually on production if upgrading an existing install before deploying item 2 code.
CREATE TABLE IF NOT EXISTS pitch_type_configs (
    id                   INT UNSIGNED  AUTO_INCREMENT PRIMARY KEY,
    name                 VARCHAR(100)  NOT NULL,
    archive_dropbox_path VARCHAR(255)  NOT NULL,
    prompt_template      MEDIUMTEXT    NOT NULL,
    badge_color          VARCHAR(7)    NOT NULL DEFAULT '#888888',
    active               TINYINT(1)   NOT NULL DEFAULT 1,
    sort_order           INT           NOT NULL DEFAULT 0,
    created_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_ptc_name (name),
    KEY idx_ptc_active_sort (active, sort_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ─── Session G-1: pitch_targets materialized sync table ─────────────────────
-- db.create_all() on startup creates this automatically on fresh installs.
-- Run manually on production after pulling the G-1 commit, before restarting.
CREATE TABLE IF NOT EXISTS pitch_targets (
    id                  INT UNSIGNED  AUTO_INCREMENT PRIMARY KEY,
    hubspot_id          VARCHAR(50),
    name                VARCHAR(500)  NOT NULL,

    source_hubspot      TINYINT(1)    NOT NULL DEFAULT 0,
    source_spreadsheet  TINYINT(1)    NOT NULL DEFAULT 0,
    source_queue_csv    TINYINT(1)    NOT NULL DEFAULT 0,

    stage               VARCHAR(50)   NOT NULL DEFAULT 'needs-outreach',
    stage_conflict      TINYINT(1)    NOT NULL DEFAULT 0,
    conflict_note       VARCHAR(500),

    pitch_type          VARCHAR(100),
    website             VARCHAR(500),
    description         TEXT,
    reach_out_1         DATE,
    submission_deadline DATE,

    spreadsheet_status  VARCHAR(500),
    spreadsheet_row     INT,
    hs_lead_status      VARCHAR(100),
    queue_csv_status    VARCHAR(50),

    email_address       VARCHAR(500),
    not_a_fit           TINYINT(1)    NOT NULL DEFAULT 0,
    not_a_fit_reason    VARCHAR(500),

    last_synced_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uq_pt_hubspot_id (hubspot_id),
    KEY idx_pt_stage      (stage),
    KEY idx_pt_name       (name(100)),
    KEY idx_pt_not_a_fit  (not_a_fit)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ─── Session E migration: pitch_type column on pitch_approvals ───────────────
-- ALTER TABLE pitch_approvals
--   ADD COLUMN IF NOT EXISTS pitch_type VARCHAR(50) NOT NULL DEFAULT 'Festival'
--     AFTER company_name;

-- ─── Session D migration: new columns on pitch_approvals ──────────────────────
-- Run these on an existing database where pitch_approvals was created before Session D.
-- The CREATE TABLE above already includes these columns for fresh installs.
-- MySQL 8.0.3+ supports IF NOT EXISTS on ADD COLUMN; on older versions, omit the clause
-- (the statements are idempotent if columns already exist from a prior deploy).
--
-- ALTER TABLE pitch_approvals
--   ADD COLUMN IF NOT EXISTS company_name   VARCHAR(500)  AFTER hubspot_contact_id,
--   ADD COLUMN IF NOT EXISTS research_notes MEDIUMTEXT    AFTER draft_body,
--   ADD COLUMN IF NOT EXISTS to_email       VARCHAR(500)  AFTER research_notes,
--   ADD COLUMN IF NOT EXISTS cc_email       VARCHAR(500)  AFTER to_email;

-- ─── Phase 1: Wheel — hubspot_owner_id on pitch_targets ──────────────────────
-- Run on production before deploying Phase 1 code.
-- ALTER TABLE pitch_targets
--   ADD COLUMN IF NOT EXISTS hubspot_owner_id VARCHAR(50) NULL AFTER hs_lead_status;

-- ─── Phase 1: Wheel — is_cyclical on pitch_type_configs ──────────────────────
-- TRUE (default) = annual recurrence → renders the Wheel.
-- FALSE = one-off / tour-anchored → renders the linear timeline.
-- Run on production before deploying Phase 1 code.
-- ALTER TABLE pitch_type_configs
--   ADD COLUMN IF NOT EXISTS is_cyclical BOOLEAN NOT NULL DEFAULT TRUE AFTER active;
