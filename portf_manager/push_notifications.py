"""Send Web Push notifications to subscribed browsers."""

import json
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


def send_push_notification(
    subscription: Dict,
    payload: Dict,
    vapid_private_key: str,
    vapid_public_key: str,
) -> bool:
    """Send a single push notification.

    Args:
        subscription: Dict with endpoint, p256dh, auth keys.
        payload: Notification payload (title, body, icon).
        vapid_private_key: PEM-encoded VAPID private key.
        vapid_public_key: URL-safe base64 VAPID public key (unused directly).

    Returns:
        True on success, False on failure.
    """
    try:
        from pywebpush import webpush, WebPushException  # noqa: F401

        webpush(
            subscription_info={
                "endpoint": subscription["endpoint"],
                "keys": {
                    "p256dh": subscription["p256dh"],
                    "auth": subscription["auth"],
                },
            },
            data=json.dumps(payload),
            vapid_private_key=vapid_private_key,
            vapid_claims={"sub": "mailto:noreply@pfm.local"},
        )
        return True
    except Exception as e:
        logger.warning(
            "Push notification failed for %s: %s",
            subscription["endpoint"][:40],
            e,
        )
        return False


def send_alerts_push(db: object, alerts: List[Dict]) -> None:
    """Send push notifications for price alerts to all subscriptions.

    Args:
        db: Database instance with get_push_subscriptions and get_setting.
        alerts: List of alert dicts from the check_alerts endpoint.
    """
    if not alerts:
        return
    subscriptions = db.get_push_subscriptions()
    if not subscriptions:
        return
    private_key = db.get_setting("vapid_private_key")
    public_key = db.get_setting("vapid_public_key")
    if not private_key or not public_key:
        logger.warning("VAPID keys not configured; push notifications skipped")
        return

    for alert in alerts:
        triggered = alert.get("triggers", [])
        for t in triggered:
            payload = {
                "title": f"PFM Alert: {alert['symbol']} {t['type']}",
                "body": (
                    f"{alert['name']} at {t['price']:.2f} "
                    f"(threshold: {t['threshold']:.2f})"
                ),
                "icon": "/icons/icon-192.png",
            }
            for sub in subscriptions:
                send_push_notification(sub, payload, private_key, public_key)
