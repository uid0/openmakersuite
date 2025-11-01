"""
API views for dashboard configuration management.
"""

from django.http import JsonResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import DashboardConfig, DashboardMessage


@api_view(['GET'])
@permission_classes([AllowAny])
def get_dashboard_messages(request):
    """
    Get active dashboard messages for rotation.

    Public endpoint used by TV Dashboard to fetch current messages.
    """
    try:
        # Get active messages in order
        messages = DashboardMessage.objects.filter(is_active=True)
        message_list = [msg.message for msg in messages]

        # Get configuration
        config = DashboardConfig.get_config()

        # Fallback to default if no messages configured
        if not message_list:
            message_list = [
                'Tracking items from request to delivery',
                'Scan QR codes to request reorders',
                'Keeping your makerspace stocked'
            ]

        return Response({
            'messages': message_list,
            'rotation_interval_seconds': config.rotation_interval_seconds,
            'auto_refresh_seconds': config.auto_refresh_seconds,
            'maintenance_mode': config.is_maintenance_mode,
            'maintenance_message': config.maintenance_message if config.is_maintenance_mode else None,
            'last_updated': config.updated_at.isoformat()
        })

    except Exception as e:
        # Graceful fallback for any errors
        return Response({
            'messages': ['Tracking items from request to delivery'],
            'rotation_interval_seconds': 10,
            'auto_refresh_seconds': 30,
            'maintenance_mode': False,
            'maintenance_message': None,
            'error': str(e)
        })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_dashboard_config(request):
    """
    Get current dashboard configuration.

    Admin endpoint for viewing current settings.
    """
    try:
        config = DashboardConfig.get_config()
        messages = DashboardMessage.objects.all().order_by('order', 'created_at')

        return Response({
            'config': {
                'rotation_interval_seconds': config.rotation_interval_seconds,
                'auto_refresh_seconds': config.auto_refresh_seconds,
                'is_maintenance_mode': config.is_maintenance_mode,
                'maintenance_message': config.maintenance_message,
                'custom_css': config.custom_css,
                'updated_at': config.updated_at.isoformat()
            },
            'messages': [
                {
                    'id': msg.id,
                    'message': msg.message,
                    'is_active': msg.is_active,
                    'order': msg.order,
                    'created_at': msg.created_at.isoformat(),
                    'updated_at': msg.updated_at.isoformat()
                }
                for msg in messages
            ]
        })

    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_dashboard_config(request):
    """
    Update dashboard configuration.

    Admin endpoint for modifying dashboard settings.
    """
    try:
        config = DashboardConfig.get_config()
        data = request.data

        # Update configuration fields
        if 'rotation_interval_seconds' in data:
            config.rotation_interval_seconds = max(
                1, int(data['rotation_interval_seconds']))

        if 'auto_refresh_seconds' in data:
            config.auto_refresh_seconds = max(
                5, int(data['auto_refresh_seconds']))

        if 'is_maintenance_mode' in data:
            config.is_maintenance_mode = bool(data['is_maintenance_mode'])

        if 'maintenance_message' in data:
            config.maintenance_message = str(data['maintenance_message'])

        if 'custom_css' in data:
            config.custom_css = str(data['custom_css'])

        config.save()

        return Response({
            'message': 'Configuration updated successfully',
            'config': {
                'rotation_interval_seconds': config.rotation_interval_seconds,
                'auto_refresh_seconds': config.auto_refresh_seconds,
                'is_maintenance_mode': config.is_maintenance_mode,
                'maintenance_message': config.maintenance_message,
                'updated_at': config.updated_at.isoformat()
            }
        })

    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_400_BAD_REQUEST
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_dashboard_message(request):
    """
    Add a new dashboard message.

    Admin endpoint for adding messages to rotation.
    """
    try:
        data = request.data

        if 'message' not in data:
            return Response(
                {'error': 'Message text is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        message = DashboardMessage.objects.create(
            message=data['message'],
            is_active=data.get('is_active', True),
            order=data.get('order', 0)
        )

        return Response({
            'message': 'Dashboard message added successfully',
            'id': message.id,
            'text': message.message
        }, status=status.HTTP_201_CREATED)

    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_400_BAD_REQUEST
        )


# Simple health check endpoint
def dashboard_health(request):
    """Simple health check for dashboard system."""
    try:
        config = DashboardConfig.get_config()
        message_count = DashboardMessage.objects.filter(is_active=True).count()

        return JsonResponse({
            'status': 'healthy',
            'active_messages': message_count,
            'maintenance_mode': config.is_maintenance_mode,
            'last_config_update': config.updated_at.isoformat()
        })
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'error': str(e)
        }, status=500)
