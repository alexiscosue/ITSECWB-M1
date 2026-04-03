from flask import Flask, render_template, redirect, url_for, request, session, flash, jsonify
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector
from mysql.connector import Error
from werkzeug.utils import secure_filename
import os
import json
from contextlib import contextmanager
from dotenv import load_dotenv
import re
import uuid
from datetime import datetime, timedelta
from email_validator import validate_email, EmailNotValidError
import phonenumbers
from phonenumbers.phonenumberutil import NumberParseException
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
import traceback

load_dotenv()

DEBUG = True

app = Flask(__name__, static_folder='assets', static_url_path='/assets')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=1)
app.config['DEBUG'] = DEBUG  # change to False in production
app.secret_key = 'your_secret_key_here'

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=not DEBUG,  # True in production (HTTPS)
    SESSION_COOKIE_SAMESITE='Lax'
)

# --- Logging Setup ---
LOG_FOLDER = "logs"
os.makedirs(LOG_FOLDER, exist_ok=True)

log_file = os.path.join(LOG_FOLDER, "app.log")

handler = RotatingFileHandler(
    log_file,
    maxBytes=5 * 1024 * 1024,  # 5MB per file
    backupCount=5              # keep old logs (no overwrite)
)

formatter = logging.Formatter(
    '%(asctime)s | %(levelname)s | %(message)s'
)

handler.setFormatter(formatter)
handler.setLevel(logging.INFO)

app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)

def log_auth(action, email, status, ip):
    app.logger.info(f"[AUTH] action={action} email={email} status={status} ip={ip}")

def log_transaction(user_id, action, details):
    app.logger.info(f"[TRANSACTION] user_id={user_id} action={action} details={details}")

def log_admin(admin_id, action, target, details=""):
    app.logger.warning(f"[ADMIN] admin_id={admin_id} action={action} target={target} details={details}")

def log_session(user_id, action, ip, details=""):
    app.logger.info(f"[SESSION] user_id={user_id} action={action} ip={ip} details={details}")

# --- IP-Based Rate Limiting ---
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

login_attempts = {}  # {email: {'count': int, 'last_attempt': datetime}}
MAX_ATTEMPTS = 5
LOCKOUT_TIME = timedelta(minutes=5)

MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Configure upload folder for product images
UPLOAD_FOLDER = os.path.join(app.static_folder, 'images')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Configure upload folder for profile photos
PROFILE_UPLOAD_FOLDER = os.path.join(app.static_folder, 'profiles')
os.makedirs(PROFILE_UPLOAD_FOLDER, exist_ok=True)  # make sure folder exists

def get_db_connection():
    try:
        connection = mysql.connector.connect(
            host=os.getenv('DB_HOST'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_NAME'),
            autocommit=False
        )
        return connection
    except Error as e:
        print("MySQL connection error:", e)
        return None

@contextmanager
def db_transaction():
    """Context manager that handles database transactions with automatic rollback on errors"""
    conn = get_db_connection()
    if not conn:
        raise Exception("Failed to connect to database")
    
    try:
        cursor = conn.cursor(dictionary=True)
        yield conn, cursor
        conn.commit()
        print("Transaction committed successfully")
    except Exception as e:
        conn.rollback()
        print(f"Transaction rolled back due to error: {e}")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# --- Login Required Decorator ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# --- Role-Specific Decorators ---
def staff_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login to access this page.', 'warning')
            return redirect(url_for('login'))
        if session.get('role') not in ['staff', 'admin']:
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login to access this page.', 'warning')
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def detect_image_type(stream):
    """Detect image type from file header bytes (replaces removed imghdr module)."""
    header = stream.read(32)
    stream.seek(0)
    if header[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if header[:3] == b'\xff\xd8\xff':
        return 'jpeg'
    if header[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    return None

def strong_password(password):
    return (len(password) >= 8 and
            re.search(r"[A-Z]", password) and
            re.search(r"[a-z]", password) and
            re.search(r"[0-9]", password))

@app.before_request
def session_timeout_handler():
    # 🚫 Skip static + session-check routes
    if request.endpoint and (
        request.endpoint.startswith('static') or
        request.endpoint in ['session_time_left']
    ):
        return

    session.permanent = True

    if 'user_id' not in session:
        return

    now = datetime.now()

    # --- Idle Timeout ---
    if 'last_activity' in session:
        try:
            last_activity = datetime.fromisoformat(session['last_activity'])
        except Exception:
            user_id = session.get('user_id')

            log_session(
                user_id=user_id,
                action="ERROR",
                ip=request.remote_addr,
                details="invalid last_activity format"
            )

            session.clear()
            flash("Session error. Please log in again.", "warning")
            return redirect(url_for('login'))

        timeout_limit = app.config['PERMANENT_SESSION_LIFETIME']

        if now - last_activity > timeout_limit:
            user_id = session.get('user_id')

            log_session(
                user_id=user_id,
                action="TIMEOUT_IDLE",
                ip=request.remote_addr,
                details="idle timeout"
            )

            session.clear()
            flash("Session expired due to inactivity.", "warning")
            return redirect(url_for('login'))

    # --- Absolute Timeout ---
    if 'login_time' in session:
        try:
            login_time = datetime.fromisoformat(session['login_time'])
        except Exception:
            user_id = session.get('user_id')

            log_session(
                user_id=user_id,
                action="ERROR",
                ip=request.remote_addr,
                details="invalid login_time format"
            )

            session.clear()
            return redirect(url_for('login'))

        if now - login_time > timedelta(hours=1):
            user_id = session.get('user_id')

            log_session(
                user_id=user_id,
                action="TIMEOUT_ABSOLUTE",
                ip=request.remote_addr,
                details="absolute timeout"
            )

            session.clear()
            flash("Session expired. Please log in again.", "warning")
            return redirect(url_for('login'))

    # ✅ Update activity only for valid user actions
    session['last_activity'] = now.isoformat()

@app.route('/session-time-left')
def session_time_left():
    if 'user_id' not in session or 'last_activity' not in session:
        return jsonify({'remaining': 0, 'logged_in': False})

    last_activity = datetime.fromisoformat(session['last_activity'])
    timeout = app.config['PERMANENT_SESSION_LIFETIME']
    remaining = (last_activity + timeout - datetime.now()).total_seconds()

    return jsonify({
        'remaining': max(0, int(remaining)),
        'logged_in': True
    })

@app.route('/keep-alive')
@login_required
def keep_alive():
    session['last_activity'] = datetime.now().isoformat()

    log_session(
        user_id=session.get('user_id'),
        action="EXTENDED",
        ip=request.remote_addr,
        details="keep-alive ping"
    )

    return jsonify({'status': 'extended'})

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/test-error')
def test_error():
    raise Exception("Test generic error handler")

@app.route('/menu')
def menu():
    try:
        with db_transaction() as (conn, cursor):
            cursor.execute("SELECT * FROM products")
            menu_items = cursor.fetchall()
            return render_template('menu.html', menu_items=menu_items)
    except Exception as e:
        print(f"Error loading menu: {e}")
        flash('Error loading menu items.', 'danger')
        return render_template('menu.html', menu_items=[])

@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("5 per minute", methods=["POST"])
def register():
    if request.method == 'POST':
        first_name = request.form['first_name'].strip()
        last_name = request.form['last_name'].strip()
        email = request.form['email'].strip()
        country_code = request.form['country_code'].strip()
        phone = request.form['phone'].strip()
        raw_password = request.form['password']
        profile_photo = request.files.get('profile_photo')

        # --- Password Strength ---
        if not strong_password(raw_password):
            flash('Password must be at least 8 characters and include upper, lower, and number.', 'danger')
            return render_template('register.html')

        # --- Hashing and Salting ---
        password_hash = generate_password_hash(raw_password, method='pbkdf2:sha256', salt_length=16)

        # --- Email Validation ---
        try:
            valid = validate_email(email)
            email = valid.email
        except EmailNotValidError as e:
            flash(str(e), 'danger')
            return render_template('register.html')

        # --- Phone Validation ---
        full_phone = f"{country_code}{phone}"
        try:
            parsed_phone = phonenumbers.parse(full_phone, None)
            if not phonenumbers.is_valid_number(parsed_phone):
                flash('Invalid phone number for the selected country.', 'danger')
                return render_template('register.html')
        except NumberParseException:
            flash('Invalid phone number.', 'danger')
            return render_template('register.html')

        # --- Profile Photo ---
        filename = None
        if profile_photo and profile_photo.filename:
            if not allowed_file(profile_photo.filename):
                flash('Invalid image type.', 'danger')
                return render_template('register.html')

            profile_photo.seek(0, os.SEEK_END)
            file_size = profile_photo.tell()
            profile_photo.seek(0)

            if file_size > MAX_FILE_SIZE:
                flash('File too large (max 2MB).', 'danger')
                return render_template('register.html')

            file_type = detect_image_type(profile_photo.stream)
            if file_type not in ALLOWED_EXTENSIONS:
                flash('Invalid image file.', 'danger')
                return render_template('register.html')

            filename = f"{uuid.uuid4().hex}.{file_type}"
            save_path = os.path.join(PROFILE_UPLOAD_FOLDER, filename)
            profile_photo.save(save_path)

        # --- Save to database ---
        try:
            with db_transaction() as (conn, cursor):
                cursor.execute("SELECT id FROM users WHERE email=%s", (email,))
                if cursor.fetchone():
                    flash('Email already registered.', 'danger')
                    return render_template('register.html')

                cursor.execute("""
                    INSERT INTO users
                    (first_name, last_name, email, country_code, phone, password_hash, profile_photo)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """, (first_name, last_name, email, country_code, phone, password_hash, filename))

                log_auth("register", email, "SUCCESS", request.remote_addr)

                flash('Registration successful. Please login.', 'success')
                return redirect(url_for('login'))

        except Exception as e:
            print(e)

            log_auth("register", email, "FAILED", request.remote_addr)

            flash('Registration failed.', 'danger')
            return render_template('register.html')

    return render_template('register.html')



@app.route('/add_to_cart', methods=['POST'])
@login_required
def add_to_cart():
    data = request.get_json()
    product_id = data.get('id')
    user_id = session['user_id']

    try:
        with db_transaction() as (conn, cursor):
            # Check if the item is already in the user's cart
            cursor.execute(
                "SELECT * FROM user_carts WHERE user_id = %s AND product_id = %s",
                (user_id, product_id)
            )
            cart_item = cursor.fetchone()

            if cart_item:
                # If it exists, increment the quantity
                cursor.execute(
                    "UPDATE user_carts SET quantity = quantity + 1 WHERE user_id = %s AND product_id = %s",
                    (user_id, product_id)
                )
            else:
                # If not, insert a new record
                cursor.execute(
                    "INSERT INTO user_carts (user_id, product_id, quantity) VALUES (%s, %s, 1)",
                    (user_id, product_id)
                )
            
            # Load the updated cart data
            cart_data = _load_cart_from_db(cursor, user_id)

        log_transaction(user_id, "ADD_TO_CART", f"product_id={product_id}")

        return jsonify({
            'success': True,
            'message': 'Item added to cart successfully!',
            'cart': cart_data,
            'cart_count': sum(item['quantity'] for item in cart_data.values()),
            'cart_total': sum(item['price'] * item['quantity'] for item in cart_data.values())
        })
    except Exception as e:
        print(f"Error adding to cart: {e}")
        return jsonify({'error': 'Failed to add item to cart.'}), 500

@app.route('/cart')
@login_required
def cart():
    user_id = session['user_id']
    try:
        with db_transaction() as (conn, cursor):
            cart_items = _load_cart_from_db(cursor, user_id)
            total = sum(item['price'] * item['quantity'] for item in cart_items.values())
            return render_template('cart.html', cart=cart_items, total=total)
    except Exception as e:
        print(f"Error loading cart: {e}")
        flash('Error loading your cart.', 'danger')
        return render_template('cart.html', cart={}, total=0)

@app.route('/update_cart', methods=['POST'])
@login_required
def update_cart():
    data = request.get_json()
    action = data.get('action')
    product_id = data.get('item_id')
    user_id = session['user_id']

    try:
        with db_transaction() as (conn, cursor):
            if action == 'increase':
                cursor.execute(
                    "UPDATE user_carts SET quantity = quantity + 1 WHERE user_id = %s AND product_id = %s",
                    (user_id, product_id)
                )
            elif action == 'decrease':
                cursor.execute(
                    "UPDATE user_carts SET quantity = GREATEST(1, quantity - 1) WHERE user_id = %s AND product_id = %s",
                    (user_id, product_id)
                )
            elif action == 'remove':
                cursor.execute(
                    "DELETE FROM user_carts WHERE user_id = %s AND product_id = %s",
                    (user_id, product_id)
                )
            
            cart_data = _load_cart_from_db(cursor, user_id)
            
            log_transaction(user_id, "UPDATE_CART", f"{action} product_id={product_id}")

            return jsonify({
                'success': True,
                'cart': cart_data,
                'cart_count': sum(item['quantity'] for item in cart_data.values()),
                'cart_total': sum(item['price'] * item['quantity'] for item in cart_data.values())
            })
    except Exception as e:
        print(f"Error updating cart: {e}")
        return jsonify({'success': False, 'error': 'Failed to update cart.'}), 500

@app.route('/save-cart-session', methods=['POST'])
def save_cart_session():
    session['cart'] = request.get_json().get('cart', [])
    session.modified = True
    return '', 204

# --- Login ---
def _load_cart_from_db(cursor, user_id):
    """Helper function to load cart from DB and store it in the session."""
    cursor.execute("""
        SELECT p.id, p.name, p.price, uc.quantity
        FROM user_carts uc
        JOIN products p ON uc.product_id = p.id
        WHERE uc.user_id = %s
    """, (user_id,))
    
    cart_items = cursor.fetchall()
    
    cart = {
        str(item['id']): {
            'id': item['id'],
            'name': item['name'],
            'price': float(item['price']),
            'quantity': item['quantity']
        } for item in cart_items
    }
    session['cart'] = cart
    session.modified = True
    return cart

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if request.method == 'POST':
        email_input = request.form['email'].strip()
        password = request.form['password']

        # --- Normalize & validate email ---
        try:
            valid = validate_email(email_input)
            email = valid.email
        except EmailNotValidError as e:
            flash(str(e), 'danger')
            return render_template('login.html')

        now = datetime.now()
        attempt = login_attempts.get(email)

        # --- Check login throttling ---
        if attempt:
            if attempt['count'] >= MAX_ATTEMPTS and now - attempt['last_attempt'] < LOCKOUT_TIME:
                flash('Too many login attempts. Try again later.', 'danger')
                return render_template('login.html')
            elif now - attempt['last_attempt'] > LOCKOUT_TIME:
                # Reset after lockout period
                login_attempts[email] = {'count': 0, 'last_attempt': now}

        try:
            with db_transaction() as (conn, cursor):
                cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
                user = cursor.fetchone()

                if user and check_password_hash(user['password_hash'], password):
                    # Reset attempts on successful login
                    login_attempts[email] = {'count': 0, 'last_attempt': now}

                    # 🔐 Prevent session fixation
                    session.clear()

                    # ✅ Set fresh session data
                    session['user_id'] = user['id']
                    session['email'] = user['email']
                    session['name'] = f"{user['first_name']} {user['last_name']}"
                    session['role'] = user['role']

                    session['login_time'] = datetime.now().isoformat()  # optional (for absolute timeout)

                    session.permanent = True

                    _load_cart_from_db(cursor, user['id'])

                    log_auth("login", email, "SUCCESS", request.remote_addr)

                    flash('Logged in successfully.', 'success')
                    return redirect(url_for('home'))

                else:
                    # Increment attempt count
                    if email not in login_attempts:
                        login_attempts[email] = {'count': 1, 'last_attempt': now}
                    else:
                        login_attempts[email]['count'] += 1
                        login_attempts[email]['last_attempt'] = now

                    flash('Invalid email or password.', 'danger')
                    log_auth("login", email, "FAILED", request.remote_addr)

        except Exception as e:
            print(f"Login error: {e}")
            flash('Login failed. Please try again.', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    log_auth("logout", session.get('email'), "SUCCESS", request.remote_addr)
    session.clear()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('login'))

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/order_history')
@login_required
def order_history():
    user_id = session['user_id']
    
    try:
        with db_transaction() as (conn, cursor):
            cursor.callproc('get_user_orders', (user_id,))

            for result in cursor.stored_results():
                orders = result.fetchall()
            return render_template('order_history.html', orders=orders)
            
    except Exception as e:
        print(f"Error loading order history: {e}")
        flash('Error loading order history.', 'danger')
        return render_template('order_history.html', orders=[])

@app.route('/contact')
def contact():
    return render_template('contact.html')

@app.route('/checkout')
@login_required
def checkout():
    user_id = session['user_id']
    try:
        with db_transaction() as (conn, cursor):
            # Fetch currencies
            cursor.execute("SELECT * FROM currencies")
            currencies = cursor.fetchall()
            
            # Fetch cart items from the database to ensure data is current
            cart_items = _load_cart_from_db(cursor, user_id)
            total = sum(item['price'] * item['quantity'] for item in cart_items.values())
            
            return render_template('checkout.html', currencies=currencies, cart=cart_items, total=total)
    except Exception as e:
        print(f"Error loading checkout: {e}")
        flash('Error loading checkout page.', 'danger')
        return redirect(url_for('cart'))

# --- Checkout ---
@app.route('/submit_checkout', methods=['POST'])
@login_required
def submit_checkout():
    name = request.form.get('name')
    address = request.form.get('address')
    payment_method = request.form.get('payment_method')
    cart_json = request.form.get('cart_data')
    
    try:
        cart_dict = json.loads(cart_json) if cart_json else {}
        cart = list(cart_dict.values()) # Convert dict to list of items
    except json.JSONDecodeError:
        flash("Invalid cart data.", "danger")
        return redirect(url_for('checkout'))

    if not name or not address or not payment_method or not cart:
        flash("Please complete the form and add items to your cart.", "danger")
        return redirect(url_for('checkout'))

    user_id = session['user_id']
    total = sum(item['price'] * item['quantity'] for item in cart)

    try:
        with db_transaction() as (conn, cursor):
            # Step 1: Validate stock availability for all items before proceeding
            for item in cart:
                cursor.execute(
                    "SELECT stock_quantity FROM products WHERE id = %s AND is_active = TRUE",
                    (item['id'],)
                )
                product = cursor.fetchone()
                
                if not product:
                    raise Exception(f"Product {item['name']} is no longer available")
                
                if product['stock_quantity'] < item['quantity']:
                    raise Exception(f"Insufficient stock for {item['name']}. Available: {product['stock_quantity']}, Requested: {item['quantity']}")

            # Step 2: Create order and get the new order_id
            cart_json_for_db = json.dumps(cart)
            cursor.callproc('create_order', (user_id, total, cart_json_for_db))
            
            # Fetch the order_id from the result of the stored procedure
            order_id = None
            for result in cursor.stored_results():
                order_id_result = result.fetchone()
                if order_id_result:
                    order_id = order_id_result['order_id']
                    break

            if not order_id:
                raise Exception("Failed to create order or retrieve order ID")

            # Step 3: Record transaction
            cursor.callproc('record_transaction', (order_id, total, payment_method, 'Success'))

            # Step 5: Clear user's cart from the database
            cursor.execute("DELETE FROM user_carts WHERE user_id = %s", (user_id,))

            # Step 6: Clear cart from session
            session.pop('cart', None)
            session.modified = True

            log_transaction(user_id, "CHECKOUT", f"order_id={order_id} total={total}")

            flash("Order placed successfully! Your order number is " + str(order_id), "success")
            return redirect(url_for('order_history'))

    except mysql.connector.Error as db_error:
        print(f"Database error during checkout: {db_error}")
        # Check for the custom error message from the trigger
        if "Stock is lesser than indicated quantity" in str(db_error.msg):
            flash("Some items in your cart are out of stock. Please update your cart.", "danger")
        else:
            flash("Database error occurred while processing your order.", "danger")
        return redirect(url_for('checkout'))
        
    except Exception as e:
        log_transaction(user_id, "CHECKOUT_FAILED", str(e))

        print(f"Checkout error: {e}")
        flash(f"Error placing order: {str(e)}", "danger")
        return redirect(url_for('checkout'))

# -- Admin --
@app.route('/admin')
@admin_required
def admin():
    editing_id = request.args.get('edit', type=int)
    try:
        with db_transaction() as (conn, cursor):
            # Fetch products
            cursor.execute(
                "SELECT id, name, price, stock_quantity, description, image, is_active "
                "FROM products ORDER BY id DESC"
            )
            products = cursor.fetchall()

            # Fetch users
            cursor.execute("""
                SELECT 
                    id,
                    CONCAT(first_name, ' ', last_name) AS name,
                    email,
                    role
                FROM users
            """)
            users = cursor.fetchall()


            return render_template('admin.html', products=products, users=users, editing_id=editing_id)
            
    except Exception as e:
        print(f"Error loading admin panel: {e}")
        flash('Error loading admin panel.', 'danger')
        return render_template('admin.html', products=[], users=[], editing_id=editing_id)

@app.route('/add_product', methods=['POST'])
@admin_required
def add_product():
    name = request.form.get('name')
    price = request.form.get('price')
    stock_qty = request.form.get('stock_quantity', type=int) or 0
    description = request.form.get('description')

    # Handle file upload
    image_file = request.files.get('image')
    filename = None
    if image_file and image_file.filename:
        filename = secure_filename(image_file.filename)
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image_file.save(save_path)

    try:
        with db_transaction() as (conn, cursor):
            cursor.callproc('add_product', (name, description, price, stock_qty, filename))

            log_admin(session['user_id'], "ADD_PRODUCT", name)

            flash('Product added successfully!', 'success')

    except mysql.connector.Error as err:
        print(f"Database error adding product: {err}")
        if "Indicated Stock is lesser than 0" in str(err):
            flash("Product stock must be greater than 0.", 'danger')
        else:
            flash(f"Database error: {err}", 'danger')
    except Exception as e:
        print(f"Error adding product: {e}")
        flash("Error adding product.", 'danger')

    return redirect(url_for('admin'))

@app.route('/edit_product/<int:product_id>', methods=['POST'])
@admin_required
def edit_product(product_id):
    name = request.form.get('name')
    price = request.form.get('price')
    stock_qty = request.form.get('stock_quantity', type=int)
    description = request.form.get('description')
    is_active = 1 if request.form.get('is_active') == 'on' else 0

    # Handle optional file upload
    image_file = request.files.get('image')
    filename = None
    if image_file and image_file.filename:
        filename = secure_filename(image_file.filename)
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image_file.save(save_path)

    try:
        with db_transaction() as (conn, cursor):
            if filename:
                cursor.execute(
                    "UPDATE products SET name=%s, price=%s, stock_quantity=%s, description=%s, image=%s, is_active=%s WHERE id=%s",
                    (name, price, stock_qty, description, filename, is_active, product_id)
                )
            else:
                cursor.execute(
                    "UPDATE products SET name=%s, price=%s, stock_quantity=%s, description=%s, is_active=%s WHERE id=%s",
                    (name, price, stock_qty, description, is_active, product_id)
                )

            log_admin(session['user_id'], "EDIT_PRODUCT", product_id)

            flash('Product updated successfully!', 'success')

    except mysql.connector.Error as err:
        print(f"Database error updating product: {err}")
        if "Stock is lesser than indicated quantity" in str(err):
            flash("Cannot set stock to negative value.", 'danger')
        else:
            flash(f"Database error: {err}", 'danger')
    except Exception as e:
        print(f"Error updating product: {e}")
        flash("Error updating product.", 'danger')

    return redirect(url_for('admin'))

@app.route('/delete_product/<int:product_id>')
@admin_required
def delete_product(product_id):
    try:
        with db_transaction() as (conn, cursor):
            # Check if product exists and has any order history
            cursor.execute("SELECT name FROM products WHERE id = %s", (product_id,))
            product = cursor.fetchone()
            
            if not product:
                flash('Product not found.', 'danger')
                return redirect(url_for('admin'))
            
            cursor.execute("SELECT COUNT(*) as order_count FROM order_items WHERE product_id = %s", (product_id,))
            order_check = cursor.fetchone()
            
            if order_check['order_count'] > 0:
                flash(f'Cannot delete {product["name"]} - it has order history. Consider deactivating instead.', 'warning')
                return redirect(url_for('admin'))
            
            cursor.execute("DELETE FROM products WHERE id = %s", (product_id,))

            log_admin(session['user_id'], "DELETE_PRODUCT", product_id)

            flash(f'Product "{product["name"]}" deleted successfully!', 'success')

    except Exception as e:
        print(f"Error deleting product: {e}")
        flash("Error deleting product.", 'danger')

    return redirect(url_for('admin'))

# --- Staff Routes ---
@app.route('/staff')
@staff_required
def staff():
    try:
        with db_transaction() as (conn, cursor):
            # Fetch orders with associated products
            cursor.execute("""
            SELECT 
                o.id, o.total, o.status, o.created_at, 
                CONCAT(u.first_name, ' ', u.last_name) AS user_name,
                u.email AS user_email,
                p.name AS product_name, 
                oi.quantity, 
                oi.price
            FROM orders o
            JOIN users u ON o.user_id = u.id
            LEFT JOIN order_items oi ON o.id = oi.order_id
            LEFT JOIN products p ON oi.product_id = p.id
            ORDER BY o.created_at DESC, o.id
            """)
            
            order_details = cursor.fetchall()
            
            orders_dict = {}
            for item in order_details:
                order_id = item['id']
                if order_id not in orders_dict:
                    orders_dict[order_id] = {
                        'id': order_id,
                        'total': item['total'],
                        'status': item['status'],
                        'created_at': item['created_at'],
                        'user_name': item['user_name'],
                        'user_email': item['user_email'],
                        'products': []
                    }
                
                if item['product_name']:
                    orders_dict[order_id]['products'].append({
                        'name': item['product_name'],
                        'quantity': item['quantity'],
                        'price': item['price']
                    })

            orders = list(orders_dict.values())

            # Fetch inventory
            cursor.execute("SELECT id, name, price, stock_quantity, description, image, is_active FROM products ORDER BY name")
            products = cursor.fetchall()

            return render_template('staff.html', orders=orders, products=products)
    except Exception as e:
        print(f"Error loading staff page: {e}")
        flash('Error loading staff page.', 'danger')
        return redirect(url_for('home'))

@app.route('/staff/order/<int:order_id>/update', methods=['POST'])
@staff_required
def update_order_status(order_id):
    new_status = request.form.get('status')
    if not new_status:
        flash('No status provided.', 'danger')
        return redirect(url_for('staff'))

    try:
        with db_transaction() as (conn, cursor):
            cursor.execute(
                "UPDATE orders SET status = %s WHERE id = %s",
                (new_status, order_id)
            )

            log_admin(session['user_id'], "UPDATE_ORDER_STATUS", order_id, f"status={new_status}")

            flash(f'Order #{order_id} status updated to {new_status}.', 'success')
    except Exception as e:
        print(f"Error updating order status: {e}")
        flash('Error updating order status.', 'danger')
    
    return redirect(url_for('staff'))

# --- Admin Routes ---
@app.route('/admin/user/<int:user_id>/role', methods=['POST'])
@admin_required
def update_user_role(user_id):
    new_role = request.form.get('role')
    if not new_role in ['customer', 'staff', 'admin']:
        flash('Invalid role specified.', 'danger')
        return redirect(url_for('admin'))
    
    try:
        with db_transaction() as (conn, cursor):
            cursor.callproc('update_user_role', (user_id, new_role))

            log_admin(session['user_id'], "UPDATE_ROLE", user_id, f"new_role={new_role}")

            flash('User role updated successfully.', 'success')
    except Exception as e:
        print(f"Error updating user role: {e}")
        flash('Error updating user role.', 'danger')
        
    return redirect(url_for('admin'))

@app.route('/admin/user/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    if user_id == session.get('user_id'):
        flash("You cannot delete your own account.", 'danger')
        return redirect(url_for('admin'))
        
    try:
        with db_transaction() as (conn, cursor):
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))

            log_admin(session['user_id'], "DELETE_USER", user_id)

            flash('User deleted successfully.', 'success')
    except Exception as e:
        print(f"Error deleting user: {e}")
        flash('Error deleting user.', 'danger')
        
    return redirect(url_for('admin'))

@app.errorhandler(429)
def ratelimit_handler(e):
    flash('Too many requests. Please slow down and try again later.', 'danger')
    return redirect(request.referrer or url_for('home'))

@app.errorhandler(Exception)
def handle_exception(e):
    if app.config['DEBUG']:
        # Detailed error (for development)
        return f"""
        <h1>Internal Server Error</h1>
        <pre>{traceback.format_exc()}</pre>
        """, 500
    else:
        # Generic message (for users)
        app.logger.error(f"Unhandled Exception: {str(e)}")
        return render_template("error.html", message="Something went wrong. Please try again later."), 500
    
if __name__ == '__main__':
    app.run(debug=DEBUG)
