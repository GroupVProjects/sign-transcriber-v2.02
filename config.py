import os
from datetime import timedelta
from pathlib import Path

# Load .env automatically when config is imported so scripts and CLI commands
# can discover DATABASE_URL in local development and in deployment pipelines.
from dotenv import load_dotenv
base_dir = Path(__file__).resolve().parent
dotenv_path = base_dir / '.env'
if dotenv_path.exists():
    load_dotenv(dotenv_path)

# ============================================================================
# ENVIRONMENT DETECTION
# ============================================================================
ENV = os.getenv('FLASK_ENV', 'development')
IS_PRODUCTION = ENV == 'production'

# ============================================================================
# DATABASE CONFIGURATION
# ============================================================================

def get_database_uri():
    """
    Get database URI - MANDATORY Railway MySQL when DATABASE_URL is set.
    
    Production Requirement:
    - DATABASE_URL must be set in environment variables
    - Only Railway MySQL is supported
    - No SQLite fallback allowed
    """
    database_url = os.getenv('DATABASE_URL')
    
    if not database_url:
        raise ValueError(
            "CRITICAL: DATABASE_URL environment variable is not set!\n"
            "The application REQUIRES Railway MySQL database.\n"
            "Please set DATABASE_URL in your .env file:\n"
            "  DATABASE_URL=mysql+pymysql://user:password@host:port/database\n"
            "This is mandatory for all environments."
        )
    
    # Railway MySQL - Ensure it uses pymysql driver
    if database_url.startswith('mysql://'):
        # Convert mysql:// to mysql+pymysql:// if needed
        database_url = database_url.replace('mysql://', 'mysql+pymysql://', 1)
    
    print(f"✅ Using Railway MySQL Database")
    print(f"   URL: {database_url.split('@')[0]}@{database_url.split('@')[1] if '@' in database_url else 'railway'}")
    return database_url

SQLALCHEMY_DATABASE_URI = get_database_uri()
SQLALCHEMY_TRACK_MODIFICATIONS = False

# Connection pooling configuration (important for production)
SQLALCHEMY_ENGINE_OPTIONS = {
    'pool_pre_ping': True,  # Test connections before using (verify they're alive)
    'pool_recycle': 3600,    # Recycle connections after 1 hour (MySQL default)
    'pool_size': 10,         # Number of connections to keep in pool
    'max_overflow': 20,      # Maximum excess connections
    'echo': False,           # Set to True for SQL debugging
}

# ============================================================================
# SECRET KEY & SECURITY
# ============================================================================
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise ValueError("SECRET_KEY environment variable must be set in production!")
    SECRET_KEY = 'dev-secret-key-change-before-deployment'

# ============================================================================
# SESSION CONFIGURATION
# ============================================================================
PERMANENT_SESSION_LIFETIME = timedelta(days=7)

# Security settings - depends on environment
SESSION_COOKIE_SECURE = IS_PRODUCTION  # True in production (HTTPS only)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'

# ============================================================================
# UPLOAD CONFIGURATION
# ============================================================================
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size
UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', 'uploads')
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv'}

# Create upload folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ============================================================================
# AI MODEL CONFIGURATION
# ============================================================================
MODEL_CONFIG = {
    'confidence_threshold': 0.7,
    'detection_fps': 30,
    'supported_signs': 'A-Z'
}

# ============================================================================
# DEBUG & LOGGING
# ============================================================================
DEBUG = not IS_PRODUCTION
TESTING = os.getenv('TESTING', 'False').lower() == 'true'
