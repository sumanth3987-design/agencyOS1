#!/usr/bin/env python
import sys
sys.path.insert(0, r"d:\AgencyOS1")

from app.models import User

columns = [c.name for c in User.__table__.columns]
print("User model columns:", columns)
print("phone column exists:", "phone" in columns)

# Check column type
for col in User.__table__.columns:
    if col.name == "phone":
        print(f"phone column type: {col.type}")
        print(f"phone column nullable: {col.nullable}")

# Verify database has the column
from app.extensions import db
inspector = db.inspect(db.engine)
db_columns = [col['name'] for col in inspector.get_columns('user')]
print("Database user table columns:", db_columns)
print("phone column in database:", "phone" in db_columns)