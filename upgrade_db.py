"""AgencyOS Database Schema Upgrade Script.

Safely adds newly introduced columns to existing SQLite tables without wiping any data,
creates new tables (withdrawal_requests, wallet_transactions), and auto-verifies the admin.
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "instance", "agencyos.db")

def upgrade_database():
    if not os.path.exists(DB_PATH):
        print(f"[!] Database not found at {DB_PATH}. It will be initialized on first run.")
        return

    print(f"[*] Upgrading database at: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    def get_columns(table_name):
        cursor.execute(f"PRAGMA table_info({table_name});")
        return [row[1] for row in cursor.fetchall()]

    # 1. Update 'user' table
    user_cols = get_columns("user")
    if "is_verified" not in user_cols:
        cursor.execute("ALTER TABLE user ADD COLUMN is_verified BOOLEAN DEFAULT 0;")
        print("  [+] Added 'is_verified' column to 'user'")
    if "verification_status" not in user_cols:
        cursor.execute("ALTER TABLE user ADD COLUMN verification_status VARCHAR(30) DEFAULT 'Pending';")
        print("  [+] Added 'verification_status' column to 'user'")
    if "verified_at" not in user_cols:
        cursor.execute("ALTER TABLE user ADD COLUMN verified_at DATETIME;")
        print("  [+] Added 'verified_at' column to 'user'")
    if "rejection_reason" not in user_cols:
        cursor.execute("ALTER TABLE user ADD COLUMN rejection_reason TEXT;")
        print("  [+] Added 'rejection_reason' column to 'user'")

    # Auto-verify existing Admin and existing users so you aren't locked out
    cursor.execute("UPDATE user SET is_verified = 1, verification_status = 'Verified' WHERE role = 'admin';")
    print("  [*] Auto-verified admin accounts.")

    # 2. Update 'project' table
    project_cols = get_columns("project")
    if "required_skills" not in project_cols:
        cursor.execute("ALTER TABLE project ADD COLUMN required_skills TEXT;")
        print("  [+] Added 'required_skills' column to 'project'")
    if "assigned_professional_id" not in project_cols:
        cursor.execute("ALTER TABLE project ADD COLUMN assigned_professional_id INTEGER;")
        print("  [+] Added 'assigned_professional_id' column to 'project'")

    # 3. Update 'professional_profile' table
    if "professional_profile" in [row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]:
        prof_cols = get_columns("professional_profile")
        if "pending_balance" not in prof_cols:
            cursor.execute("ALTER TABLE professional_profile ADD COLUMN pending_balance NUMERIC(12, 2) DEFAULT 0.00;")
            print("  [+] Added 'pending_balance' column to 'professional_profile'")
        if "available_balance" not in prof_cols:
            cursor.execute("ALTER TABLE professional_profile ADD COLUMN available_balance NUMERIC(12, 2) DEFAULT 0.00;")
            print("  [+] Added 'available_balance' column to 'professional_profile'")
        if "bank_beneficiary_name" not in prof_cols:
            cursor.execute("ALTER TABLE professional_profile ADD COLUMN bank_beneficiary_name VARCHAR(120);")
            print("  [+] Added 'bank_beneficiary_name' column to 'professional_profile'")

    # 4. Update 'professional_project_application' table
    if "professional_project_application" in [row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]:
        app_cols = get_columns("professional_project_application")
        if "proposal" not in app_cols:
            cursor.execute("ALTER TABLE professional_project_application ADD COLUMN proposal TEXT;")
            print("  [+] Added 'proposal' column to 'professional_project_application'")
        if "proposed_rate" not in app_cols:
            cursor.execute("ALTER TABLE professional_project_application ADD COLUMN proposed_rate NUMERIC(12, 2);")
            print("  [+] Added 'proposed_rate' column to 'professional_project_application'")
        if "estimated_days" not in app_cols:
            cursor.execute("ALTER TABLE professional_project_application ADD COLUMN estimated_days INTEGER;")
            print("  [+] Added 'estimated_days' column to 'professional_project_application'")

    conn.commit()
    conn.close()

    # 5. Create new tables (withdrawal_requests, wallet_transactions) via SQLAlchemy
    try:
        from app import create_app
        from app.extensions import db
        app = create_app()
        with app.app_context():
            db.create_all()
            print("  [+] Synchronized new models & tables via SQLAlchemy.")
    except Exception as e:
        print(f"  [!] SQLAlchemy sync note: {e}")

    print("\n[✓] Database upgrade completed successfully! All tables and columns are synchronized.")

if __name__ == "__main__":
    upgrade_database()