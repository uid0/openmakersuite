#!/usr/bin/env python
"""
Quick script to create a development superuser.
Usage: python create_dev_user.py
"""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()

username = "admin"
email = "admin@localhost"
password = "admin"  # nosec B105 - Hardcoded password is intentional for dev environment only

if User.objects.filter(username=username).exists():
    print(f"✅ User '{username}' already exists!")
else:
    User.objects.create_superuser(username=username, email=email, password=password)
    print(f"✅ Created superuser:")
    print(f"   Username: {username}")
    print(f"   Password: {password}")
    print(f"   Email: {email}")
    print()
    print("⚠️  This is for development only! Change the password in production.")
    print()
    print("🚀 You can now:")
    print(f"   1. Log in at http://localhost:8000/admin")
    print(f"   2. Use the API with session authentication")
