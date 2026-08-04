import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from werkzeug.middleware.proxy_fix import ProxyFix
from app import create_app

application = create_app()

# Tell Flask it's behind Apache/Passenger so url_for(_external=True) produces https:// URLs
# and the OAuth callback URI matches what Google expects.
application.wsgi_app = ProxyFix(
    application.wsgi_app,
    x_for=1,
    x_proto=1,
    x_host=1,
    x_prefix=1,
)
