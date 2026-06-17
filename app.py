import os
from datetime import datetime, timezone
from io import BytesIO

# CRITICAL: Load environment variables FIRST before any config is imported
if os.path.exists('.env'):
    with open('.env', 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip()

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_file, send_from_directory
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from functools import wraps
from sqlalchemy import inspect, or_
from werkzeug.utils import secure_filename
from models import db, User, Transcript, AuditLog, SignDataset, Recording
from forms import RegistrationForm, LoginForm, CreateUserForm, EditUserForm, TranscriptForm, SignDatasetForm
from sign_detector import initialize_detector
from export_utils import TranscriptExporter, get_export_options
from config import *

# Initialize Flask app
app = Flask(__name__)
app.config.from_object('config')

# Initialize extensions
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'

# Initialize sign detector lazily so startup is resilient if the model is unavailable
sign_detector = None


def get_sign_detector():
    """Initialize the detector once and return a reusable instance."""
    global sign_detector
    if sign_detector is None:
        try:
            sign_detector = initialize_detector()
        except Exception as exc:
            app.logger.exception('Failed to initialize sign detector: %s', exc)
            return None
    return sign_detector


@login_manager.user_loader
def load_user(user_id):
    """Load user from database"""
    return db.session.get(User, int(user_id))


def admin_required(f):
    """Decorator to require admin role"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            flash('You need administrator privileges to access this page.', 'danger')
            return redirect(url_for('user_dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def log_audit(action, target_type, target_id=None, details=None):
    """Create audit log entry"""
    if current_user.is_authenticated and current_user.is_admin():
        log = AuditLog(
            admin_id=current_user.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details
        )
        db.session.add(log)
        db.session.commit()


# ============================================================================
# DATABASE HEALTH CHECK & ERROR HANDLERS
# ============================================================================

@app.route('/health')
def health_check():
    """
    Health check endpoint for monitoring and container orchestration.
    Returns database connection status and application health.
    """
    try:
        from sqlalchemy import text
        # Test database connection
        db.session.execute(text('SELECT 1'))
        db.session.commit()
        
        return jsonify({
            'status': 'healthy',
            'database': 'connected',
            'environment': os.getenv('FLASK_ENV', 'development')
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'database': 'disconnected',
            'error': str(e)
        }), 503


@app.errorhandler(Exception)
def handle_db_error(error):
    """
    Global error handler for database and other exceptions.
    Helps diagnose MySQL connection issues.
    """
    # Log the error
    import traceback
    app.logger.error(f"Application error: {error}\n{traceback.format_exc()}")
    
    # Check if it's a database connection error
    error_str = str(error).lower()
    if any(term in error_str for term in ['mysql', 'connection', 'database', '2006', '1045', '1040']):
        return render_template('errors/500.html', 
                             message="Database connection error. Please contact support."), 500
    
    # Generic error response
    return render_template('errors/500.html', 
                         message="An unexpected error occurred. Please try again."), 500


# ============================================================================
# AUTH ROUTES
# ============================================================================

@app.route('/')
def index():
    """Landing page"""
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    """User registration"""
    if current_user.is_authenticated:
        return redirect(url_for('user_dashboard'))

    form = RegistrationForm()
    if form.validate_on_submit():
        # Prevent duplicate accounts from causing database errors.
        existing_user = User.query.filter(
            or_(
                User.username == form.username.data,
                User.email == form.email.data
            )
        ).first()
        if existing_user:
            flash('A user with that username or email already exists.', 'danger')
            return render_template('auth/register.html', form=form)

        selected_role = form.role.data
        user = User(
            username=form.username.data,
            email=form.email.data,
            full_name=form.full_name.data,
            role=selected_role
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()

        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('auth/register.html', form=form)


@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login"""
    if current_user.is_authenticated:
        return redirect(url_for('user_dashboard'))
    
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        
        if user is None or not user.check_password(form.password.data):
            flash('Invalid username or password.', 'danger')
            return redirect(url_for('login'))
        
        if not user.is_active:
            flash('Your account has been deactivated.', 'danger')
            return redirect(url_for('login'))
        
        login_user(user)
        user.last_login = datetime.now(timezone.utc)
        db.session.commit()
        
        next_page = request.args.get('next')
        if next_page and next_page.startswith('/'):
            return redirect(next_page)
        
        return redirect(url_for('user_dashboard' if user.role == 'user' else 'admin_dashboard'))
    
    return render_template('auth/login.html', form=form)


@app.route('/logout')
@login_required
def logout():
    """User logout"""
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


# ============================================================================
# USER ROUTES - Sign Language User
# ============================================================================

@app.route('/dashboard')
@login_required
def user_dashboard():
    """User dashboard"""
    if current_user.is_admin():
        return redirect(url_for('admin_dashboard'))
    
    page = request.args.get('page', 1, type=int)
    transcripts = Transcript.query.filter_by(user_id=current_user.id).order_by(
        Transcript.created_at.desc()
    ).paginate(page=page, per_page=10)
    
    stats = {
        'total_transcripts': Transcript.query.filter_by(user_id=current_user.id).count(),
        'total_duration': db.session.query(db.func.sum(Transcript.session_duration)).filter_by(
            user_id=current_user.id
        ).scalar() or 0
    }
    
    return render_template('user/dashboard.html', transcripts=transcripts, stats=stats)


@app.route('/transcribe', methods=['GET', 'POST'])
@login_required
def transcribe():
    """Sign language transcription interface"""
    if current_user.is_admin():
        return redirect(url_for('admin_dashboard'))
    
    form = TranscriptForm()
    if form.validate_on_submit():
        transcript = Transcript(
            user_id=current_user.id,
            title=form.title.data,
            content=form.content.data,
            status='completed'
        )
        db.session.add(transcript)
        db.session.commit()
        
        flash('Transcript saved successfully!', 'success')
        return redirect(url_for('user_dashboard'))
    
    return render_template('user/transcribe.html', form=form)


@app.route('/transcript/<int:transcript_id>', methods=['GET', 'POST'])
@login_required
def view_transcript(transcript_id):
    """View specific transcript"""
    transcript = Transcript.query.get_or_404(transcript_id)
    
    # Check ownership
    if transcript.user_id != current_user.id and not current_user.is_admin():
        flash('You do not have permission to view this transcript.', 'danger')
        return redirect(url_for('user_dashboard'))
    
    form = TranscriptForm()
    if form.validate_on_submit():
        transcript.title = form.title.data
        transcript.content = form.content.data
        transcript.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        
        flash('Transcript updated successfully!', 'success')
        return redirect(url_for('view_transcript', transcript_id=transcript.id))
    
    elif request.method == 'GET':
        form.title.data = transcript.title
        form.content.data = transcript.content
    
    return render_template('user/view_transcript.html', transcript=transcript, form=form)


@app.route('/transcript/<int:transcript_id>/delete', methods=['POST'])
@login_required
def delete_transcript(transcript_id):
    """Delete transcript"""
    transcript = Transcript.query.get_or_404(transcript_id)
    
    # Check ownership
    if transcript.user_id != current_user.id and not current_user.is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    
    db.session.delete(transcript)
    db.session.commit()
    
    flash('Transcript deleted successfully!', 'success')
    return redirect(url_for('user_dashboard'))


@app.route('/transcript/<int:transcript_id>/export/<format_type>', methods=['GET'])
@login_required
def export_transcript(transcript_id, format_type):
    """Export transcript in specified format (txt, csv, pdf)"""
    transcript = Transcript.query.get_or_404(transcript_id)
    
    # Check ownership
    if transcript.user_id != current_user.id and not current_user.is_admin():
        flash('You do not have permission to export this transcript.', 'danger')
        return redirect(url_for('user_dashboard'))
    
    # Validate format
    valid_formats = {'txt', 'csv', 'pdf'}
    if format_type not in valid_formats:
        flash('Invalid export format.', 'danger')
        return redirect(url_for('view_transcript', transcript_id=transcript.id))
    
    try:
        exporter = TranscriptExporter()
        
        if format_type == 'txt':
            filename, content = exporter.export_txt(transcript)
            return send_file(
                BytesIO(content.encode('utf-8')),
                mimetype='text/plain',
                as_attachment=True,
                download_name=filename
            )
        
        elif format_type == 'csv':
            filename, content = exporter.export_csv(transcript)
            return send_file(
                BytesIO(content),
                mimetype='text/csv',
                as_attachment=True,
                download_name=filename
            )
        
        elif format_type == 'pdf':
            filename, content = exporter.export_pdf(transcript)
            return send_file(
                BytesIO(content),
                mimetype='application/pdf',
                as_attachment=True,
                download_name=filename
            )
    
    except Exception as e:
        print(f"Error exporting transcript: {e}")
        flash(f'Error exporting transcript: {str(e)}', 'danger')
        return redirect(url_for('view_transcript', transcript_id=transcript.id))


# ============================================================================
# ADMIN ROUTES
# ============================================================================

@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    """Admin dashboard with analytics"""
    total_users = User.query.count()
    total_transcripts = Transcript.query.count()
    active_users = User.query.filter_by(is_active=True).count()
    flagged_transcripts = Transcript.query.filter_by(status='flagged').count()
    
    # Recent activity
    recent_transcripts = Transcript.query.order_by(Transcript.created_at.desc()).limit(5).all()
    recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
    
    analytics = {
        'total_users': total_users,
        'total_transcripts': total_transcripts,
        'active_users': active_users,
        'flagged_transcripts': flagged_transcripts
    }
    
    return render_template('admin/dashboard.html', analytics=analytics, 
                         recent_transcripts=recent_transcripts,
                         recent_users=recent_users)


@app.route('/admin/users')
@login_required
@admin_required 
def manage_users():
    """Manage all users"""
    page = request.args.get('page', 1, type=int)
    users = User.query.order_by(User.created_at.desc()).paginate(page=page, per_page=15)
    
    return render_template('admin/manage_users.html', users=users)


@app.route('/admin/users/create', methods=['GET', 'POST'])
@login_required
@admin_required
def create_user():
    """Create new user (admin only)"""
    form = CreateUserForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data,
            full_name=form.full_name.data,
            role=form.role.data
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        
        log_audit('CREATE_USER', 'user', user.id, f"Created user {user.username}")
        flash(f'User {user.username} created successfully!', 'success')
        return redirect(url_for('manage_users'))
    
    return render_template('admin/create_user.html', form=form)


@app.route('/admin/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    """Edit user details (admin only)"""
    user = User.query.get_or_404(user_id)
    form = EditUserForm()
    
    if form.validate_on_submit():
        user.full_name = form.full_name.data
        user.email = form.email.data
        user.role = form.role.data
        user.is_active = form.is_active.data == 'True'
        db.session.commit()
        
        log_audit('UPDATE_USER', 'user', user.id, f"Updated user {user.username}")
        flash(f'User {user.username} updated successfully!', 'success')
        return redirect(url_for('manage_users'))
    
    elif request.method == 'GET':
        form.full_name.data = user.full_name
        form.email.data = user.email
        form.role.data = user.role
        form.is_active.data = str(user.is_active)
    
    return render_template('admin/edit_user.html', user=user, form=form)


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    """Delete user (admin only)"""
    user = User.query.get_or_404(user_id)
    
    # Prevent deleting self
    if user.id == current_user.id:
        return jsonify({'error': 'Cannot delete your own account'}), 400
    
    username = user.username
    db.session.delete(user)
    db.session.commit()
    
    log_audit('DELETE_USER', 'user', user_id, f"Deleted user {username}")
    flash(f'User {username} deleted successfully!', 'success')
    
    return redirect(url_for('manage_users'))


@app.route('/api/admin/users/search', methods=['GET'])
@login_required
@admin_required
def search_users():
    """Search users by name, email, or user ID (API endpoint)"""
    query_text = request.args.get('q', '').strip()
    limit = request.args.get('limit', 50, type=int)
    
    if not query_text or len(query_text) < 1:
        return jsonify({'users': [], 'total': 0})
    
    # Search across multiple fields with partial matches
    from sqlalchemy import or_
    search_query = (
        User.query.filter(
            or_(
                User.username.ilike(f'%{query_text}%'),
                User.email.ilike(f'%{query_text}%'),
                User.full_name.ilike(f'%{query_text}%'),
                User.id == query_text if query_text.isdigit() else False
            )
        )
        .order_by(User.created_at.desc())
        .limit(limit)
    )
    
    users = search_query.all()
    
    # Format results for JSON response
    results = []
    for user in users:
        results.append({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'full_name': user.full_name,
            'role': user.role,
            'is_active': user.is_active,
            'created_at': user.created_at.strftime('%Y-%m-%d'),
            'edit_url': url_for('edit_user', user_id=user.id),
            'delete_url': url_for('delete_user', user_id=user.id),
            'current_user': user.id == current_user.id
        })
    
    return jsonify({
        'users': results,
        'total': len(results),
        'query': query_text
    })


@app.route('/admin/transcripts')
@login_required
@admin_required
def manage_transcripts():
    """View and manage all transcripts"""
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', 'all')
    
    query = Transcript.query
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    
    transcripts = query.order_by(Transcript.created_at.desc()).paginate(page=page, per_page=15)
    
    return render_template('admin/manage_transcripts.html', transcripts=transcripts, 
                         current_status=status_filter)


@app.route('/admin/recordings')
@login_required
@admin_required
def manage_recordings():
    """View all recorded gesture videos for admin review."""
    page = request.args.get('page', 1, type=int)
    recordings = Recording.query.order_by(Recording.created_at.desc()).paginate(
        page=page,
        per_page=15
    )
    return render_template('admin/manage_recordings.html', recordings=recordings)


@app.route('/recordings/<path:filename>')
@login_required
def serve_recording_file(filename):
    """Serve saved recording videos to authorized users."""
    recordings_dir = os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], 'recordings')
    recording = Recording.query.filter_by(file_name=filename).first_or_404()

    if recording.user_id != current_user.id and not current_user.is_admin():
        return jsonify({'error': 'Unauthorized'}), 403

    return send_from_directory(recordings_dir, filename)


@app.route('/admin/transcripts/<int:transcript_id>/flag', methods=['POST'])
@login_required
@admin_required
def flag_transcript(transcript_id):
    """Flag transcript as inappropriate"""
    transcript = Transcript.query.get_or_404(transcript_id)
    transcript.status = 'flagged'
    db.session.commit()
    
    log_audit('FLAG_TRANSCRIPT', 'transcript', transcript_id)
    flash('Transcript flagged successfully!', 'warning')
    
    return redirect(url_for('manage_transcripts'))


@app.route('/admin/transcripts/<int:transcript_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_transcript(transcript_id):
    """Delete transcript (admin only)"""
    transcript = Transcript.query.get_or_404(transcript_id)
    user_id = transcript.user_id
    
    db.session.delete(transcript)
    db.session.commit()
    
    log_audit('DELETE_TRANSCRIPT', 'transcript', transcript_id, f"User: {user_id}")
    flash('Transcript deleted successfully!', 'success')
    
    return redirect(url_for('manage_transcripts'))


@app.route('/admin/analytics')
@login_required
@admin_required
def analytics():
    """Analytics and statistics"""
    users_count = User.query.count()
    transcripts_count = Transcript.query.count()
    
    # Get data for charts
    transcripts_by_status = db.session.query(
        Transcript.status,
        db.func.count(Transcript.id)
    ).group_by(Transcript.status).all()
    
    top_users = db.session.query(
        User.username,
        db.func.count(Transcript.id).label('transcript_count')
    ).join(Transcript).group_by(User.id).order_by(
        db.func.count(Transcript.id).desc()
    ).limit(10).all()
    
    return render_template('admin/analytics.html',
                         users_count=users_count,
                         transcripts_count=transcripts_count,
                         transcripts_by_status=transcripts_by_status,
                         top_users=top_users)


@app.route('/admin/datasets')
@login_required
@admin_required
def manage_datasets():
    """View and manage sign language datasets"""
    page = request.args.get('page', 1, type=int)
    gesture_filter = request.args.get('gesture_type', 'all')
    
    query = SignDataset.query
    if gesture_filter != 'all':
        query = query.filter_by(gesture_type=gesture_filter)
    
    datasets = query.order_by(SignDataset.sign_name).paginate(page=page, per_page=20)
    
    return render_template('admin/manage_datasets.html', datasets=datasets, 
                         current_gesture=gesture_filter)


@app.route('/admin/datasets/create', methods=['GET', 'POST'])
@login_required
@admin_required
def create_dataset():
    """Create a sign dataset entry."""
    form = SignDatasetForm()
    if form.validate_on_submit():
        dataset = SignDataset(
            sign_name=form.sign_name.data.strip(),
            description=form.description.data.strip() or None,
            gesture_type=form.gesture_type.data,
            image_url=form.image_url.data.strip() or None,
            video_url=form.video_url.data.strip() or None
        )
        db.session.add(dataset)
        db.session.commit()
        log_audit('CREATE_DATASET', 'dataset', dataset.id, f"Created dataset {dataset.sign_name}")
        flash('Dataset created successfully!', 'success')
        return redirect(url_for('manage_datasets'))
    return render_template('admin/create_dataset.html', form=form)


@app.route('/admin/datasets/<int:dataset_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_dataset(dataset_id):
    """Edit a sign dataset entry."""
    dataset = SignDataset.query.get_or_404(dataset_id)
    form = SignDatasetForm()

    if form.validate_on_submit():
        dataset.sign_name = form.sign_name.data.strip()
        dataset.description = form.description.data.strip() or None
        dataset.gesture_type = form.gesture_type.data
        dataset.image_url = form.image_url.data.strip() or None
        dataset.video_url = form.video_url.data.strip() or None
        db.session.commit()
        log_audit('UPDATE_DATASET', 'dataset', dataset.id, f"Updated dataset {dataset.sign_name}")
        flash('Dataset updated successfully!', 'success')
        return redirect(url_for('manage_datasets'))

    elif request.method == 'GET':
        form.sign_name.data = dataset.sign_name
        form.description.data = dataset.description
        form.gesture_type.data = dataset.gesture_type
        form.image_url.data = dataset.image_url
        form.video_url.data = dataset.video_url

    return render_template('admin/edit_dataset.html', dataset=dataset, form=form)


@app.route('/admin/datasets/<int:dataset_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_dataset(dataset_id):
    """Delete a sign dataset entry."""
    dataset = SignDataset.query.get_or_404(dataset_id)
    db.session.delete(dataset)
    db.session.commit()
    log_audit('DELETE_DATASET', 'dataset', dataset_id, f"Deleted dataset {dataset.sign_name}")
    flash('Dataset deleted successfully!', 'success')
    return redirect(url_for('manage_datasets'))


@app.route('/admin/audit-logs')
@login_required
@admin_required
def audit_logs():
    """View audit logs for admin activities"""
    page = request.args.get('page', 1, type=int)
    action_filter = request.args.get('action', 'all')
    
    query = AuditLog.query
    if action_filter != 'all':
        query = query.filter_by(action=action_filter)
    
    logs = query.order_by(AuditLog.timestamp.desc()).paginate(page=page, per_page=25)
    
    # Get unique actions for filter dropdown
    unique_actions = db.session.query(AuditLog.action).distinct().all()
    actions = [action[0] for action in unique_actions]
    
    return render_template('admin/audit_logs.html', logs=logs, 
                         current_action=action_filter, actions=actions)


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route('/api/transcribe', methods=['POST'])
@login_required
def api_transcribe():
    """
    API endpoint for real-time transcription with hand gesture detection
    Only responds when hands are actually detected in the frame
    """
    try:
        detector = get_sign_detector()
        if detector is None:
            return jsonify({
                'status': 'error',
                'message': 'Hand detector is not available right now.',
                'hands_detected': 0,
                'detected_sign': None
            }), 503

        # Check if frame data is provided
        if 'frame' not in request.files:
            return jsonify({
                'status': 'no_hand',
                'message': 'No frame data',
                'hands_detected': 0,
                'detected_sign': None
            }), 200
        
        # Read frame from request
        frame_file = request.files['frame']
        frame_data = frame_file.read()
        if not frame_data:
            return jsonify({
                'status': 'no_hand',
                'message': 'Empty frame data',
                'hands_detected': 0,
                'detected_sign': None
            }), 200
        
        # Detect hand gestures in frame
        detection = detector.detect_signs(frame_data)
        
        # Only return gesture data if hands were detected
        if detection['has_hands']:
            return jsonify({
                'status': 'hand_detected',
                'hands_detected': detection['hands_detected'],
                'detected_sign': detection['detected_sign'],
                'gestures': detection['gestures'],
                'confidence': detection['confidence'],
                'hand_positions': detection['hand_positions'],
                'landmarks': detection['landmarks'],
                'keypoints': detection.get('hand_keypoints', [])
            }), 200
        else:
            return jsonify({
                'status': 'no_hand',
                'hands_detected': 0,
                'detected_sign': None,
                'confidence': 0.0,
                'landmarks': [],
                'keypoints': []
            }), 200
            
    except Exception as e:
        print(f"Error in transcription API: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
            'hands_detected': 0
        }), 500


@app.route('/api/upload-recording', methods=['POST'])
@login_required
def api_upload_recording():
    """Save a recorded gesture video to the project and track it in the database."""
    try:
        if 'video' not in request.files:
            return jsonify({'status': 'error', 'message': 'No video file provided'}), 400

        video_file = request.files['video']
        if video_file.filename == '':
            return jsonify({'status': 'error', 'message': 'No selected video file'}), 400

        title = request.form.get('title', 'Recorded Gesture Session')
        duration_value = request.form.get('duration', '0')
        duration = int(duration_value) if str(duration_value).isdigit() else 0

        recordings_dir = os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], 'recordings')
        os.makedirs(recordings_dir, exist_ok=True)

        safe_name = secure_filename(video_file.filename)
        if not safe_name:
            safe_name = f"gesture_{current_user.id}_{int(datetime.now(timezone.utc).timestamp())}.webm"

        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        filename = f"{current_user.id}_{timestamp}_{safe_name}"
        file_path = os.path.join(recordings_dir, filename)
        video_file.save(file_path)

        recording = Recording(
            user_id=current_user.id,
            title=title,
            file_name=filename,
            file_path=file_path,
            mime_type=video_file.mimetype or 'video/webm',
            duration=duration,
            status='ready'
        )
        db.session.add(recording)
        db.session.commit()

        return jsonify({
            'status': 'success',
            'recording_id': recording.id,
            'message': 'Video recording saved successfully',
            'file_url': url_for('serve_recording_file', filename=filename)
        }), 200
    except Exception as e:
        print(f"Error saving recording: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/start-session', methods=['POST'])
@login_required
def api_start_session():
    """Start a transcription session"""
    data = request.get_json(silent=True) or {}
    
    transcript = Transcript(
        user_id=current_user.id,
        title=data.get('title', 'Untitled Session'),
        status='draft'
    )
    db.session.add(transcript)
    db.session.commit()
    
    return jsonify({
        'status': 'success',
        'session_id': transcript.id
    })


@app.route('/api/save-session/<int:session_id>', methods=['POST'])
@login_required
def api_save_session(session_id):
    """Save transcription session"""
    transcript = Transcript.query.get_or_404(session_id)
    
    # Verify ownership
    if transcript.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json(silent=True) or {}
    transcript.content = data.get('content', '')
    transcript.raw_content = data.get('raw_content', [])
    transcript.confidence_scores = data.get('confidence_scores', [])
    transcript.session_duration = data.get('duration', 0)
    transcript.status = 'completed'
    
    db.session.commit()
    
    return jsonify({
        'status': 'success',
        'message': 'Session saved successfully'
    })


# ============================================================================
# GESTURE TRAINING API
# ============================================================================

@app.route('/api/train-gesture', methods=['POST'])
@login_required
def api_train_gesture():
    """
    Train the sign detector on a new ASL gesture
    Users can teach the system custom signs
    """
    try:
        data = request.get_json()
        
        if not data or 'gesture_name' not in data or 'frame' not in data:
            return jsonify({
                'status': 'error',
                'message': 'Missing gesture_name or frame data'
            }), 400
        
        gesture_name = data['gesture_name'].upper().strip()
        frame_data = data['frame']
        
        # Limit gesture name length
        if len(gesture_name) > 50:
            return jsonify({
                'status': 'error',
                'message': 'Gesture name too long (max 50 characters)'
            }), 400
        
        # Detect Hand gesture in frame
        detection = sign_detector.detect_signs(frame_data)
        
        # Only train if hands are detected
        if not detection['has_hands']:
            return jsonify({
                'status': 'error',
                'message': 'No hands detected in frame. Please show your hand clearly.'
            }), 400
        
        # Get landmarks from first detected hand
        if detection['landmarks'] and len(detection['landmarks']) > 0:
            landmarks = detection['landmarks'][0]
            
            # Train the gesture
            sign_detector.train_gesture(gesture_name, landmarks)
            
            return jsonify({
                'status': 'success',
                'message': f'Gesture "{gesture_name}" trained successfully',
                'gesture_name': gesture_name,
                'samples': sign_detector.get_gesture_samples(gesture_name)
            }), 200
        else:
            return jsonify({
                'status': 'error',
                'message': 'Could not extract hand landmarks'
            }), 400
            
    except Exception as e:
        print(f"Error training gesture: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/trained-gestures', methods=['GET'])
@login_required
def api_list_trained_gestures():
    """
    Get list of all trained custom gestures
    """
    try:
        trained = sign_detector.get_trained_gestures()
        
        # Get sample counts for each gesture
        gesture_info = {}
        for gesture in trained:
            gesture_info[gesture] = {
                'samples': sign_detector.get_gesture_samples(gesture)
            }
        
        return jsonify({
            'status': 'success',
            'trained_gestures': gesture_info,
            'total': len(trained)
        }), 200
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/clear-training', methods=['POST'])
@login_required
@admin_required
def api_clear_training():
    """
    Clear all trained custom gestures (admin only)
    """
    try:
        sign_detector.clear_gesture_training()
        
        return jsonify({
            'status': 'success',
            'message': 'All trained gestures cleared'
        }), 200
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return render_template('errors/404.html'), 404


@app.errorhandler(403)
def forbidden(error):
    """Handle 403 errors"""
    return render_template('errors/403.html'), 403


@app.errorhandler(500)
def server_error(error):
    """Handle 500 errors"""
    db.session.rollback()
    return render_template('errors/500.html'), 500


# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

def ensure_database_initialized():
    """Create missing database tables once at app startup."""
    with app.app_context():
        try:
            # Ensure instance directory exists (for SQLite)
            instance_path = app.config.get('INSTANCE_PATH', 'instance')
            os.makedirs(instance_path, exist_ok=True)
            
            inspector = inspect(db.engine)
            required_tables = [
                User.__tablename__,
                Transcript.__tablename__,
                Recording.__tablename__,
                SignDataset.__tablename__,
                AuditLog.__tablename__,
            ]
            existing_tables = inspector.get_table_names()
            missing_tables = [table for table in required_tables if table not in existing_tables]

            if missing_tables:
                app.logger.info("Database tables missing, creating: %s", ", ".join(missing_tables))
                db.create_all()
                app.logger.info("Database initialization complete")
            else:
                app.logger.info("Database already initialized, skipping table creation")
        except Exception as e:
            app.logger.error("Database initialization failed: %s", str(e))
            print(f"⚠️  Database initialization error: {e}")
            print("   The app may not work correctly until the database is set up.")
            print("   Run: flask init-db")


@app.cli.command()
def init_db():
    """Initialize database with sample data"""
    db.create_all()
    
    # Create sample admin user if it doesn't exist
    if User.query.filter_by(username='admin').first() is None:
        admin = User(
            username='admin',
            email='admin@sign.com',
            full_name='Administrator',
            role='admin'
        )
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print('Database initialized with admin user (username: admin, password: admin123)')
    else:
        print('Database already initialized')


@app.cli.command()
def seed_signs():
    """Seed sample sign data"""
    sample_signs = [
        ('A', 'Letter A in ASL', 'letter'),
        ('B', 'Letter B in ASL', 'letter'),
        ('HELLO', 'Greeting sign', 'phrase'),
        ('THANK YOU', 'Expression of gratitude', 'phrase'),
        ('YES', 'Affirmative response', 'phrase'),
        ('NO', 'Negative response', 'phrase'),
    ]
    
    for sign_name, description, gesture_type in sample_signs:
        if SignDataset.query.filter_by(sign_name=sign_name).first() is None:
            sign = SignDataset(
                sign_name=sign_name,
                description=description,
                gesture_type=gesture_type
            )
            db.session.add(sign)
    
    db.session.commit()
    print('Sign database seeded successfully')


if __name__ == '__main__':
    ensure_database_initialized()
    app.run(debug=True, host='0.0.0.0', port=5000)
