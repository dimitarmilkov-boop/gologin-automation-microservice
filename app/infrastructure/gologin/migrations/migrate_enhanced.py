#!/usr/bin/env python3
"""
Migration script for Enhanced GoLogin features
Upgrades existing database schema to support new features
"""

import sqlite3
import os
import json
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def backup_database(db_path):
    """Create a backup of the existing database."""
    backup_path = f"{db_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    try:
        # Create backup
        with sqlite3.connect(db_path) as source:
            with sqlite3.connect(backup_path) as backup:
                source.backup(backup)
        print(f"✅ Database backed up to: {backup_path}")
        return backup_path
    except Exception as e:
        print(f"❌ Failed to backup database: {e}")
        return None

def check_column_exists(cursor, table_name, column_name):
    """Check if a column exists in a table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [column[1] for column in cursor.fetchall()]
    return column_name in columns

def migrate_gologin_schema(db_path='twitter_accounts.db'):
    """Migrate the GoLogin database schema to enhanced version."""
    print("🔄 Starting GoLogin Enhanced Migration")
    print("=" * 50)
    
    if not os.path.exists(db_path):
        print(f"❌ Database file {db_path} not found")
        return False
    
    # Create backup
    backup_path = backup_database(db_path)
    if not backup_path:
        print("❌ Migration aborted - backup failed")
        return False
    
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            
            print("\n📋 Checking existing schema...")
            
            # Check if gologin_profiles table exists
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='gologin_profiles'
            """)
            
            if not cursor.fetchone():
                print("❌ gologin_profiles table not found")
                print("Please run the basic GoLogin setup first")
                return False
            
            print("✅ gologin_profiles table found")
            
            # List of new columns to add
            new_columns = [
                ('os_type', 'TEXT DEFAULT "win"'),
                ('screen_resolution', 'TEXT'),
                ('timezone', 'TEXT'),
                ('language', 'TEXT'),
                ('execution_mode', 'TEXT DEFAULT "cloud"'),
                ('assigned_port', 'INTEGER'),
                ('last_sync_at', 'TIMESTAMP'),
                ('cloud_profile_data', 'TEXT'),
                ('proxy_type', 'TEXT')
            ]
            
            # Add new columns if they don't exist
            print("\n🔧 Adding new columns...")
            for column_name, column_def in new_columns:
                if not check_column_exists(cursor, 'gologin_profiles', column_name):
                    try:
                        cursor.execute(f"ALTER TABLE gologin_profiles ADD COLUMN {column_name} {column_def}")
                        print(f"✅ Added column: {column_name}")
                    except Exception as e:
                        print(f"❌ Failed to add column {column_name}: {e}")
                else:
                    print(f"⏭️  Column {column_name} already exists")
            
            # Create profile_sync_log table
            print("\n📊 Creating profile_sync_log table...")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS profile_sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT NOT NULL,
                    sync_type TEXT NOT NULL,  -- 'import', 'update', 'delete'
                    sync_status TEXT NOT NULL,  -- 'success', 'failed'
                    sync_data TEXT,  -- JSON data
                    error_message TEXT,
                    sync_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            print("✅ profile_sync_log table created")
            
            # Create new indexes
            print("\n📑 Creating new indexes...")
            new_indexes = [
                ('idx_gologin_profiles_execution_mode', 'gologin_profiles', 'execution_mode'),
                ('idx_gologin_profiles_sync', 'gologin_profiles', 'last_sync_at'),
                ('idx_profile_sync_log_profile', 'profile_sync_log', 'profile_id')
            ]
            
            for index_name, table_name, column_name in new_indexes:
                try:
                    cursor.execute(f'CREATE INDEX IF NOT EXISTS {index_name} ON {table_name}({column_name})')
                    print(f"✅ Created index: {index_name}")
                except Exception as e:
                    print(f"❌ Failed to create index {index_name}: {e}")
            
            # Update existing profiles with default values
            print("\n🔄 Updating existing profiles...")
            cursor.execute('''
                UPDATE gologin_profiles 
                SET execution_mode = 'cloud',
                    os_type = 'win',
                    screen_resolution = '1920x1080',
                    timezone = 'America/New_York',
                    language = 'en-US'
                WHERE execution_mode IS NULL
            ''')
            
            updated_count = cursor.rowcount
            print(f"✅ Updated {updated_count} existing profiles with default values")
            
            # Create migration log entry
            cursor.execute('''
                INSERT INTO profile_sync_log 
                (profile_id, sync_type, sync_status, sync_data)
                VALUES ('MIGRATION', 'migration', 'success', ?)
            ''', (json.dumps({
                'migration_date': datetime.now().isoformat(),
                'version': 'enhanced_v1.0',
                'backup_file': backup_path,
                'updated_profiles': updated_count
            }),))
            
            conn.commit()
            
            print("\n🎉 Migration completed successfully!")
            print(f"📁 Backup saved to: {backup_path}")
            print(f"🔢 Updated {updated_count} existing profiles")
            
            return True
            
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        print(f"💾 Database backup available at: {backup_path}")
        print("You can restore from backup if needed")
        return False

def verify_migration(db_path='twitter_accounts.db'):
    """Verify that the migration was successful."""
    print("\n🔍 Verifying migration...")
    
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            
            # Check for new columns
            cursor.execute("PRAGMA table_info(gologin_profiles)")
            columns = [column[1] for column in cursor.fetchall()]
            
            required_columns = [
                'execution_mode', 'os_type', 'screen_resolution', 
                'timezone', 'language', 'assigned_port', 
                'last_sync_at', 'cloud_profile_data', 'proxy_type'
            ]
            
            missing_columns = [col for col in required_columns if col not in columns]
            
            if missing_columns:
                print(f"❌ Missing columns: {missing_columns}")
                return False
            
            print("✅ All required columns present")
            
            # Check for new table
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='profile_sync_log'
            """)
            
            if not cursor.fetchone():
                print("❌ profile_sync_log table missing")
                return False
            
            print("✅ profile_sync_log table exists")
            
            # Check indexes
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = [idx[0] for idx in cursor.fetchall()]
            
            required_indexes = [
                'idx_gologin_profiles_execution_mode',
                'idx_gologin_profiles_sync',
                'idx_profile_sync_log_profile'
            ]
            
            missing_indexes = [idx for idx in required_indexes if idx not in indexes]
            
            if missing_indexes:
                print(f"⚠️  Missing indexes: {missing_indexes}")
            else:
                print("✅ All required indexes present")
            
            print("\n✅ Migration verification successful!")
            return True
            
    except Exception as e:
        print(f"❌ Verification failed: {e}")
        return False

def main():
    """Main migration function."""
    print("🚀 GoLogin Enhanced Database Migration")
    print("=" * 50)
    
    db_path = 'twitter_accounts.db'
    
    # Check if database exists
    if not os.path.exists(db_path):
        print(f"❌ Database {db_path} not found")
        print("Please run the basic GoLogin setup first")
        return
    
    # Run migration
    success = migrate_gologin_schema(db_path)
    
    if success:
        # Verify migration
        verify_migration(db_path)
        
        print("\n🎯 Next Steps:")
        print("1. Test the enhanced GoLogin manager:")
        print("   python test_gologin_enhanced.py")
        print()
        print("2. Update your environment variables:")
        print("   Add enhanced configuration to .env file")
        print()
        print("3. Start using enhanced features:")
        print("   from gologin_manager_enhanced import EnhancedGoLoginManager")
    else:
        print("\n❌ Migration failed")
        print("Check the error messages above and try again")

if __name__ == "__main__":
    main() 