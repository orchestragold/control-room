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
            try:
                send_email(
                    to_address = payload['to_email_actual'],
                    subject    = payload['subject'],
                    body_html  = payload['body'],
                    cc_address = payload.get('cc_email') or None,
                )
                task.status       = 'completed'
                task.completed_at = datetime.utcnow()

                approval = PitchApproval.query.get(payload.get('pitch_approval_id'))
                if approval:
                    approval.status  = 'sent'
                    approval.sent_at = datetime.utcnow()

                    # Write ATTEMPTED_TO_CONTACT back to HubSpot so the board moves the card
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
                recipient = payload.get('to_email_actual', '?')
                intended  = payload.get('to_email_intended', '')
                redirect_note = f' (redirected from {intended})' if payload.get('was_redirected') else ''
                print(f'  Sent → {recipient}{redirect_note}')

            except Exception as e:
                task.retry_count  += 1
                task.error_message = str(e)
                task.status = (
                    'pending' if task.retry_count < task.max_retries else 'failed'
                )
                failed += 1
                print(f'  Failed: {e}')

            db.session.commit()

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

        if unmatched:
            print(f'\n{len(unmatched)} sent messages with no portal record '
                  f'(pitched outside portal — drag manually on board):')
            for msg in unmatched[:25]:
                subj = msg["subject"][:60]
                print(f'  → {msg["to_address"]}: {subj}')
            if len(unmatched) > 25:
                print(f'  ... and {len(unmatched) - 25} more')

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
