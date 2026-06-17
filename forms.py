from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, SelectField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, ValidationError, Length
from models import User

class RegistrationForm(FlaskForm):
    """Registration form for new users"""
    username = StringField('Username', validators=[
        DataRequired(),
        Length(min=3, max=80)
    ])
    email = StringField('Email', validators=[
        DataRequired(),
        Email()
    ])
    full_name = StringField('Full Name', validators=[
        DataRequired(),
        Length(min=2, max=120)
    ])
    role = SelectField('Account Type', choices=[
        ('user', 'Regular User'),
        ('admin', 'Administrator')
    ], default='user', validators=[DataRequired()])
    password = PasswordField('Password', validators=[
        DataRequired(),
        Length(min=6, message='Password must be at least 6 characters')
    ])
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match')
    ])
    submit = SubmitField('Register')
    
    def validate_username(self, username):
        """Check if username already exists"""
        user = User.query.filter_by(username=username.data).first()
        if user:
            raise ValidationError('Username already taken. Please choose a different one.')
    
    def validate_email(self, email):
        """Check if email already exists"""
        user = User.query.filter_by(email=email.data).first()
        if user:
            raise ValidationError('Email already registered. Please use a different email.')


class LoginForm(FlaskForm):
    """Login form for authenticated users"""
    username = StringField('Username', validators=[
        DataRequired()
    ])
    password = PasswordField('Password', validators=[
        DataRequired()
    ])
    submit = SubmitField('Login')


class CreateUserForm(FlaskForm):
    """Admin form to create new users"""
    username = StringField('Username', validators=[
        DataRequired(),
        Length(min=3, max=80)
    ])
    email = StringField('Email', validators=[
        DataRequired(),
        Email()
    ])
    full_name = StringField('Full Name', validators=[
        DataRequired(),
        Length(min=2, max=120)
    ])
    role = SelectField('Role', choices=[('user', 'Sign Language User'), ('admin', 'Administrator')])
    password = PasswordField('Temporary Password', validators=[
        DataRequired(),
        Length(min=6)
    ])
    submit = SubmitField('Create User')
    
    def validate_username(self, username):
        """Check if username already exists"""
        user = User.query.filter_by(username=username.data).first()
        if user:
            raise ValidationError('Username already taken.')
    
    def validate_email(self, email):
        """Check if email already exists"""
        user = User.query.filter_by(email=email.data).first()
        if user:
            raise ValidationError('Email already registered.')


class EditUserForm(FlaskForm):
    """Admin form to edit existing users"""
    full_name = StringField('Full Name', validators=[
        DataRequired(),
        Length(min=2, max=120)
    ])
    email = StringField('Email', validators=[
        DataRequired(),
        Email()
    ])
    role = SelectField('Role', choices=[('user', 'Sign Language User'), ('admin', 'Administrator')])
    is_active = SelectField('Status', choices=[(True, 'Active'), (False, 'Inactive')])
    submit = SubmitField('Update User')


class TranscriptForm(FlaskForm):
    """Form for creating/editing transcripts"""
    title = StringField('Transcript Title', validators=[
        DataRequired(),
        Length(min=1, max=255)
    ])
    content = StringField('Transcription Content', validators=[
        DataRequired()
    ])
    submit = SubmitField('Save Transcript')


class SignDatasetForm(FlaskForm):
    """Form for creating/editing sign datasets."""
    sign_name = StringField('Sign Name', validators=[
        DataRequired(),
        Length(min=1, max=100)
    ])
    description = TextAreaField('Description', validators=[
        Length(max=500)
    ])
    gesture_type = SelectField('Type', choices=[
        ('letter', 'Letter'),
        ('number', 'Number'),
        ('phrase', 'Phrase')
    ], validators=[DataRequired()])
    image_url = StringField('Image URL', validators=[
        Length(max=500)
    ])
    video_url = StringField('Video URL', validators=[
        Length(max=500)
    ])
    submit = SubmitField('Save Dataset')
