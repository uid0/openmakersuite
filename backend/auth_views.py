"""
Custom authentication views for makerspace users.
"""
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate
import re


@api_view(['POST'])
@permission_classes([AllowAny])
def register_user(request):
    """
    Register a new user with simple validation.
    For makerspace use - simplified registration process.
    """
    username = request.data.get('username', '').strip()
    email = request.data.get('email', '').strip()
    password = request.data.get('password', 'makerspace123')  # Default password
    
    # Basic validation
    if not username:
        return Response(
            {'detail': 'Username is required'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    if len(username) < 3:
        return Response(
            {'detail': 'Username must be at least 3 characters long'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Check if username already exists
    if User.objects.filter(username=username).exists():
        return Response(
            {'detail': 'Username already exists. Please choose another.'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Validate email if provided
    if email and not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        return Response(
            {'detail': 'Please enter a valid email address'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        # Create the user
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password
        )
        user.save()
        
        # Generate tokens for immediate login
        refresh = RefreshToken.for_user(user)
        access = refresh.access_token
        
        return Response({
            'detail': 'User created successfully',
            'username': username,
            'access': str(access),
            'refresh': str(refresh),
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        return Response(
            {'detail': f'Registration failed: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([AllowAny])
def login_user(request):
    """
    Login user and return JWT tokens.
    """
    username = request.data.get('username', '').strip()
    password = request.data.get('password', '')
    
    if not username or not password:
        return Response(
            {'detail': 'Username and password are required'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Authenticate user
    user = authenticate(username=username, password=password)
    
    if user is None:
        return Response(
            {'detail': 'Invalid credentials'}, 
            status=status.HTTP_401_UNAUTHORIZED
        )
    
    if not user.is_active:
        return Response(
            {'detail': 'User account is disabled'}, 
            status=status.HTTP_401_UNAUTHORIZED
        )
    
    # Generate tokens
    refresh = RefreshToken.for_user(user)
    access = refresh.access_token
    
    return Response({
        'access': str(access),
        'refresh': str(refresh),
        'username': user.username,
        'is_staff': user.is_staff,
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def refresh_token(request):
    """
    Refresh JWT access token.
    """
    refresh_token = request.data.get('refresh')
    
    if not refresh_token:
        return Response(
            {'detail': 'Refresh token is required'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        refresh = RefreshToken(refresh_token)
        access = refresh.access_token
        
        return Response({
            'access': str(access),
        })
        
    except Exception as e:
        return Response(
            {'detail': 'Invalid refresh token'}, 
            status=status.HTTP_401_UNAUTHORIZED
        )
