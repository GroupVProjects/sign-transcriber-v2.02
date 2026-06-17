from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    """User model for both Sign Language Users and Administrators"""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default='user', nullable=False)  # 'user' or 'admin'
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )
    last_login = db.Column(db.DateTime)

    # Relationships
    transcripts = db.relationship(
        'Transcript',
        backref='user',
        lazy=True,
        cascade='all, delete-orphan'
    )

    def set_password(self, password):
        """Hash and set password"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Verify password"""
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        """Check if user is admin"""
        return self.role == 'admin'

    def __repr__(self):
        return f'<User {self.username}>'


class Transcript(db.Model):
    """Transcript model for storing transcription sessions"""
    __tablename__ = 'transcripts'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id'),
        nullable=False,
        index=True
    )
    title = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=True)
    raw_content = db.Column(db.JSON, nullable=True)  # Stores detected signs as JSON
    confidence_scores = db.Column(db.JSON, nullable=True)
    session_duration = db.Column(db.Integer, nullable=True)  # Duration in seconds
    video_url = db.Column(db.String(500), nullable=True)
    status = db.Column(
        db.String(20),
        default='completed'
    )  # 'draft', 'completed', 'flagged'
    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        index=True
    )
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f'<Transcript {self.id} by {self.user.username}>'


class Recording(db.Model):
    """Recorded webcam gesture sessions for review and admin monitoring."""
    __tablename__ = 'recordings'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id'),
        nullable=False,
        index=True
    )
    title = db.Column(db.String(255), nullable=False)
    file_name = db.Column(db.String(255), nullable=False, unique=True)
    file_path = db.Column(db.String(500), nullable=False)
    mime_type = db.Column(db.String(100), nullable=False, default='video/webm')
    duration = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(30), nullable=False, default='ready')
    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        index=True
    )

    user = db.relationship('User', backref=db.backref('recordings', lazy=True))

    def __repr__(self):
        return f'<Recording {self.id} by user {self.user_id}>'


class SignDataset(db.Model):
    """Sign dataset model for managing sign language vocabulary"""
    __tablename__ = 'sign_dataset'

    id = db.Column(db.Integer, primary_key=True)
    sign_name = db.Column(
        db.String(100),
        nullable=False,
        unique=True,
        index=True
    )
    description = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.String(500), nullable=True)
    video_url = db.Column(db.String(500), nullable=True)
    gesture_type = db.Column(
        db.String(50),
        nullable=False
    )  # 'letter', 'number', 'phrase'
    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    def __repr__(self):
        return f'<SignDataset {self.sign_name}>'


class AuditLog(db.Model):
    """Audit log for tracking admin activities"""
    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id'),
        nullable=False
    )
    action = db.Column(db.String(100), nullable=False)
    target_type = db.Column(
        db.String(50),
        nullable=False
    )  # 'user', 'transcript', 'dataset'
    target_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.Text, nullable=True)
    timestamp = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        index=True
    )

    def __repr__(self):
        return f'<AuditLog {self.action} by admin {self.admin_id}>'