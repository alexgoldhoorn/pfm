# Migration Guide: Updating Code to Use Database Factory

This guide helps you update existing code to use the new database factory for seamless SQLite/PostgreSQL compatibility.

## Summary of Changes

The database abstraction layer introduces a factory pattern that automatically selects the appropriate database implementation based on environment variables. This ensures backward compatibility while adding PostgreSQL support.

## Code Changes Required

### 1. Update Database Imports

**Before:**
```python
from portf_manager.database import get_database

db = get_database()
```

**After:**
```python
from portf_manager.database_factory import get_database

db = get_database()
```

### 2. Update Direct Database Usage

**Before:**
```python
from portf_manager.database import Database

db = Database("my_database.db")
```

**After:**
```python
import os
from portf_manager.database_factory import get_database_adapter

# For SQLite
os.environ["SQLITE_DB_PATH"] = "my_database.db"
db = get_database_adapter()

# For PostgreSQL
os.environ["DATABASE_URL"] = "postgresql://user:pass@localhost:5432/mydb"
db = get_database_adapter()
```

### 3. Update Domain Model Usage

**Before:**
```python
from portf_manager.database import get_database
from portf_manager.models import Asset

db = get_database()
asset_data = db.get_asset_by_symbol("AAPL")
asset = Asset.from_dict(asset_data, db)
```

**After:**
```python
from portf_manager.database_factory import get_database
from portf_manager.models import Asset

db = get_database()
asset_data = db.get_asset_by_symbol("AAPL")
asset = Asset.from_dict(asset_data, db)
```

### 4. Update CLI and Application Code

**Before:**
```python
# In CLI or main application
from portf_manager.database import get_database

def main():
    db = get_database("portfolio.db")
    # ... rest of application
```

**After:**
```python
# In CLI or main application
from portf_manager.database_factory import get_database

def main():
    db = get_database()  # Automatically chooses SQLite or PostgreSQL
    # ... rest of application
```

## Files That Need Updates

Based on the existing codebase, here are the specific files that should be updated:

### 1. CLI Interface (`portf_manager/cli.py`)
Update database imports to use the factory:

```python
# Change this:
from .database import get_database

# To this:
from .database_factory import get_database
```

### 2. Test Files
Update test files to use the factory:

```python
# In test files
from portf_manager.database_factory import get_database, reset_database_instance

# For test isolation
def setup_test():
    reset_database_instance()
    return get_database()
```

### 3. Application Scripts
Update any scripts that directly import the database:

```python
# Update scripts like create_demo_account.py, repair_database.py, etc.
from portf_manager.database_factory import get_database
```

## Environment Configuration

### Development (SQLite)
```bash
# No environment variables needed - uses SQLite by default
# Optional: specify custom SQLite path
export SQLITE_DB_PATH="/path/to/custom/portfolio.db"
```

### Production (PostgreSQL)
```bash
export DATABASE_URL="postgresql://username:password@hostname:5432/database_name"
```

### Docker Environment
```bash
# In docker-compose.yml or .env file
DATABASE_URL=postgresql://portf_user:portf_password@postgres:5432/portf_db
```

## Testing Strategy

### 1. Local Testing
```bash
# Test SQLite (default)
python -m pytest tests/

# Test PostgreSQL
export DATABASE_URL="postgresql://user:pass@localhost:5432/test_db"
python -m pytest tests/
```

### 2. Test Database Switching
```python
import os
from portf_manager.database_factory import get_database, reset_database_instance

# Test SQLite
if "DATABASE_URL" in os.environ:
    del os.environ["DATABASE_URL"]
reset_database_instance()
sqlite_db = get_database()
print(f"Using: {type(sqlite_db).__name__}")

# Test PostgreSQL
os.environ["DATABASE_URL"] = "postgresql://user:pass@localhost:5432/test_db"
reset_database_instance()
pg_db = get_database()
print(f"Using: {type(pg_db).__name__}")
```

## Backward Compatibility

The factory maintains complete backward compatibility:

- **All existing API methods work unchanged**
- **No breaking changes to function signatures**
- **Same return types and behavior**
- **Existing SQLite databases continue to work**

## Common Migration Patterns

### Pattern 1: Simple Database Usage
```python
# Before
from portf_manager.database import get_database
db = get_database()

# After  
from portf_manager.database_factory import get_database
db = get_database()
```

### Pattern 2: Custom Database Path
```python
# Before
from portf_manager.database import Database
db = Database("custom.db")

# After
import os
from portf_manager.database_factory import get_database_adapter
os.environ["SQLITE_DB_PATH"] = "custom.db"
db = get_database_adapter()
```

### Pattern 3: Domain Model Integration
```python
# Before
from portf_manager.database import get_database
from portf_manager.models import Portfolio

db = get_database()
portfolio_data = db.get_portfolio(1)
portfolio = Portfolio.from_dict(portfolio_data, db)

# After (no changes needed!)
from portf_manager.database_factory import get_database
from portf_manager.models import Portfolio

db = get_database()
portfolio_data = db.get_portfolio(1)
portfolio = Portfolio.from_dict(portfolio_data, db)
```

## Testing the Migration

### 1. Create a Test Script
```python
#!/usr/bin/env python3
import os
import sys
from portf_manager.database_factory import get_database, reset_database_instance

def test_migration():
    """Test that the migration works correctly."""
    
    # Test SQLite fallback
    print("Testing SQLite...")
    if "DATABASE_URL" in os.environ:
        del os.environ["DATABASE_URL"]
    reset_database_instance()
    
    db = get_database()
    print(f"Database type: {type(db).__name__}")
    
    # Test basic operations
    try:
        # Create a test asset
        asset_id = db.create_asset(
            symbol="TEST",
            name="Test Asset",
            asset_type="stock"
        )
        print(f"Created asset with ID: {asset_id}")
        
        # Retrieve the asset
        asset = db.get_asset(asset_id)
        print(f"Retrieved asset: {asset['symbol']}")
        
        print("✅ Migration test passed!")
        return True
        
    except Exception as e:
        print(f"❌ Migration test failed: {e}")
        return False

if __name__ == "__main__":
    success = test_migration()
    sys.exit(0 if success else 1)
```

### 2. Run the Test
```bash
python test_migration.py
```

## Rollback Plan

If issues arise, you can rollback by reverting the import changes:

```python
# Rollback: Change this
from portf_manager.database_factory import get_database

# Back to this
from portf_manager.database import get_database
```

The old database module remains unchanged and fully functional.

## Benefits After Migration

1. **Automatic Database Selection**: No code changes needed to switch between SQLite and PostgreSQL
2. **Environment-based Configuration**: Easy deployment to different environments
3. **Production Ready**: PostgreSQL support for scalability
4. **Backward Compatible**: Existing SQLite databases continue to work
5. **Future Proof**: Easy to add support for additional databases

## Next Steps

1. Update imports in your codebase
2. Test with SQLite to ensure nothing breaks
3. Set up PostgreSQL for production
4. Test with PostgreSQL
5. Deploy with confidence

The migration is designed to be safe and incremental, allowing you to update at your own pace while maintaining full functionality.
