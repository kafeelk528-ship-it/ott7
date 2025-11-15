import os
import json
import stripe
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, send_file, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# -------------------------
# Config
# -------------------------
app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-me")

DB_PATH = os.getenv("SQLITE_DB", "database.db")

# Stripe config (test)
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_123")
stripe.api_key = STRIPE_SECRET_KEY
YOUR_DOMAIN = os.getenv("YOUR_DOMAIN", "https://your-render-url.onrender.com")

# Admin credentials via env (recommended to set on Render)
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@admin.com")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH")  # if set, use hash
ADMIN_PASSWORD_PLAIN = os.getenv("ADMIN_PASSWORD", "adminpass")  # fallback only

# For demo: if ADMIN_PASSWORD_HASH not set, create from plain
if not ADMIN_PASSWORD_HASH:
    ADMIN_PASSWORD_HASH = generate_password_hash(ADMIN_PASSWORD_PLAIN)


# -------------------------
# Database helpers (SQLite)
# -------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    # users
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT UNIQUE,
        password_hash TEXT,
        created_at TEXT
    )""")
    # plans
    cur.execute("""
    CREATE TABLE IF NOT EXISTS plans (
        id INTEGER PRIMARY KEY,
        name TEXT,
        price INTEGER,
        logo TEXT
    )""")
    # orders
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT,
        plan_id INTEGER,
        plan_name TEXT,
        amount INTEGER,
        coupon_code TEXT,
        created_at TEXT
    )""")
    # coupons
    cur.execute("""
    CREATE TABLE IF NOT EXISTS coupons (
        code TEXT PRIMARY KEY,
        type TEXT,        -- 'flat' or 'percent'
        amount INTEGER,   -- if flat => rupees, if percent => percent e.g. 10 for 10%
        expires_at TEXT
    )""")
    # messages
    cur.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT,
        message TEXT,
        created_at TEXT
    )""")
    conn.commit()
    conn.close()

    seed_plans()


def seed_plans():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(1) as cnt FROM plans")
    if cur.fetchone()["cnt"] == 0:
        default = [
            (1, "Netflix Standard", 199, "netflix.png"),
            (2, "Amazon Prime Video", 149, "prime.png"),
            (3, "Disney+ Hotstar Premium", 299, "hotstar.png"),
            (4, "Sony LIV Premium", 129, "sonyliv.png"),
            (5, "Zee5 Premium", 99, "zee5.png"),
        ]
        cur.executemany("INSERT INTO plans (id, name, price, logo) VALUES (?, ?, ?, ?)", default)
        conn.commit()
    conn.close()

# init DB on startup
init_db()


# -------------------------
# Auth helpers
# -------------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login", next=request.path))
        return f(*args, **kwargs)
    return decorated

# -------------------------
# Utility functions
# -------------------------
def query_plans():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM plans ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_plan(plan_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM plans WHERE id=?", (plan_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def apply_coupon_to_amount(code, amount):
    if not code:
        return amount, None
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM coupons WHERE code=?", (code.upper(),))
    row = cur.fetchone()
    conn.close()
    if not row:
        return amount, "INVALID"
    coupon = dict(row)
    if coupon["expires_at"]:
        expires = datetime.fromisoformat(coupon["expires_at"])
        if datetime.utcnow() > expires:
            return amount, "EXPIRED"
    if coupon["type"] == "flat":
        new = max(0, amount - coupon["amount"])
    else:  # percent
        new = max(0, int(amount * (100 - coupon["amount"]) / 100))
    return new, None

# -------------------------
# Routes - Public
# -------------------------
@app.route("/")
def home():
    plans = query_plans()
    return render_template("index.html", plans=plans)

@app.route("/plans")
def show_plans():
    plans = query_plans()
    return render_template("plans.html", plans=plans)

@app.route("/plan/<int:plan_id>")
def plan_details(plan_id):
    p = get_plan(plan_id)
    if not p:
        abort(404)
    return render_template("plan-details.html", plan=p)

# ==========================
# Create checkout session (Stripe)
# Supports optional coupon stored in session['coupon']
# ==========================
@app.route("/create-checkout-session/<int:plan_id>", methods=["GET"])
def create_checkout_session(plan_id):
    p = get_plan(plan_id)
    if not p:
        return "Invalid plan", 404

    # amount in rupees -> convert to paisa
    amount = int(p["price"])
    coupon = session.get("coupon_code")
    final_amount, coupon_error = apply_coupon_to_amount(coupon, amount)
    if coupon_error:
        # remove invalid/expired coupon from session
        session.pop("coupon_code", None)
        final_amount = amount

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "inr",
                    "product_data": {"name": p["name"]},
                    "unit_amount": final_amount * 100,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=f"{YOUR_DOMAIN}/success?plan={p['id']}",
            cancel_url=f"{YOUR_DOMAIN}/plan/{p['id']}",
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        return str(e)

# success (Stripe redirects here after payment in test mode)
@app.route("/success")
def success():
    plan_id = request.args.get("plan")
    plan = get_plan(int(plan_id)) if plan_id else None
    # Save order to DB (demo) - in real production use webhooks
    conn = get_db()
    cur = conn.cursor()
    user_email = session.get("user")
    coupon = session.get("coupon_code")
    created = datetime.utcnow().isoformat()
    if plan:
        cur.execute(
            "INSERT INTO orders (user_email, plan_id, plan_name, amount, coupon_code, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_email, plan["id"], plan["name"], plan["price"], coupon, created)
        )
        conn.commit()
        order_id = cur.lastrowid
    else:
        order_id = None
    conn.close()

    # Clear coupon after use
    session.pop("coupon_code", None)
    return render_template("success.html", plan=plan, order_id=order_id)


# Contact form
@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        message = request.form.get("message")
        created = datetime.utcnow().isoformat()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO messages (name, email, message, created_at) VALUES (?, ?, ?, ?)",
                    (name, email, message, created))
        conn.commit()
        conn.close()
        flash("Message submitted. Thank you!", "success")
        return redirect(url_for("contact"))
    return render_template("contact.html")


# Coupons (apply)
@app.route("/apply-coupon", methods=["POST"])
def apply_coupon():
    code = request.form.get("coupon", "").strip().upper()
    if not code:
        flash("Please enter a coupon code", "warning")
        return redirect(request.referrer or url_for("plans_page"))
    # test coupon application
    # use session to store coupon
    session["coupon_code"] = code
    flash(f"Coupon {code} applied (demo). It will be validated at checkout.", "success")
    return redirect(request.referrer or url_for("plans_page"))

# -------------------------
# Auth - Users (no OTP) - simple
# -------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email").lower().strip()
        password = request.form.get("password")
        if not email or not password:
            flash("Email and password required", "warning")
            return redirect(url_for("register"))
        pw_hash = generate_password_hash(password)
        created = datetime.utcnow().isoformat()
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                        (name, email, pw_hash, created))
            conn.commit()
            session["user"] = email
            flash("Registered and logged in", "success")
            return redirect(url_for("home"))
        except sqlite3.IntegrityError:
            flash("Email already registered", "warning")
            return redirect(url_for("register"))
        finally:
            conn.close()
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email").lower().strip()
        password = request.form.get("password")
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE email=?", (email,))
        row = cur.fetchone()
        conn.close()
        if row and check_password_hash(row["password_hash"], password):
            session["user"] = email
            flash("Logged in", "success")
            return redirect(url_for("home"))
        flash("Invalid credentials", "danger")
        return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("Logged out", "info")
    return redirect(url_for("home"))

# -------------------------
# Admin login (separate)
# -------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        if email.lower() == ADMIN_EMAIL.lower() and check_password_hash(ADMIN_PASSWORD_HASH, password):
            session["is_admin"] = True
            session["admin_email"] = email
            flash("Admin login successful", "success")
            return redirect(url_for("admin_dashboard"))
        flash("Invalid admin credentials", "danger")
        return redirect(url_for("admin_login"))
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    session.pop("admin_email", None)
    flash("Admin logged out", "info")
    return redirect(url_for("home"))

# -------------------------
# Admin dashboard & management
# -------------------------
@app.route("/admin")
@admin_required
def admin_dashboard():
    conn = get_db()
    cur = conn.cursor()
    # stats
    cur.execute("SELECT COUNT(*) as users FROM users")
    users = cur.fetchone()["users"]
    cur.execute("SELECT COUNT(*) as orders FROM orders")
    orders = cur.fetchone()["orders"]
    cur.execute("SELECT SUM(amount) as revenue FROM orders")
    revenue = cur.fetchone()["revenue"] or 0
    # orders by plan
    cur.execute("SELECT plan_name, COUNT(*) as cnt FROM orders GROUP BY plan_name")
    rows = cur.fetchall()
    labels = [r["plan_name"] for r in rows]
    data = [r["cnt"] for r in rows]
    # fetch messages
    cur.execute("SELECT * FROM messages ORDER BY created_at DESC LIMIT 20")
    messages = [dict(r) for r in cur.fetchall()]
    # fetch coupons
    cur.execute("SELECT * FROM coupons ORDER BY code")
    coupons = [dict(r) for r in cur.fetchall()]
    conn.close()
    return render_template("admin.html", stats={"users": users, "orders": orders, "revenue": revenue},
                           labels=json.dumps(labels), chartdata=json.dumps(data),
                           messages=messages, coupons=coupons, plans=query_plans())

# Admin: create coupon
@app.route("/admin/coupons/create", methods=["POST"])
@admin_required
def admin_create_coupon():
    code = request.form.get("code", "").upper().strip()
    ctype = request.form.get("type", "flat")
    amount = int(request.form.get("amount", "0"))
    expires = request.form.get("expires")  # optional ISO date
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO coupons (code, type, amount, expires_at) VALUES (?, ?, ?, ?)",
                    (code, ctype, amount, expires or None))
        conn.commit()
        flash("Coupon created", "success")
    except sqlite3.IntegrityError:
        flash("Coupon code already exists", "warning")
    finally:
        conn.close()
    return redirect(url_for("admin_dashboard"))

# Admin: delete coupon
@app.route("/admin/coupons/delete/<code>", methods=["POST"])
@admin_required
def admin_delete_coupon(code):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM coupons WHERE code=?", (code.upper(),))
    conn.commit()
    conn.close()
    flash("Coupon removed", "success")
    return redirect(url_for("admin_dashboard"))


# -------------------------
# Invoice PDF generation (reportlab)
# -------------------------
def generate_invoice_pdf(order_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id=?", (order_id,))
    order = cur.fetchone()
    conn.close()
    if not order:
        return None
    order = dict(order)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 20)
    c.drawString(40, height - 80, "OTT Store - Invoice")
    c.setFont("Helvetica", 12)
    c.drawString(40, height - 110, f"Invoice ID: {order['id']}")
    c.drawString(40, height - 130, f"Date: {order['created_at']}")
    c.drawString(40, height - 150, f"Customer Email: {order['user_email'] or 'Guest'}")

    c.drawString(40, height - 190, "Plan")
    c.drawString(300, height - 190, "Amount (₹)")

    c.line(40, height - 195, width - 40, height - 195)

    c.drawString(40, height - 220, order["plan_name"])
    c.drawString(300, height - 220, str(order["amount"]))

    c.line(40, height - 260, width - 40, height - 260)
    c.drawString(40, height - 290, "Total")
    c.drawString(300, height - 290, str(order["amount"]))

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

@app.route("/invoice/<int:order_id>")
@login_required
def invoice(order_id):
    buf = generate_invoice_pdf(order_id)
    if not buf:
        abort(404)
    return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name=f"invoice_{order_id}.pdf")


# -------------------------
# Simple profile and orders page
# -------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    user_email = session.get("user")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE user_email=? ORDER BY created_at DESC", (user_email,))
    orders = [dict(r) for r in cur.fetchall()]
    conn.close()
    return render_template("dashboard.html", orders=orders)


# -------------------------
# Context processor to inject user/admin info
# -------------------------
@app.context_processor
def inject_user():
    return dict(user=session.get("user"), is_admin=session.get("is_admin"))


# -------------------------
# Run
# -------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)

