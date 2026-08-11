from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from app.extensions import db


@dataclass
class PlatformConfig:
    calls_per_window: int
    window_seconds: int


# Per-platform rate limit config.
# These match each API's documented limits; tighten or loosen as needed.
PLATFORM_CONFIGS: dict[str, PlatformConfig] = {
    'hubspot':         PlatformConfig(calls_per_window=100,    window_seconds=10),
    'meta':            PlatformConfig(calls_per_window=200,    window_seconds=3600),
    'youtube':         PlatformConfig(calls_per_window=10_000, window_seconds=86400),
    'mailerlite':      PlatformConfig(calls_per_window=120,    window_seconds=60),
    'asana':           PlatformConfig(calls_per_window=150,    window_seconds=60),
    'google_calendar': PlatformConfig(calls_per_window=500,    window_seconds=100),
    'dropbox':         PlatformConfig(calls_per_window=300,    window_seconds=60),
    # Zoho Mail API — pitch sending. Daily send cap tracked separately in app_settings
    # (key: zoho_sends_today, reset nightly by cron). API-call-level limit is permissive.
    'zoho':            PlatformConfig(calls_per_window=60,     window_seconds=60),
}


class APIThrottle:
    """
    Rate-limit tracker and task queue for a single platform.

    Usage (immediate call path):
        throttle = APIThrottle('hubspot')
        immediate, task_id = throttle.call_or_queue('fetch_contacts', payload)
        if immediate:
            # make the actual API call here
            ...
        else:
            # task is queued; the cron processor will execute it
            ...

    Usage (queue-only path):
        task_id = throttle.enqueue('send_pitch', payload, user_id=current_user.id)
    """

    def __init__(self, platform: str):
        if platform not in PLATFORM_CONFIGS:
            raise ValueError(
                f"Unknown platform '{platform}'. "
                f"Add it to PLATFORM_CONFIGS in app/core/throttle.py."
            )
        self.platform = platform
        self.config = PLATFORM_CONFIGS[platform]

    def can_call(self) -> bool:
        from app.models.queue import APIRateTracking

        window_start = datetime.utcnow() - timedelta(seconds=self.config.window_seconds)
        total = (
            db.session.query(
                db.func.coalesce(db.func.sum(APIRateTracking.call_count), 0)
            )
            .filter(
                APIRateTracking.platform == self.platform,
                APIRateTracking.window_start >= window_start,
            )
            .scalar()
        )
        return int(total) < self.config.calls_per_window

    def record_call(self):
        """Increment the call counter for the current minute bucket."""
        from app.models.queue import APIRateTracking

        bucket = datetime.utcnow().replace(second=0, microsecond=0)
        existing = APIRateTracking.query.filter_by(
            platform=self.platform, window_start=bucket
        ).first()
        if existing:
            existing.call_count += 1
        else:
            db.session.add(
                APIRateTracking(
                    platform=self.platform,
                    window_start=bucket,
                    call_count=1,
                )
            )
        db.session.commit()

    def enqueue(
        self,
        task_type: str,
        payload: dict,
        priority: int = 5,
        scheduled_at: Optional[datetime] = None,
        user_id: Optional[int] = None,
        max_retries: int = 3,
    ) -> int:
        """Add a task to the queue and return its ID."""
        from app.models.queue import APITaskQueue

        task = APITaskQueue(
            platform=self.platform,
            task_type=task_type,
            payload=payload,
            priority=priority,
            scheduled_at=scheduled_at,
            created_by=user_id,
            max_retries=max_retries,
        )
        db.session.add(task)
        db.session.commit()
        return task.id

    def call_or_queue(
        self,
        task_type: str,
        payload: dict,
        **enqueue_kwargs,
    ) -> tuple[bool, Optional[int]]:
        """
        If under the rate limit: record the call and return (True, None) so the
        caller can make the actual API request immediately.

        If at/over the limit: queue the task and return (False, task_id) so the
        cron processor handles it when the window clears.
        """
        if self.can_call():
            self.record_call()
            return True, None
        task_id = self.enqueue(task_type, payload, **enqueue_kwargs)
        return False, task_id
