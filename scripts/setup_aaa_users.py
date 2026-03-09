#!/usr/bin/env python3
"""
Setup AAA (Authentication, Authorization, Accounting) Test Users.

Creates test users with different roles and permissions for testing the
ADK web UI and agent access control.
"""

import asyncio
import os
import sys
from pathlib import Path

# Add shared-models to path
sys.path.insert(0, str(Path(__file__).parent.parent / "shared-models" / "src"))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from shared_models.aaa_service import AAAService
from shared_models.auth_service import AuthService
from shared_models.models import UserRole

# Database configuration
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@localhost:5432/partner_agent"
)


async def setup_test_users():
    """Create test users with different roles and permissions."""

    print("=" * 70)
    print("Setting up AAA Test Users")
    print("=" * 70)
    print()

    # Create database engine
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as db:
        # 1. Admin User - Full access to all agents
        print("Creating ADMIN user...")
        admin_user = await AAAService.get_or_create_user(
            db,
            email="admin@example.com",
            role=UserRole.ADMIN,
            organization="Engineering",
            department="Platform"
        )
        print(f"  ✓ admin@example.com")
        print(f"    Role: {admin_user.role}")
        print(f"    Access: Full (all agents)")
        print()

        # 2. Manager User - Full access to all agents
        print("Creating MANAGER user...")
        manager_user = await AAAService.get_or_create_user(
            db,
            email="manager@example.com",
            role=UserRole.MANAGER,
            organization="Engineering",
            department="Support"
        )
        print(f"  ✓ manager@example.com")
        print(f"    Role: {manager_user.role}")
        print(f"    Access: Full (all agents)")
        print()

        # 3. Engineer User - Software and Network support
        print("Creating ENGINEER user...")
        engineer_user = await AAAService.get_or_create_user(
            db,
            email="engineer@example.com",
            role=UserRole.ENGINEER,
            organization="Engineering",
            department="Development"
        )
        print(f"  ✓ engineer@example.com")
        print(f"    Role: {engineer_user.role}")
        print(f"    Allowed agents: {engineer_user.allowed_agents}")
        print()

        # 4. Support Staff User - Software and Network support
        print("Creating SUPPORT_STAFF user...")
        support_user = await AAAService.get_or_create_user(
            db,
            email="support@example.com",
            role=UserRole.SUPPORT_STAFF,
            organization="Support",
            department="Technical Support"
        )
        print(f"  ✓ support@example.com")
        print(f"    Role: {support_user.role}")
        print(f"    Allowed agents: {support_user.allowed_agents}")
        print()

        # 5. End User - Limited to software support only
        print("Creating USER (end user)...")
        end_user = await AAAService.get_or_create_user(
            db,
            email="user@example.com",
            role=UserRole.USER,
            organization="Customer",
            department=None
        )
        print(f"  ✓ user@example.com")
        print(f"    Role: {end_user.role}")
        print(f"    Allowed agents: {end_user.allowed_agents}")
        print()

        # 6. Carlos - Software Support Only (UI test user)
        print("Creating Carlos - Software Support Only...")
        carlos_user = await AAAService.get_or_create_user(
            db,
            email="carlos@example.com",
            role=UserRole.USER,
            organization="Customer",
            department="Engineering"
        )
        # Set password
        from shared_models.auth_service import AuthService
        await AuthService.set_user_password(db, "carlos@example.com", "carlos123")
        print(f"  ✓ carlos@example.com / carlos123")
        print(f"    Role: {carlos_user.role}")
        print(f"    Allowed agents: {carlos_user.allowed_agents}")
        print()

        # 7. Luis - Network Support Only (UI test user)
        print("Creating Luis - Network Support Only...")
        luis_user = await AAAService.get_or_create_user(
            db,
            email="luis@example.com",
            role=UserRole.ENGINEER,
            organization="Engineering",
            department="Infrastructure"
        )
        # Update to only allow network support
        await AAAService.update_user_permissions(
            db,
            user_email="luis@example.com",
            allowed_agents=["network-support"]
        )
        # Set password
        await AuthService.set_user_password(db, "luis@example.com", "luis123")
        print(f"  ✓ luis@example.com / luis123")
        print(f"    Role: engineer")
        print(f"    Allowed agents: ['network-support']")
        print()

        # 8. Sharon - Admin (All Agents) (UI test user)
        print("Creating Sharon - Admin (All Agents)...")
        sharon_user = await AAAService.get_or_create_user(
            db,
            email="sharon@example.com",
            role=UserRole.ADMIN,
            organization="Engineering",
            department="Platform"
        )
        # Set password
        await AuthService.set_user_password(db, "sharon@example.com", "sharon123")
        print(f"  ✓ sharon@example.com / sharon123")
        print(f"    Role: {sharon_user.role}")
        print(f"    Access: Full (all agents)")
        print()

        # 9. Josh - No Agent Access (UI test user)
        print("Creating Josh - No Agent Access...")
        josh_user = await AAAService.get_or_create_user(
            db,
            email="josh@example.com",
            role=UserRole.USER,
            organization="Customer",
            department="Intern"
        )
        # Explicitly set empty allowed_agents
        await AAAService.update_user_permissions(
            db,
            user_email="josh@example.com",
            allowed_agents=[]
        )
        # Set password
        await AuthService.set_user_password(db, "josh@example.com", "josh123")
        print(f"  ✓ josh@example.com / josh123")
        print(f"    Role: {josh_user.role}")
        print(f"    Allowed agents: [] (none)")
        print()

        # 10. Custom User - Specific agent access
        print("Creating custom user with network-only access...")
        custom_user = await AAAService.get_or_create_user(
            db,
            email="network-specialist@example.com",
            role=UserRole.ENGINEER,
            organization="Engineering",
            department="Infrastructure"
        )

        # Update to only allow network support
        await AAAService.update_user_permissions(
            db,
            user_email="network-specialist@example.com",
            allowed_agents=["network-support"]
        )
        print(f"  ✓ network-specialist@example.com")
        print(f"    Role: engineer")
        print(f"    Allowed agents: ['network-support']")
        print()

        # 7. Disabled User - For testing access denial
        print("Creating disabled user...")
        disabled_user = await AAAService.get_or_create_user(
            db,
            email="disabled@example.com",
            role=UserRole.USER
        )

        await AAAService.update_user_permissions(
            db,
            user_email="disabled@example.com",
            status="disabled"
        )
        print(f"  ✓ disabled@example.com")
        print(f"    Role: user")
        print(f"    Status: disabled")
        print()

    await engine.dispose()

    print("=" * 70)
    print("Test Users Created Successfully!")
    print("=" * 70)
    print()
    print("You can now test ADK web UI with these users:")
    print()
    print("1. admin@example.com - Full access to all agents")
    print("2. manager@example.com - Full access to all agents")
    print("3. engineer@example.com - Software + Network support")
    print("4. support@example.com - Software + Network support")
    print("5. user@example.com - Software support only")
    print("6. network-specialist@example.com - Network support only")
    print("7. disabled@example.com - Account disabled (should be denied)")
    print()
    print("Test chat:")
    print()
    print("  curl -X POST http://localhost:8000/adk/chat \\")
    print("    -H 'Content-Type: application/json' \\")
    print("    -d '{\"message\": \"hello\", \"user\": {\"email\": \"engineer@example.com\"}}'")
    print()
    print()


async def verify_setup():
    """Verify test users were created correctly."""

    print("Verifying user setup...")
    print()

    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as db:
        test_users = [
            "admin@example.com",
            "engineer@example.com",
            "user@example.com"
        ]

        for email in test_users:
            user = await AAAService.get_user_by_email(db, email)
            if user:
                allowed = await AAAService.get_user_allowed_agents(db, email)
                print(f"✓ {email}: role={user.role}, allowed_agents={allowed}")
            else:
                print(f"✗ {email}: NOT FOUND")

    await engine.dispose()
    print()


if __name__ == "__main__":
    print()
    asyncio.run(setup_test_users())
    asyncio.run(verify_setup())
    print("Setup complete!")
    print()
