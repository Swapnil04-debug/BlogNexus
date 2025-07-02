# Updated app.py: contact form handling, category & technology filtering
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from flask import render_template, request, flash, redirect, url_for
from flask_mail import Message
import mysql.connector
from mysql.connector import Error
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_wtf import CSRFProtect
from config import Config
from types import SimpleNamespace
import os
from flask_mail import Mail
from mysql.connector import Error
from mysql.connector import Error, IntegrityError
from functools import wraps
from flask import session, redirect, url_for, flash
from datetime import datetime, timedelta
import random

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


app = Flask(__name__)
app.config.from_object(Config)


app = Flask(__name__, template_folder='templates', static_folder='static')
app.config.from_object(Config)
mail = Mail(app)

csrf = CSRFProtect(app)

app.secret_key = app.config['SECRET_KEY']
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB limit


ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db_connection():
    return mysql.connector.connect(
        host=app.config['DB_HOST'],
        user=app.config['DB_USER'],
        password=app.config['DB_PASSWORD'],
        database=app.config['DB_NAME']
    )

@app.context_processor
@app.context_processor
def inject_globals():
    # Inject current_user
    user = SimpleNamespace(name='Guest')
    if 'user_id' in session:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute('SELECT email FROM users WHERE user_id = %s', (session['user_id'],))
            row = cursor.fetchone()
            if row:
                user = SimpleNamespace(name=row['email'])
        except Error:
            pass
        finally:
            cursor.close()
            conn.close()

    # Inject categories
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT id, name FROM categories ORDER BY name')
        cats = cursor.fetchall()
    except Error:
        cats = []
    finally:
        cursor.close()
        conn.close()

    return {
        'current_user': user,
        'categories': cats
    }
@app.route('/')

def index():
    page = request.args.get('page', 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.*, u.email AS author_email, c.name AS category_name
            FROM posts p
            JOIN users u ON p.author_id = u.user_id
            LEFT JOIN categories c ON p.category_id = c.id
            ORDER BY p.created_at DESC
            LIMIT %s OFFSET %s
        """, (per_page, offset))
        posts = cursor.fetchall()
        cursor.execute('SELECT FOUND_ROWS() AS total')
        total = cursor.fetchone()['total']
    except Error:
        flash('Error fetching posts', 'danger')
        posts, total = [], 0
    finally:
        cursor.close()
        conn.close()

    total_pages = (total + per_page - 1) // per_page
    return render_template('index.html', posts=posts, page=page, total_pages=total_pages)

@app.route('/category/<int:category_id>')

def filter_by_category(category_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # 1) Fetch filtered posts
        cursor.execute("""
            SELECT p.*, u.email AS author_email, c.name AS category_name
            FROM posts p
            JOIN users u ON p.author_id = u.user_id
            JOIN categories c ON p.category_id = c.id
            WHERE c.id = %s
            ORDER BY p.created_at DESC
        """, (category_id,))
        posts = cursor.fetchall()

        # 2) Fetch all categories for the sidebar
        cursor.execute("SELECT id, name FROM categories ORDER BY name")
        categories_list = cursor.fetchall()

    except Error:
        flash('Error loading category posts', 'danger')
        posts = []
        categories_list = []
    finally:
        cursor.close()
        conn.close()

    # 3) Determine selected category name
    selected = next((c['name'] for c in categories_list if c['id']==category_id), None)

    # 4) Pass both lists into template
    return render_template(
        'categories.html',
        posts=posts,
        categories=categories_list,
        current_category=selected
    )
@app.route('/admin/messages')
@login_required
def admin_messages():
    if 'user_id' not in session:
        flash("Please log in to view messages.", "warning")
        return redirect(url_for('login'))

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        # ← Change this to match your insert table
        cursor.execute("""
            SELECT id, name, email, subject, message AS content, created_at
            FROM contact_messages
            ORDER BY created_at DESC
        """)
        msgs = cursor.fetchall()
    except Error as e:
        flash(f"Error loading messages: {e}", "danger")
        msgs = []
    finally:
        cursor.close()
        conn.close()

    return render_template('admin_messages.html', messages=msgs)

RESEND_LIMIT = 3
RESEND_WINDOW = timedelta(hours=1)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    step = request.args.get('step', 'form')

    if request.method == 'POST':
        # 1️⃣ Form submission - initial email/password
        if step == 'form' and not request.form.get('otp'):
            email = request.form['email']
            password = request.form['password']

            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT 1 FROM users WHERE email = %s', (email,))
                if cursor.fetchone():
                    flash('This email is already registered. Please log in.', 'danger')
                    return render_template('signup.html', step='form')

                otp = f"{random.randint(0, 999999):06d}"
                expires = datetime.utcnow() + timedelta(minutes=5)
                now = datetime.utcnow()

                # Create unverified user with OTP info
                cursor.execute("""
                    INSERT INTO users (email, password, signup_otp, otp_expires_at, is_verified, otp_resend_count, otp_last_resend_time)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (email, generate_password_hash(password), otp, expires, False, 0, now))
                conn.commit()

                session.update({
                    'pending_email': email,
                    'pending_otp': otp,
                    'pending_expires': expires.isoformat(),
                    'otp_resend_count': 0,
                    'otp_last_resend_time': now.isoformat()
                })

                msg = Message(
                    subject="Your BlogNexus Signup Code",
                    recipients=[email],
                    body=f"Your verification code is: {otp}\nIt expires in 5 minutes."
                )
                mail.send(msg)
                flash("OTP sent—check your inbox!", "info")
                return render_template('signup.html', step='otp')

            except Error as e:
                flash(f'Database error: {e}', 'danger')
                return render_template('signup.html', step='form')
            finally:
                cursor.close()
                conn.close()

        # 2️⃣ OTP verification or resend
        if step == 'otp' or request.form.get('otp'):
            action = request.form.get('action', 'verify')

            # Handle resend
            if action == 'resend':
                last_resend = datetime.fromisoformat(session.get('otp_last_resend_time', datetime.min.isoformat()))
                resend_count = session.get('otp_resend_count', 0)

                if resend_count >= RESEND_LIMIT and datetime.utcnow() - last_resend < RESEND_WINDOW:
                    flash("Resend limit reached. Try again later.", "danger")
                    return render_template('signup.html', step='otp')

                otp = f"{random.randint(0, 999999):06d}"
                expires = datetime.utcnow() + timedelta(minutes=5)
                now = datetime.utcnow()

                session['pending_otp'] = otp
                session['pending_expires'] = expires.isoformat()
                session['otp_resend_count'] = resend_count + 1
                session['otp_last_resend_time'] = now.isoformat()

                try:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE users
                        SET signup_otp = %s, otp_expires_at = %s,
                            otp_resend_count = %s, otp_last_resend_time = %s
                        WHERE email = %s
                    """, (otp, expires, resend_count + 1, now, session['pending_email']))
                    conn.commit()
                except Error as e:
                    flash(f"Error updating OTP: {e}", "danger")
                    return render_template('signup.html', step='otp')
                finally:
                    cursor.close()
                    conn.close()

                msg = Message(
                    subject="Your new BlogNexus signup code",
                    recipients=[session['pending_email']],
                    body=f"Your new code is: {otp} (valid for 5 minutes)"
                )
                mail.send(msg)
                flash("New OTP sent.", "info")
                return render_template('signup.html', step='otp')

            # Verify OTP
            entered_otp = request.form.get('otp')
            stored_otp = session.get('pending_otp')
            expires_at = datetime.fromisoformat(session.get('pending_expires', ''))

            if entered_otp == stored_otp and datetime.utcnow() < expires_at:
                try:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE users
                        SET is_verified = %s
                        WHERE email = %s
                    """, (True, session['pending_email']))
                    conn.commit()
                except Error as e:
                    flash(f'Database error: {e}', 'danger')
                    return render_template('signup.html', step='form')
                finally:
                    cursor.close()
                    conn.close()

                for key in ('pending_email', 'pending_otp', 'pending_expires',
                            'otp_resend_count', 'otp_last_resend_time'):
                    session.pop(key, None)

                flash("Signup complete! You may now log in.", "success")
                return redirect(url_for('login'))
            else:
                flash("Invalid or expired OTP. Please try again.", "danger")
                return render_template('signup.html', step='otp')

    # 3️⃣ Fallback GET
    return render_template('signup.html', step=step)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                'SELECT user_id, password, is_verified FROM users WHERE email = %s', 
                (email,)
            )
            user = cursor.fetchone()
            if user:
                if not user['is_verified']:
                    flash('Please verify your email before logging in.', 'danger')
                elif check_password_hash(user['password'], password):
                    session['user_id'] = user['user_id']
                    flash('Logged in successfully.', 'success')
                    return redirect(url_for('dashboard'))
                else:
                    flash('Invalid password.', 'danger')
            else:
                flash('User not found.', 'danger')
        except Error as e:
            flash(f'Database error: {e}', 'danger')
        finally:
            cursor.close()
            conn.close()

    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Fetch user's posts
        cursor.execute("""
            SELECT posts.*, users.email AS author_email
            FROM posts
            JOIN users ON posts.author_id = users.user_id
            WHERE posts.author_id = %s
            ORDER BY posts.created_at DESC
        """, (session['user_id'],))
        posts = cursor.fetchall()

        # Count total posts across all users
        cursor.execute('SELECT COUNT(*) AS total_posts FROM posts')
        total_posts = cursor.fetchone()['total_posts']

    except Error:
        posts = []
        total_posts = 0
        flash('Error loading dashboard', 'danger')
    finally:
        cursor.close()
        conn.close()

    return render_template('dashboard.html', posts=posts, total_posts=total_posts)

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/create', methods=['GET', 'POST'])
@login_required
def create_post():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        category_id = request.form.get('category_id', type=int)
        image = request.files.get('image')
        filename = None
        if image and image.filename and allowed_file(image.filename):
            filename = secure_filename(image.filename)
            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO posts (title, content, image, author_id, category_id) VALUES (%s, %s, %s, %s, %s)",
            (title, content, filename, session['user_id'],category_id)
        )
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for('index'))
    return render_template('create.html')

@app.route('/post/<int:id>')
def view_post(id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('''
            SELECT posts.*, users.email AS author_email
            FROM posts
            JOIN users ON posts.author_id = users.user_id
            WHERE posts.id = %s
        ''', (id,))
        post = cursor.fetchone()
        if not post:
            abort(404)
    except Error:
        abort(500)
    finally:
        cursor.close()
        conn.close()
    return render_template('post.html', post=post)

@app.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_post(id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM posts WHERE id = %s', (id,))
        post = cursor.fetchone()
        if not post or post['author_id'] != session['user_id']:
            abort(403)

        if request.method == 'POST':
            title       = request.form['title']
            content     = request.form['content']
            image_file  = request.files.get('image')
            filename    = post['image']  # default to old filename

            # if a new image was uploaded, save it and update filename
            if image_file and image_file.filename and allowed_file(image_file.filename):
                filename = secure_filename(image_file.filename)
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

            cursor.execute(
                'UPDATE posts SET title=%s, content=%s, image=%s WHERE id=%s',
                (title, content, filename, id)
            )
            conn.commit()
            flash('Post updated.', 'success')
            return redirect(url_for('view_post', id=id))

    except Error as e:
        flash(f'Error editing post: {e}', 'danger')
    finally:
        cursor.close()
        conn.close()

    return render_template('edit.html', post=post)

@app.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete_post(id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT author_id FROM posts WHERE id = %s', (id,))
        post = cursor.fetchone()
        if not post or post['author_id'] != session['user_id']:
            abort(403)
        cursor.execute('DELETE FROM posts WHERE id = %s', (id,))
        conn.commit()
        flash('Post deleted.', 'info')
    except Error as e:
        flash(f'Error deleting post: {e}', 'danger')
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for('index'))

@app.route('/categories')
def categories():
    name = request.args.get('name')  # optional filter
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        base_query = """
            SELECT posts.*, users.email AS author_email
            FROM posts
            JOIN users ON posts.author_id = users.user_id
        """
        params = []
        if name:
            base_query += " WHERE LOWER(posts.category) = %s"
            params.append(name.lower())
        base_query += " ORDER BY posts.created_at DESC"
        cursor.execute(base_query, params)
        posts = cursor.fetchall()
    except Error as e:
        flash(f'Error loading categories: {e}', 'danger')
        posts = []
        name = None
    finally:
        cursor.close()
        conn.close()
    return render_template('categories.html', posts=posts, current_category=name)

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        subject = request.form['subject']
        message = request.form['message']

        try:
            # Save to database
            conn = mysql.connector.connect(
                host=app.config['DB_HOST'],
                user=app.config['DB_USER'],
                password=app.config['DB_PASSWORD'],
                database=app.config['DB_NAME']
            )
            cursor = conn.cursor()
            query = "INSERT INTO contact_messages (name, email, subject, message) VALUES (%s, %s, %s, %s)"
            cursor.execute(query, (name, email, subject, message))
            conn.commit()
            cursor.close()
            conn.close()

            # Email body with branding and emojis
            msg_body = f"""
📬 New message from BlogNexus contact form:

🧑 Name: {name}
📧 Email: {email}
📝 Subject: {subject}

💬 Message:
{message}
            """

            # Send branded email
            msg = Message(
                subject=f"📨 BlogNexus Contact: {subject}",
                sender=("BlogNexus", app.config['MAIL_USERNAME']),
                recipients=[app.config['MAIL_USERNAME']],
                body=msg_body
            )
            mail.send(msg)
            flash('Your message has been sent successfully and saved to the database ✅', 'success')

        except Exception as e:
            import traceback
            traceback.print_exc()
            flash(f'Error: {e}', 'danger')

        return redirect(url_for('contact'))

    return render_template('contact.html')


@app.route('/technology')
def technology():
    # Render the standalone technology page
    return render_template('technology.html')

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5001)
