from flask import Flask

from .config import Config
from .extensions import csrf, db, login_manager, oauth


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    oauth.init_app(app)

    oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

    login_manager.login_view = 'auth.login'
    login_manager.login_message = ''

    @login_manager.user_loader
    def load_user(user_id):
        from .models.user import User
        return User.query.get(int(user_id))

    @app.context_processor
    def inject_globals():
        from app.core.mode import get_mode
        return {'mode': get_mode()}

    from .auth.routes import auth_bp
    from .main.routes import main_bp
    from .pitch_machine.routes import pm_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(pm_bp)

    with app.app_context():
        _auto_init_db(app)

    @app.cli.command('init-db')
    def init_db_command():
        """Create all tables and seed default settings. Safe to re-run."""
        _auto_init_db(app)
        print('Database initialised and default settings seeded.')

    @app.cli.command('sync-hubspot')
    def sync_hubspot_command():
        """Pull all HubSpot companies into the local cache. Safe to re-run."""
        from app.integrations.hubspot import sync_companies_to_cache, HubSpotError
        try:
            count = sync_companies_to_cache()
            print(f'Synced {count} companies from HubSpot.')
        except HubSpotError as e:
            print(f'Error: {e}')

    @app.cli.command('sync-pitch-targets')
    def sync_pitch_targets_command():
        """Rebuild pitch_targets from HubSpot cache, spreadsheet XLSX, and queue CSV."""
        from app.pitch_machine.pitch_target_sync import sync_pitch_targets
        try:
            result = sync_pitch_targets()
            print(str(result))
            for w in result.warnings:
                print(f'  WARNING: {w}')
        except Exception as e:
            print(f'Error: {e}')
            raise

    @app.cli.command('process-queue')
    def process_queue_command():
        """Process pending zoho_mail send tasks. Run by cron every few minutes."""
        from datetime import datetime
        from app.integrations.zoho_mail import ZohoError, send_email
        from app.models.pitch import PitchApproval
        from app.models.queue import APITaskQueue

        tasks = (
            APITaskQueue.query
            .filter_by(platform='zoho_mail', status='pending')
            .filter(APITaskQueue.retry_count < APITaskQueue.max_retries)
            .filter(
                (APITaskQueue.scheduled_at == None) |
                (APITaskQueue.scheduled_at <= datetime.utcnow())
            )
            .order_by(APITaskQueue.created_at)
            .limit(10)
            .all()
        )

        if not tasks:
            print('No pending tasks.')
            return

        sent = 0
        failed = 0

        for task in tasks:
            task.status     = 'processing'
            task.started_at = datetime.utcnow()
            db.session.commit()

            payload = task.payload or {}

            # ── C2: narrow except to send_email() only ───────────────────────────
            # Exceptions in the success block (post-send) must NOT trigger a retry,
            # since the email was already delivered.
            try:
                send_email(
                    to_address = payload['to_email_actual'],
                    subject    = payload['subject'],
                    body_html  = payload['body'],
                    cc_address = payload.get('cc_email') or None,
                )
            except Exception as e:
                task.retry_count  += 1
                task.error_message = str(e)
                task.status = (
                    'pending' if task.retry_count < task.max_retries else 'failed'
                )
                failed += 1
                print(f'  Failed: {e}')
                db.session.commit()
                continue

            # ── C1: commit task=completed before touching approval/HubSpot ───────
            # If anything below fails, the task is already durably completed and
            # won't be re-sent on the next cron tick.
            task.status       = 'completed'
            task.completed_at = datetime.utcnow()
            db.session.commit()

            # ── Update approval and HubSpot (best-effort; send already recorded) ──
            approval = PitchApproval.query.get(payload.get('pitch_approval_id'))
            if approval:
                approval.status  = 'sent'
                approval.sent_at = datetime.utcnow()

                if approval.hubspot_contact_id:
                    try:
                        from app.integrations.hubspot import HubSpotClient, HubSpotError
                        from app.models.hubspot_cache import HubSpotCompany
                        updates = {'hs_lead_status': 'ATTEMPTED_TO_CONTACT'}
                        if approval.send_date:
                            updates['reach_out_1'] = approval.send_date.isoformat()
                        HubSpotClient().update_company(approval.hubspot_contact_id, updates)
                        company = HubSpotCompany.query.filter_by(
                            hubspot_id=approval.hubspot_contact_id
                        ).first()
                        if company:
                            company.hs_lead_status = 'ATTEMPTED_TO_CONTACT'
                            if approval.send_date and not company.reach_out_1:
                                company.reach_out_1 = approval.send_date
                    except Exception as hs_e:
                        print(f'  HubSpot write failed for {approval.company_name}: {hs_e}')

            sent += 1
            recipient     = payload.get('to_email_actual', '?')
            intended      = payload.get('to_email_intended', '')
            redirect_note = f' (redirected from {intended})' if payload.get('was_redirected') else ''
            print(f'  Sent → {recipient}{redirect_note}')

            db.session.commit()  # approval.status='sent' + cache updates

        print(f'Done — {sent} sent, {failed} failed.')

    @app.cli.command('debug-zoho-sent')
    def debug_zoho_sent_command():
        """
        Diagnostic: dump Zoho folders + raw Sent messages without any filtering.
        Helps identify why a specific message is missing from scan-sent output.
        """
        import re as _re
        import requests as _requests
        from app.integrations.zoho_mail import (
            ZohoError, _get_access_token, get_account_id,
        )

        access_token = _get_access_token()
        account_id   = get_account_id(access_token)
        headers      = {'Authorization': f'Zoho-oauthtoken {access_token}'}

        # 1. List all folders so we can confirm which one we're reading
        folders_resp = _requests.get(
            f'https://mail.zoho.com/api/accounts/{account_id}/folders',
            headers=headers, timeout=15,
        )
        folders_resp.raise_for_status()
        print('=== ZOHO FOLDERS ===')
        for f in folders_resp.json().get('data', []):
            print(f'  id={f["folderId"]}  name={f.get("folderName")!r}  count={f.get("messageCount")}')

        sent_folder_id = next(
            (f['folderId'] for f in folders_resp.json().get('data', [])
             if f.get('folderName', '').lower() == 'sent'),
            None,
        )
        if not sent_folder_id:
            print('ERROR: No folder named "sent" found.')
            return

        # 2. Fetch raw messages — no timestamp filter, no matching
        print(f'\n=== RAW SENT MESSAGES (folder {sent_folder_id}, first 200) ===')
        resp = _requests.get(
            f'https://mail.zoho.com/api/accounts/{account_id}/messages/view',
            headers=headers,
            params={'folderId': sent_folder_id, 'limit': 200, 'start': 0},
            timeout=30,
        )
        resp.raise_for_status()
        raw_msgs = resp.json().get('data', [])
        print(f'API returned {len(raw_msgs)} messages in this page.')

        email_pat = _re.compile(r'[\w._%+\-]+@[\w.\-]+\.[a-zA-Z]{2,}')
        print('\nAll messages (ts / to_address / subject):')
        for msg in raw_msgs:
            ts      = msg.get('sentDateInGMT') or msg.get('receivedTime') or '0'
            to_raw  = msg.get('toAddress', '')
            m       = email_pat.search(to_raw)
            to_addr = m.group(0).lower() if m else to_raw[:60]
            subj    = msg.get('subject', '')[:70]
            print(f'  ts={ts}  to={to_addr}  subj={subj!r}')

    @app.cli.command('scan-sent')
    def scan_sent_command():
        """
        Scan Zoho Sent folder and reconcile against pitch_approvals.
        Marks matched approvals as sent, writes ATTEMPTED_TO_CONTACT to HubSpot.
        Run manually after sending pitches outside the portal, or to backfill.
        """
        from datetime import datetime
        from app.integrations.zoho_mail import ZohoError, list_sent_messages
        from app.integrations.hubspot import HubSpotClient, HubSpotError
        from app.models.pitch import PitchApproval
        from app.models.hubspot_cache import HubSpotCompany

        try:
            messages = list_sent_messages(days_back=90)
        except ZohoError as e:
            print(f'Error fetching sent messages: {e}')
            return

        print(f'Found {len(messages)} sent messages in last 90 days.')

        matched   = 0
        unmatched = []

        for msg in messages:
            to_email = msg['to_address']
            if not to_email:
                continue

            approval = (
                PitchApproval.query
                .filter(
                    db.func.lower(PitchApproval.to_email) == to_email,
                    PitchApproval.status.in_(['pending', 'approved']),
                )
                .first()
            )

            if not approval:
                unmatched.append(msg)
                continue

            approval.status  = 'sent'
            approval.sent_at = msg['sent_at'] or datetime.utcnow()

            if approval.hubspot_contact_id:
                try:
                    updates = {'hs_lead_status': 'ATTEMPTED_TO_CONTACT'}
                    if approval.send_date:
                        updates['reach_out_1'] = approval.send_date.isoformat()
                    HubSpotClient().update_company(approval.hubspot_contact_id, updates)
                    company = HubSpotCompany.query.filter_by(
                        hubspot_id=approval.hubspot_contact_id
                    ).first()
                    if company:
                        company.hs_lead_status = 'ATTEMPTED_TO_CONTACT'
                        if approval.send_date and not company.reach_out_1:
                            company.reach_out_1 = approval.send_date
                except HubSpotError as e:
                    print(f'  HubSpot write failed for {approval.company_name}: {e}')

            db.session.commit()
            matched += 1
            print(f'  Matched: {approval.company_name} → {to_email}')

        print(f'\n{matched} matched and updated.')

        # ── Phase 2: propose subject-line matches — NO auto-write, review required ──
        # Prints proposed matches for your review. Confirmed matches: drag the card
        # from QUEUED → SENT on the board (writes ATTEMPTED_TO_CONTACT directly).
        proposed = []
        still_unmatched = []

        if unmatched:
            import re as _re
            _SUBJECT_PATTERNS = [
                _re.compile(r'Orchestra\s+GOLD\s+[✱✱*]\s+(.+?)\s+\d{4}', _re.IGNORECASE),
                _re.compile(r'Orchestra\s+GOLD\s+[-–—]\s+(.+?)\s+\d{4}', _re.IGNORECASE),
                _re.compile(r'Orchestra\s+GOLD\s+[|]\s+(.+)', _re.IGNORECASE),
            ]

            all_companies = HubSpotCompany.query.filter_by(is_duplicate=False).all()

            def _extract_name(subject):
                for pat in _SUBJECT_PATTERNS:
                    m = pat.search(subject)
                    if m:
                        return m.group(1).strip()
                return None

            def _find_company(extracted):
                name_lower = extracted.lower()
                exact = [c for c in all_companies if c.name.lower() == name_lower]
                if exact:
                    return exact[0]
                subs = [c for c in all_companies
                        if name_lower in c.name.lower() or c.name.lower() in name_lower]
                return subs[0] if len(subs) == 1 else None

            for msg in unmatched:
                subject   = msg['subject']
                extracted = _extract_name(subject)
                company   = _find_company(extracted) if extracted else None

                if company is None:
                    still_unmatched.append(msg)
                    continue

                if company.hs_lead_status == 'ATTEMPTED_TO_CONTACT':
                    still_unmatched.append(msg)  # already SENT, no action needed
                    continue

                proposed.append((company, msg))

        if proposed:
            print(f'\nPhase 2 — {len(proposed)} proposed match(es) (NOT applied — review and drag to SENT on the board to confirm):')
            for company, msg in proposed:
                print(f'  ? {company.name}  ←  "{msg["subject"][:65]}"')

        if still_unmatched:
            import os as _os
            # Deduplicate by (to_address, subject) — Zoho sometimes returns one
            # message per recipient (To + CC), producing identical-looking entries.
            seen_pairs: set = set()
            deduped = []
            for msg in still_unmatched:
                key = (msg['to_address'], msg['subject'])
                if key not in seen_pairs:
                    seen_pairs.add(key)
                    deduped.append(msg)

            report_path = _os.path.expanduser('~/scan_sent_unmatched.txt')
            with open(report_path, 'w') as _f:
                _f.write(f'scan-sent unmatched — {len(deduped)} messages '
                         f'({len(still_unmatched) - len(deduped)} duplicates collapsed)\n\n')
                for msg in deduped:
                    _f.write(f'{msg["to_address"]}\t{msg["subject"]}\t{msg.get("sent_at","")}\n')
            print(f'\n{len(deduped)} unmatched ({len(still_unmatched) - len(deduped)} dupes collapsed) '
                  f'— written to: {report_path}')
            print('First 50:')
            for msg in deduped[:50]:
                print(f'  → {msg["to_address"]}: {msg["subject"][:65]}')
            if len(deduped) > 50:
                print(f'  ... (see {report_path} for all)')

        # ── Phase 3: fix portal pitches where send succeeded but HubSpot write failed ──
        # PitchApproval.status='sent' means process-queue delivered the email.
        # If the subsequent HubSpot write failed, the board still shows QUEUED.
        # Find those and write ATTEMPTED_TO_CONTACT now.
        hs_fixed    = 0
        hs_skipped  = 0
        sent_approvals = PitchApproval.query.filter_by(status='sent').all()
        for appr in sent_approvals:
            if not appr.hubspot_contact_id:
                hs_skipped += 1
                continue
            company = HubSpotCompany.query.filter_by(
                hubspot_id=appr.hubspot_contact_id
            ).first()
            if not company or company.hs_lead_status == 'ATTEMPTED_TO_CONTACT':
                continue
            try:
                updates = {'hs_lead_status': 'ATTEMPTED_TO_CONTACT'}
                if appr.send_date and not company.reach_out_1:
                    updates['reach_out_1'] = appr.send_date.isoformat()
                HubSpotClient().update_company(appr.hubspot_contact_id, updates)
                company.hs_lead_status = 'ATTEMPTED_TO_CONTACT'
                if appr.send_date and not company.reach_out_1:
                    company.reach_out_1 = appr.send_date
                db.session.commit()
                hs_fixed += 1
                print(f'  Phase 3 fixed: {appr.company_name} (approval #{appr.id})')
            except HubSpotError as e:
                print(f'  Phase 3 HubSpot write failed for {appr.company_name}: {e}')

        total = len(sent_approvals)
        linked = total - hs_skipped
        print(f'\nPhase 3: {total} sent portal approvals total.')
        print(f'  {hs_skipped} skipped — no hubspot_contact_id (not linked to a HubSpot company).')
        if linked:
            print(f'  {linked} HubSpot-linked: {hs_fixed} fixed, {linked - hs_fixed} already correct.')

    @app.cli.command('seed-pitch-types')
    def seed_pitch_types_command():
        """Seed or reset pitch_type_configs to the built-in defaults. Skips existing rows."""
        from app.models.pitch_config import PitchTypeConfig
        from app.pitch_machine.default_pitch_types import DEFAULT_PITCH_TYPES
        added = 0
        for data in DEFAULT_PITCH_TYPES:
            if PitchTypeConfig.query.filter_by(name=data['name']).first() is None:
                db.session.add(PitchTypeConfig(**data))
                added += 1
                print(f'  Added: {data["name"]}')
            else:
                print(f'  Skipped (exists): {data["name"]}')
        db.session.commit()
        print(f'Done — {added} added.')

    @app.cli.command('generate-drafts')
    def generate_drafts_command():
        """Process pending pitch_machine/generate_draft tasks. Cron fallback for browser-driven generation."""
        import re
        from datetime import date, datetime, timedelta
        from app.integrations.claude_drafts import DraftGenerationError, DraftGenerator
        from app.models.pitch import PitchApproval
        from app.models.queue import APITaskQueue
        from app.utils.sanitize import sanitize_body_html

        _CC = 'booking@orchestragold.com'

        def _parse_date(value):
            if not value:
                return None
            try:
                return datetime.strptime(value, '%Y-%m-%d').date()
            except ValueError:
                return None

        def _first_email(*sources):
            pat = re.compile(r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b')
            for text in sources:
                if not text:
                    continue
                for m in pat.finditer(text):
                    addr = m.group(0).lower()
                    if 'orchestragold.com' not in addr:
                        return addr
            return ''

        # Reset tasks stuck in 'processing' for > 10 min
        stale_cutoff = datetime.utcnow() - timedelta(minutes=10)
        stale = (
            APITaskQueue.query
            .filter_by(platform='pitch_machine', task_type='generate_draft', status='processing')
            .filter(APITaskQueue.started_at < stale_cutoff)
            .all()
        )
        for t in stale:
            t.status      = 'pending'
            t.retry_count += 1
        if stale:
            db.session.commit()
            print(f'Reset {len(stale)} stale task(s).')

        tasks = (
            APITaskQueue.query
            .filter_by(platform='pitch_machine', task_type='generate_draft', status='pending')
            .filter(APITaskQueue.retry_count < APITaskQueue.max_retries)
            .order_by(APITaskQueue.created_at)
            .all()
        )

        if not tasks:
            print('No pending generate_draft tasks.')
            return

        print(f'{len(tasks)} pending task(s).')
        succeeded = 0
        failed    = 0

        for task in tasks:
            task.status     = 'processing'
            task.started_at = datetime.utcnow()
            db.session.commit()

            payload    = task.payload or {}
            entry_type = payload.get('entry_type', 'hubspot')
            pitch_type = payload.get('pitch_type', 'Festival')

            if entry_type == 'hubspot':
                name          = payload.get('name', '')
                website       = payload.get('website') or None
                description   = payload.get('description') or None
                hubspot_id    = payload.get('hubspot_id', '')
                email_address = ''
            else:
                name          = payload.get('item_name', '')
                website       = None
                description   = payload.get('notes') or None
                hubspot_id    = payload.get('hubspot_id', '')
                email_address = payload.get('email_address', '')

            send_date = _parse_date(payload.get('send_date') or '')

            try:
                draft = DraftGenerator(pitch_type=pitch_type).generate(
                    name=name, website=website, description=description,
                )
            except DraftGenerationError as e:
                task.status        = 'failed'
                task.error_message = str(e)
                task.completed_at  = datetime.utcnow()
                db.session.commit()
                failed += 1
                print(f'  Failed: {name}: {e}')
                continue

            # Mirror the browser route's logic exactly:
            # hs: items have no email column — extract from research notes.
            # qs: items use payload['email_address'] with NO fallback; an empty
            # TO is visible and fixable, a silently wrong one isn't.
            if entry_type == 'hubspot':
                to_email = _first_email(draft.research_notes)
            else:
                to_email = email_address

            db.session.add(PitchApproval(
                hubspot_contact_id = hubspot_id,
                company_name       = name,
                pitch_type         = pitch_type,
                touch_number       = 1,
                draft_subject      = draft.subject,
                draft_body         = sanitize_body_html(draft.body),
                research_notes     = draft.research_notes,
                to_email           = to_email,
                cc_email           = _CC,
                send_date          = send_date,
                status             = 'pending',
            ))
            task.status       = 'completed'
            task.completed_at = datetime.utcnow()
            db.session.commit()
            succeeded += 1
            print(f'  Done: {name}')

        print(f'Finished: {succeeded} succeeded, {failed} failed.')

    @app.cli.command('pitch-untracked-sends')
    def pitch_untracked_sends_command():
        """D1 diagnostic: HubSpot companies with reach_out_1 set but no Portal PitchApproval.

        These are either legacy 2025 planning-calendar dates (pre-2026), Gmail-sent pitches
        the Portal never saw, or Zoho sends the follow-up engine stamped but the Portal has
        no record of. Used to distinguish real sends from planning noise before the engine
        can safely key off reach_out_1.
        """
        from datetime import date as _date
        from app.models.hubspot_cache import HubSpotCompany
        from app.models.pitch import PitchApproval

        companies = (
            HubSpotCompany.query
            .filter(HubSpotCompany.reach_out_1 != None)
            .order_by(HubSpotCompany.reach_out_1)
            .all()
        )
        if not companies:
            print('No HubSpot companies have reach_out_1 set.')
            return

        # Build index of hubspot_ids that have a Portal PitchApproval (any status)
        sent_hs_ids = {
            row.hubspot_contact_id
            for row in PitchApproval.query
            .filter(PitchApproval.hubspot_contact_id != '')
            .filter(PitchApproval.hubspot_contact_id != None)
            .all()
        }

        legacy  = []
        untracked = []
        portal_matched = []

        cutoff = _date(2026, 1, 1)
        for c in companies:
            if c.hubspot_id in sent_hs_ids:
                portal_matched.append(c)
            elif c.reach_out_1 < cutoff:
                legacy.append(c)
            else:
                untracked.append(c)

        print(f'\nTotal with reach_out_1 set: {len(companies)}')
        print(f'  Portal-matched (PitchApproval exists): {len(portal_matched)}')
        print(f'  Pre-2026 / LEGACY-2025 (D11):          {len(legacy)}')
        print(f'  Untracked 2026+ sends:                 {len(untracked)}')

        if legacy:
            print('\n--- LEGACY-2025 (planning dates, not real sends) ---')
            for c in legacy:
                print(f'  {c.reach_out_1}  {c.name}  [{c.hubspot_id}]')

        if untracked:
            print('\n--- UNTRACKED 2026 (real sends not in Portal) ---')
            for c in untracked:
                print(f'  {c.reach_out_1}  {c.name}  [{c.hubspot_id}]')

        if not legacy and not untracked:
            print('\nAll reach_out_1 dates match Portal PitchApproval records.')

    @app.cli.command('sync-knowledge')
    def sync_knowledge_command():
        """Pull Dropbox knowledge files into the local cache. Safe to re-run."""
        from app.integrations.dropbox_sync import sync_knowledge_to_cache, DropboxError
        try:
            results = sync_knowledge_to_cache()
            for path, chars in results.items():
                name = path.split('/')[-1]
                print(f'  {name}: {chars:,} chars')
            print(f'Knowledge sync complete ({len(results)} files).')
        except DropboxError as e:
            print(f'Error: {e}')

    return app


def _auto_init_db(flask_app):
    """
    Create tables and seed defaults on first startup.
    db.create_all() is idempotent — safe to call on every boot.
    Needed because GoDaddy Deluxe shared hosting has no shell access,
    so there's no way to run 'flask init-db' manually.
    """
    import app.models  # noqa: F401 — registers all models before create_all
    db.create_all()

    from .models.queue import AppSetting
    if AppSetting.query.get('mode') is None:
        db.session.add(AppSetting(key_name='mode', value=flask_app.config.get('APP_MODE', 'test')))
    if AppSetting.query.get('version') is None:
        db.session.add(AppSetting(key_name='version', value='0.1.0'))
    db.session.commit()

    # Seed pitch type configs on first boot (table empty = fresh install or reset)
    from .models.pitch_config import PitchTypeConfig
    if PitchTypeConfig.query.count() == 0:
        from .pitch_machine.default_pitch_types import DEFAULT_PITCH_TYPES
        for data in DEFAULT_PITCH_TYPES:
            db.session.add(PitchTypeConfig(**data))
        db.session.commit()
