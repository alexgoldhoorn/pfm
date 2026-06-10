#!/usr/bin/env python3
"""
Standalone Database Repair Script

This script repairs already-deployed databases by:
1. Creating automatic backup (*.bak)
2. Adding missing user_id columns and indexes
3. Inserting default admin user if absent
4. Updating existing rows to reference the admin user
5. Setting PRAGMA user_version = 3 for consistency

Usage:
    python repair_database.py [database_path]

If no database path is provided, it will look for 'portfolio.db' in the current directory.
"""

import sqlite3
import sys
import shutil
from pathlib import Path
from datetime import datetime


def _add_column_if_missing(conn, table, column, ddl):
    """
    Helper function to add a column to a table if it doesn't exist.

    Args:
        conn: SQLite connection
        table: Table name
        column: Column name to check/add
        ddl: Column definition (e.g., 'INTEGER', 'TEXT NOT NULL')
    """
    # Query column list
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        print(f"  ✓ Added column '{column}' to table '{table}'")
    else:
        print(f"  - Column '{column}' already exists in table '{table}'")


def create_backup(db_path):
    """
    Create a backup of the database file.

    Args:
        db_path: Path to the database file

    Returns:
        str: Path to the backup file

    Raises:
        Exception: If backup creation fails
    """
    db_path = Path(db_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_suffix(f".{timestamp}.bak")

    try:
        shutil.copy2(db_path, backup_path)
        print(f"✓ Database backed up to: {backup_path}")
        return str(backup_path)
    except Exception as e:
        raise Exception(f"Failed to create backup: {e}")


def repair_database(db_path):
    """
    Repair an already-deployed database by adding user support.

    Args:
        db_path: Path to the database file

    Returns:
        bool: True if repair was successful, False otherwise
    """
    db_path = Path(db_path)

    # Check if database file exists
    if not db_path.exists():
        print(f"❌ Database file {db_path} does not exist.")
        return False

    print(f"🔧 Starting database repair for: {db_path}")
    print("=" * 60)

    # Create backup
    try:
        backup_path = create_backup(db_path)
    except Exception as e:
        print(f"❌ {e}")
        return False

    conn = None
    try:
        # Connect to database
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Begin transaction
        conn.execute("BEGIN TRANSACTION")

        # Check if users table exists
        cursor = conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='users'
        """
        )
        users_table_exists = cursor.fetchone() is not None

        if not users_table_exists:
            print("📋 Creating users table...")
            conn.execute(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    full_name TEXT,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    last_login TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )

            # Create indexes for users table
            conn.execute("CREATE INDEX idx_users_username ON users (username)")
            conn.execute("CREATE INDEX idx_users_email ON users (email)")

            # Create update trigger for users table
            conn.execute(
                """
                CREATE TRIGGER update_users_timestamp
                AFTER UPDATE ON users
                BEGIN
                    UPDATE users SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
                END
            """
            )

            print("  ✓ Users table created with indexes and triggers")
        else:
            print("  - Users table already exists")

        # Check if admin user exists
        cursor = conn.execute("SELECT id FROM users WHERE username = 'admin'")
        admin_user = cursor.fetchone()

        if admin_user is None:
            print("👤 Creating default admin user...")
            conn.execute(
                """
                INSERT INTO users (username, email, password_hash, salt, full_name)
                VALUES ('admin', 'admin@localhost', 'dummy_hash', 'dummy_salt', 'Default Admin User')
            """
            )

            # Get the admin user ID
            cursor = conn.execute("SELECT id FROM users WHERE username = 'admin'")
            admin_user = cursor.fetchone()
            admin_id = admin_user[0]
            print(f"  ✓ Created admin user with ID: {admin_id}")
        else:
            admin_id = admin_user[0]
            print(f"  - Admin user already exists with ID: {admin_id}")

        # List of tables that need user_id columns
        tables_to_update = [
            ("entities", "INTEGER"),
            ("portfolios", "INTEGER"),
            ("transactions", "INTEGER"),
        ]

        # Add user_id columns to tables if missing
        for table, column_type in tables_to_update:
            # Check if table exists
            cursor = conn.execute(
                f"""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='{table}'
            """
            )
            table_exists = cursor.fetchone() is not None

            if table_exists:
                print(f"🔄 Processing table: {table}")
                _add_column_if_missing(conn, table, "user_id", column_type)

                # Update existing rows to reference admin user
                cursor = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL"
                )
                null_count = cursor.fetchone()[0]

                if null_count > 0:
                    print(f"  ⚡ Updating {null_count} rows to reference admin user...")
                    conn.execute(
                        f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL",
                        (admin_id,),
                    )
                else:
                    print(f"  - No rows with NULL user_id in {table}")

                # Create index for user_id column
                index_name = f"idx_{table}_user_id"
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} (user_id)"
                )
                print(f"  ✓ Ensured index {index_name} exists")
            else:
                print(f"  - Table {table} does not exist, skipping...")

        # Set database version to 3
        print("🏷️  Setting database version...")

        # Check if database_version table exists
        cursor = conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='database_version'
        """
        )
        version_table_exists = cursor.fetchone() is not None

        if not version_table_exists:
            print("  📋 Creating database_version table...")
            conn.execute(
                """
                CREATE TABLE database_version (
                    version INTEGER PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )

        # Check current version
        cursor = conn.execute(
            "SELECT version FROM database_version ORDER BY version DESC LIMIT 1"
        )
        current_version = cursor.fetchone()
        current_version = current_version[0] if current_version else 0

        if current_version < 3:
            print(f"  ⬆️  Updating database version from {current_version} to 3...")
            conn.execute("INSERT INTO database_version (version) VALUES (3)")
        else:
            print(f"  - Database version is already {current_version}")

        # Set PRAGMA user_version = 3
        conn.execute("PRAGMA user_version = 3")
        print("  ✓ Set PRAGMA user_version = 3")

        # Commit transaction
        conn.commit()

        print("\n🎉 Database repair completed successfully!")

        # Verification
        print("\n📊 Verification:")
        print("-" * 40)

        # Check user_id columns in all tables
        for table, _ in tables_to_update:
            cursor = conn.execute(
                f"""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='{table}'
            """
            )
            if cursor.fetchone():
                cursor = conn.execute(f"PRAGMA table_info({table})")
                columns = [col["name"] for col in cursor.fetchall()]
                has_user_id = "user_id" in columns

                if has_user_id:
                    cursor = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL"
                    )
                    null_count = cursor.fetchone()[0]
                    cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                    total_count = cursor.fetchone()[0]
                    print(
                        f"  {table}: ✓ user_id column present, {null_count}/{total_count} NULL values"
                    )
                else:
                    print(f"  {table}: ❌ user_id column missing")

        # Check database version
        cursor = conn.execute("PRAGMA user_version")
        pragma_version = cursor.fetchone()[0]
        cursor = conn.execute(
            "SELECT version FROM database_version ORDER BY version DESC LIMIT 1"
        )
        table_version = cursor.fetchone()
        table_version = table_version[0] if table_version else 0

        print(f"  Database version: PRAGMA={pragma_version}, table={table_version}")

        # Check admin user
        cursor = conn.execute(
            "SELECT username, email, full_name FROM users WHERE username = 'admin'"
        )
        admin_info = cursor.fetchone()
        if admin_info:
            print(
                f"  Admin user: {admin_info['username']} ({admin_info['email']}) - {admin_info['full_name']}"
            )

        conn.close()
        return True

    except Exception as e:
        print(f"\n❌ Error during database repair: {e}")
        if conn:
            conn.rollback()
            conn.close()

        # Restore backup if something went wrong
        try:
            shutil.copy2(backup_path, db_path)
            print(f"🔄 Database restored from backup: {backup_path}")
        except Exception as restore_error:
            print(f"❌ Failed to restore backup: {restore_error}")

        return False


def main():
    """Main function to run the database repair script."""
    print("🔧 Database Repair Script")
    print("=" * 60)

    # Get database path from command line argument or use default
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    else:
        db_path = "portfolio.db"

    # Run repair
    success = repair_database(db_path)

    if success:
        print("\n✅ Database repair completed successfully!")
        print(
            "\nThe database is now ready for use with the user authentication system."
        )
        print("Default admin credentials:")
        print("  Username: admin")
        print("  Email: admin@localhost")
        print("  (Password hash and salt are dummy values - update as needed)")
        sys.exit(0)
    else:
        print("\n❌ Database repair failed!")
        print("Please check the error messages above and try again.")
        sys.exit(1)


if __name__ == "__main__":
    main()
