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
            print(f'\n{len(still_unmatched)} unmatched — no confident subject-line match '
                  f'(review and drag to SENT manually if pitched):')
            for msg in still_unmatched[:50]:
                print(f'  → {msg["to_address"]}: {msg["subject"][:65]}')
            if len(still_unmatched) > 50:
                print(f'  ... and {len(still_unmatched) - 50} more')

        # ── Phase 3: fix portal pitches where send succeeded but HubSpot write failed ──
        # PitchApproval.status='sent' means process-queue delivered the email.
        # If the subsequent HubSpot write failed, the board still shows QUEUED.
        # Find those and write ATTEMPTED_TO_CONTACT now.
        hs_fixed = 0
        sent_approvals = PitchApproval.query.filter_by(status='sent').all()
        for appr in sent_approvals:
            if not appr.hubspot_contact_id:
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

        if hs_fixed:
            print(f'\n{hs_fixed} portal pitch(es) reconciled (HubSpot write had previously failed).')
        elif sent_approvals:
            print(f'\nPhase 3: all {len(sent_approvals)} sent portal approvals already reconciled.')

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
                name        = payload.get('name', '')
                website     = payload.get('website') or None
                description = payload.get('description') or None
                hubspot_id  = payload.get('hubspot_id', '')
            else:
                name        = payload.get('item_name', '')
                website     = None
                description = payload.get('notes') or None
                hubspot_id  = payload.get('hubspot_id', '')

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

            db.session.add(PitchApproval(
                hubspot_contact_id = hubspot_id,
                company_name       = name,
                pitch_type         = pitch_type,
                touch_number       = 1,
                draft_subject      = draft.subject,
                draft_body         = sanitize_body_html(draft.body),
                research_notes     = draft.research_notes,
                to_email           = _first_email(draft.research_notes),
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
