#!/usr/bin/env python3
"""
Setup Production Users: Carlos, Luis, Sharon, and Josh.

Creates 4 specific users with different agent access levels:
- Carlos: Access to software-support agent only
- Luis: Access to network-support agent only
- Sharon: Access to all agents (admin)
- Josh: No agent access (restricted user)
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


async def setup_production_users():
    """Create Carlos, Luis, Sharon, and Josh with specific permissions."""

    print("=" * 70)
    print("Setting up Production Users")
    print("=" * 70)
    print()

    # Create database engine
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as db:
        # 1. Carlos - Software Support Only
        print("Creating user: Carlos")
        carlos = await AAAService.get_or_create_user(
            db,
            email="carlos@example.com",
            role=UserRole.USER,
            organization="Engineering",
            department="Development"
        )

        # Ensure Carlos only has software-support access
        await AAAService.update_user_permissions(
            db,
            user_email="carlos@example.com",
            allowed_agents=["software-support"]
        )
        await AuthService.set_user_password(db, "carlos@example.com", "carlos123")

        print(f"  ✓ carlos@example.com")
        print(f"    Role: {carlos.role}")
        print(f"    Allowed agents: ['software-support']")
        print(f"    Description: Can access software support agent only")
        print()

        # 2. Luis - Network Support Only
        print("Creating user: Luis")
        luis = await AAAService.get_or_create_user(
            db,
            email="luis@example.com",
            role=UserRole.USER,
            organization="Engineering",
            department="Infrastructure"
        )

        # Ensure Luis only has network-support access
        await AAAService.update_user_permissions(
            db,
            user_email="luis@example.com",
            allowed_agents=["network-support"]
        )
        await AuthService.set_user_password(db, "luis@example.com", "luis123")

        print(f"  ✓ luis@example.com")
        print(f"    Role: {luis.role}")
        print(f"    Allowed agents: ['network-support']")
        print(f"    Description: Can access network support agent only")
        print()

        # 3. Sharon - All Agents (Admin)
        print("Creating user: Sharon")
        sharon = await AAAService.get_or_create_user(
            db,
            email="sharon@example.com",
            role=UserRole.ADMIN,
            organization="Engineering",
            department="Management"
        )

        await AuthService.set_user_password(db, "sharon@example.com", "sharon123")

        print(f"  ✓ sharon@example.com")
        print(f"    Role: {sharon.role}")
        print(f"    Allowed agents: All (wildcard '*')")
        print(f"    Description: Can access all agents (admin)")
        print()

        # 4. Josh - No Agent Access
        print("Creating user: Josh")
        josh = await AAAService.get_or_create_user(
            db,
            email="josh@example.com",
            role=UserRole.USER,
            organization="Customer",
            department="Intern"
        )

        # Ensure Josh has no agent access
        await AAAService.update_user_permissions(
            db,
            user_email="josh@example.com",
            allowed_agents=[]
        )
        await AuthService.set_user_password(db, "josh@example.com", "josh123")

        print(f"  ✓ josh@example.com")
        print(f"    Role: {josh.role}")
        print(f"    Allowed agents: [] (none)")
        print(f"    Description: Cannot access any agent")
        print()

    await engine.dispose()

    print("=" * 70)
    print("Production Users Created Successfully!")
    print("=" * 70)
    print()
    print("Users created:")
    print()
    print("1. Carlos (carlos@example.com)")
    print("   - Access: software-support agent ONLY")
    print("   - Use for: Software bugs, crashes, errors")
    print()
    print("2. Luis (luis@example.com)")
    print("   - Access: network-support agent ONLY")
    print("   - Use for: Network issues, VPN, DNS, connectivity")
    print()
    print("3. Sharon (sharon@example.com)")
    print("   - Access: ALL agents (admin)")
    print("   - Use for: Any support request")
    print()
    print("4. Josh (josh@example.com)")
    print("   - Access: NO agents")
    print("   - Use for: Testing access denial for all agents")
    print()
    print("Test access:")
    print()
    print("  # Carlos - should work")
    print("  curl -X POST http://localhost:8000/adk/chat \\")
    print("    -H 'Content-Type: application/json' \\")
    print("    -H 'X-User-Email: carlos@example.com' \\")
    print("    -d '{\"message\":\"My app crashes\",\"user\":{\"email\":\"carlos@example.com\"}}'")
    print()
    print("  # Luis - should work")
    print("  curl -X POST http://localhost:8000/adk/chat \\")
    print("    -H 'Content-Type: application/json' \\")
    print("    -H 'X-User-Email: luis@example.com' \\")
    print("    -d '{\"message\":\"VPN not connecting\",\"user\":{\"email\":\"luis@example.com\"}}'")
    print()
    print("  # Sharon - should work for anything")
    print("  curl -X POST http://localhost:8000/adk/chat \\")
    print("    -H 'Content-Type: application/json' \\")
    print("    -H 'X-User-Email: sharon@example.com' \\")
    print("    -d '{\"message\":\"Any issue\",\"user\":{\"email\":\"sharon@example.com\"}}'")
    print()


async def verify_setup():
    """Verify the 3 users were created correctly."""

    print("Verifying user setup...")
    print()

    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as db:
        users = [
            ("carlos@example.com", "Carlos"),
            ("luis@example.com", "Luis"),
            ("sharon@example.com", "Sharon"),
            ("josh@example.com", "Josh"),
        ]

        for email, name in users:
            user = await AAAService.get_user_by_email(db, email)
            if user:
                allowed = await AAAService.get_user_allowed_agents(db, email)
                print(f"✓ {name:6} ({email})")
                print(f"  Role: {user.role}")
                print(f"  Allowed agents: {allowed}")
                print(f"  Status: {user.status}")
                print()
            else:
                print(f"✗ {name} ({email}): NOT FOUND")
                print()

    await engine.dispose()


async def test_agent_access():
    """Test that each user has correct agent access."""

    print("Testing agent access for each user...")
    print()

    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as db:
        test_cases = [
            ("carlos@example.com", "software-support", True),
            ("carlos@example.com", "network-support", False),
            ("luis@example.com", "software-support", False),
            ("luis@example.com", "network-support", True),
            ("sharon@example.com", "software-support", True),
            ("sharon@example.com", "network-support", True),
            ("josh@example.com", "software-support", False),
            ("josh@example.com", "network-support", False),
        ]

        for user_email, agent_name, should_have_access in test_cases:
            has_access, reason = await AAAService.check_agent_access(
                db,
                user_email=user_email,
                agent_name=agent_name
            )

            status = "✓" if has_access == should_have_access else "✗"
            expected = "ALLOWED" if should_have_access else "DENIED"
            actual = "ALLOWED" if has_access else "DENIED"

            user_short = user_email.split("@")[0].capitalize()

            if has_access == should_have_access:
                print(f"{status} {user_short:6} + {agent_name:18} = {actual:7} (expected {expected})")
            else:
                print(f"{status} {user_short:6} + {agent_name:18} = {actual:7} (expected {expected}) ⚠️  MISMATCH!")

    await engine.dispose()
    print()


if __name__ == "__main__":
    print()
    asyncio.run(setup_production_users())
    asyncio.run(verify_setup())
    asyncio.run(test_agent_access())
    print("Setup complete!")
    print()
