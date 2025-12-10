"""
Service functions for creating and managing notifications.
"""

from typing import Any, Dict, List, Optional

from django.contrib.auth import get_user_model

from .models import Notification

User = get_user_model()


def create_notification(
    user: User,
    type: str,
    title: str,
    message: str,
    action_url: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Notification:
    """
    Create a notification for a user.

    Args:
        user: User to receive the notification
        type: Notification type (success, error, warning, info)
        title: Notification title
        message: Notification message
        action_url: Optional URL to navigate to when clicked
        metadata: Optional additional metadata

    Returns:
        Created Notification instance
    """
    return Notification.objects.create(
        user=user,
        type=type,
        title=title,
        message=message,
        action_url=action_url,
        metadata=metadata or {},
    )


def create_bulk_notifications(
    users: List[User],
    type: str,
    title: str,
    message: str,
    action_url: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> List[Notification]:
    """
    Create notifications for multiple users.

    Args:
        users: List of users to receive the notification
        type: Notification type (success, error, warning, info)
        title: Notification title
        message: Notification message
        action_url: Optional URL to navigate to when clicked
        metadata: Optional additional metadata

    Returns:
        List of created Notification instances
    """
    notifications = []
    for user in users:
        notification = Notification.objects.create(
            user=user,
            type=type,
            title=title,
            message=message,
            action_url=action_url,
            metadata=metadata or {},
        )
        notifications.append(notification)
    return notifications


def notify_admins(
    type: str,
    title: str,
    message: str,
    action_url: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> List[Notification]:
    """
    Create notifications for all admin users.

    Args:
        type: Notification type (success, error, warning, info)
        title: Notification title
        message: Notification message
        action_url: Optional URL to navigate to when clicked
        metadata: Optional additional metadata

    Returns:
        List of created Notification instances
    """
    admin_users = User.objects.filter(is_staff=True, is_active=True)
    return create_bulk_notifications(
        list(admin_users),
        type,
        title,
        message,
        action_url,
        metadata,
    )
