# Milestone 2 Requirements Analysis & Q&A

## Current Status Assessment
Your application already has a solid foundation from Milestone 1 with most security features implemented. Below is the analysis of each requirement:

## 1. SQL-based Application
**Status: ✅ COMPLETED**
- **Current Implementation**: MySQL database with comprehensive schema
- **Tables**: users, products, orders, order_items, transactions, currencies, user_carts
- **Stored Procedures**: get_user_orders, add_product, create_order, record_transaction, update_user_role
- **Triggers**: product_update, deleted_products_archive, prevent_negative_stock, prevent_negative_new_product, low_stock_alerts, return_stock_on_cancellation
- **Role-based Access Control**: MySQL roles (customer_role, staff_role, admin_role)

## 2. Feature to Save and Display Text Input with At Least 2 Numeric Inputs
**Status: ❌ MISSING**
- **Required**: A feature where users can input text and at least 2 numeric values
- **Proposed Solution**: Add a "Product Review/Rating" system where users can:
  - Input review text (text input)
  - Input rating (numeric 1-5)
  - Input price_paid (numeric)
- **Alternative**: Add "Custom Donut Order" with text description and numeric quantity/price fields

## 3. Regular Users - At Least 3 Different Actions
**Status: ✅ COMPLETED**
- **Current User Actions**:
  1. **Browse Products**: View menu items with prices and descriptions
  2. **Shopping Cart Management**: Add to cart, update quantities, remove items
  3. **Checkout Process**: Complete orders with payment methods
  4. **Order History**: View past orders and transactions
  5. **Profile Management**: Update personal information (partially implemented)
- **Bonus Actions Already Available**: Registration, Login, Logout, Contact form

## 4. Admin Users - At Least 3 Different Admin-Only Actions
**Status: ✅ COMPLETED**
- **Current Admin Actions**:
  1. **Product Management**: Add, edit, delete products with image upload
  2. **User Management**: Update user roles, delete users
  3. **Order Oversight**: View all orders through staff interface
  4. **System Monitoring**: Access to product updates, phased out products, low stock alerts
  5. **Database Administration**: Full CRUD operations on all tables

## 5. Logging Features for Authentication, Transactions, and Administrative Actions
**Status: ✅ COMPLETED**
- **Current Logging Implementation**:
  - **Authentication**: log_auth() for login, logout, register attempts
  - **Transactions**: log_transaction() for cart operations, checkout
  - **Administrative**: log_admin() for product/user/order management
  - **Security**: log_security() for invalid file uploads, rate limiting
  - **Session**: log_session() for timeouts, extensions
  - **System**: log_system() for startup, database connections
- **Log File**: RotatingFileHandler with 5MB max, 5 backup files
- **Location**: logs/app.log

## 6. Implementation of Session Timeout
**Status: ✅ COMPLETED**
- **Current Implementation**:
  - **Idle Timeout**: 1 minute (PERMANENT_SESSION_LIFETIME)
  - **Absolute Timeout**: 1 hour maximum session duration
  - **Session Fixation Prevention**: session.clear() on login
  - **Activity Tracking**: last_activity timestamp updates
  - **Keep-Alive**: /keep-alive endpoint to extend sessions
  - **Session Monitoring**: /session-time-left endpoint for remaining time

## 7. Error Messaging (Detailed with Stack Trace when Debug Enabled)
**Status: ✅ COMPLETED**
- **Current Implementation**:
  - **Debug Mode**: Full stack trace displayed (app.config['DEBUG'])
  - **Production Mode**: Generic error messages via error.html template
  - **Error Handlers**: 404, 429 (rate limiting), 500 (general exceptions)
  - **Database Errors**: Specific error messages for stock issues, constraints
  - **Form Validation**: User-friendly validation messages

## 8. HTTPS Implementation (Self-signed Certificate Acceptable)
**Status: ✅ COMPLETED**
- **Current Implementation**:
  - **SSL Context**: cert.pem and key.pem files
  - **Configuration**: app.run(ssl_context=('cert.pem', 'key.pem'))
  - **Security Headers**: SESSION_COOKIE_SECURE, HTTPONLY, SAMESITE
  - **Files Present**: cert.pem, key.pem in project root

---

## Technical Deep Dive - Security Questions & Answers

### Password Hashing and Salting

**Q: What is hashing, salting, and how does it work?**
**A**: 
- **Hashing**: One-way cryptographic function that converts passwords into fixed-length strings
- **Salting**: Adding random data to passwords before hashing to prevent rainbow table attacks
- **How it works**: 
  1. User creates password → "password123"
  2. System generates random salt → "fA4Cvb0oh6AZAVBK"
  3. Salt + password combined → "fA4Cvb0oh6AZAVBKpassword123"
  4. Hash function applied → "pbkdf2:sha256:1000000$fA4Cvb0oh6AZAVBK$50b7ff83935f06eabd4b70f07da3ff5c4912cfbd455435419036194d8e8cfb95"

**Q: What's the recommended amount of cycles to hash the password?**
**A**: 
- **PBKDF2**: 100,000+ iterations (current implementation uses 1,000,000)
- **Argon2**: 2-3 passes with adequate memory
- **bcrypt**: cost factor of 12 or higher
- **Current Implementation**: PBKDF2-SHA256 with 1,000,000 iterations (excellent security)

### Input Validation and File Type Detection

**Q: What is the image metadata specifically called to determine if it's a PNG and not just a .png extension?**
**A**: 
- **Magic Numbers/File Signatures**: The first few bytes of a file that identify its type
- **PNG Magic Number**: `\x89PNG\r\n\x1a\n` (first 8 bytes)
- **JPEG Magic Number**: `\xff\xd8\xff` (first 3 bytes)
- **GIF Magic Number**: `GIF87a` or `GIF89a` (first 6 bytes)
- **Current Implementation**: detect_image_type() function reads file headers to verify actual file type

**Q: What other input validation techniques are used?**
**A**:
- **Email Validation**: email_validator library with RFC 5322 compliance
- **Phone Validation**: phonenumbers library with country-specific validation
- **Password Strength**: Regex requiring 8+ chars, uppercase, lowercase, numbers
- **File Upload**: Extension validation + magic number verification + size limits
- **SQL Injection Prevention**: Parameterized queries throughout

---

## Missing Features to Implement

### 1. Text Input Feature with Numeric Values
**Priority**: High
**Implementation Plan**:
- Create product review system with text review, rating (1-5), and price validation
- Add database table: reviews (id, user_id, product_id, rating, review_text, price_paid, created_at)
- Implement CRUD operations for reviews
- Display reviews on product pages

### 2. Bonus Features (Optional)
**Internet Accessibility**:
- Configure port forwarding on router
- Use dynamic DNS service (no-ip.com, duckdns.org)
- Update firewall rules to allow port 5000

**Syslog Integration**:
- Install and configure rsyslog
- Modify logging handlers to send to remote syslog server
- Implement TLS encryption for syslog transmission

**Proper SSL Certificate**:
- Register domain name
- Configure DNS records
- Obtain Let's Encrypt certificate using certbot
- Set up automatic certificate renewal

---

## Security Features from Milestone 1 - Current Status

### a) Anti Brute-Force Protection ✅
- **Implementation**: login_attempts dictionary with MAX_ATTEMPTS=5, LOCKOUT_TIME=5 minutes
- **Rate Limiting**: Flask-Limiter with IP-based limits
- **Account Lockout**: Temporary lockout after failed attempts

### b) Password Hashing with Salting ✅
- **Method**: PBKDF2-SHA256 with 1,000,000 iterations
- **Salt Length**: 16 bytes
- **Library**: Werkzeug security functions

### c) Input Validation ✅
- **Email**: email_validator library
- **Phone**: phonenumbers library
- **Images**: Magic number detection
- **Passwords**: Strength requirements

### d) File Upload Type Detection ✅
- **Magic Numbers**: Header-based file type detection
- **Extension Validation**: ALLOWED_EXTENSIONS set
- **Size Limits**: 2MB maximum file size
- **Secure Storage**: UUID-based filenames

---

## Implementation Priority

1. **Immediate**: Add text input feature with numeric values (Product Reviews)
2. **Optional**: Internet accessibility configuration
3. **Optional**: Syslog integration for centralized logging
4. **Optional**: Proper SSL certificate setup

**Current Security Level**: Excellent - All major security features implemented and properly configured.
