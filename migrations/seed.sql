-- Seed default app settings.
-- INSERT IGNORE is safe to re-run: skips rows that already exist.

INSERT IGNORE INTO app_settings (key_name, value) VALUES
  ('mode',    'test'),
  ('version', '0.1.0');
