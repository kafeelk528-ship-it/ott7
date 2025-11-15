from flask import Flask, render_template, request, redirect, url_for, session, abort
import sqlite3
import stripe
from datetime import datetime
from functools import wraps
import os

app = Flask(__name__)
app.secret_key = "SUPER_SECRET_KEY"

# Stripe test key (change to your key)
stripe.api_key = "sk_test_1234"

YOUR_DOMAIN = "https://ott7.onrender.com"  # update this

# =====================================================
# DATABASE SETUP
# =====================================================
DB_FILE = "ott.db"

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY,
            name TEXT,
            price INTEGER,
            logo TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS coupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            type TEXT,
            amount INTEGER,
            expires_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            password TEXT
        )
    """)

    # Create default admin
    cur.execute("SELECT COUNT(*) as c FROM admin")
    if cur.fetchone()["c"] == 0:
        cur.execute("INSERT INTO admin (username, password) VALUES ('admin', 'admin123')")

    conn.commit()
    conn.close()

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

init_db()
seed_plans()

# =====================================================
# AUTH HELPERS
# =====================================================
def admin_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrap

# =====================================================
# UTILITIES
# =====================================================
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
        expiry = datetime.fromisoformat(coupon["expires_at"])
        if datetime.utcnow() > expiry:
            return amount, "EXPIRED"

    if coupon["type"] == "flat":
        new_amount = max(0, amount - coupon["amount"])
    else:
        new_amount = max(0, int(amount * (100 - coupon["amount"]) / 100))

    return new_amount, None

# =====================================================
# PUBLIC ROUTES
# =====================================================
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
    plan = get_plan(plan_id)
    if not plan:
        abort(404)
    return render_template("plan-details.html", plan=plan)

@app.route("/contact")
def contact():
    return render_template("contact.html")

# =====================================================
# CART SYSTEM
# =====================================================
def get_cart():
    return session.get("cart", {})

def save_cart(cart):
    session["cart"] = cart
    session["cart_count"] = sum(cart.values())

@app.route("/add-to-cart/<int:plan_id>")
def add_to_cart(plan_id):
    plan = get_plan(plan_id)
    if not plan:
        return "Invalid plan", 404

    cart = get_cart()
    cart[str(plan_id)] = cart.get(str(plan_id), 0) + 1
    save_cart(cart)

    return redirect(url_for("cart"))

@app.route("/cart")
def cart():
    cart = get_cart()
    items = []
    total = 0

    for pid, qty in cart.items():
        plan = get_plan(int(pid))
        if plan:
            price = plan["price"] * qty
            total += price

            items.append({
                "id": plan["id"],
                "name": plan["name"],
                "logo": plan["logo"],
                "price": plan["price"],
                "qty": qty,
                "total": price
            })

    return render_template("cart.html", items=items, total=total)

@app.route("/remove-from-cart/<int:plan_id>")
def remove_from_cart(plan_id):
    cart = get_cart()
    cart.pop(str(plan_id), None)
    save_cart(cart)
    return redirect(url_for("cart"))

@app.route("/clear-cart")
def clear_cart():
    session["cart"] = {}
    session["cart_count"] = 0
    return redirect(url_for("cart"))

# =====================================================
# COUPON SYSTEM
# =====================================================
@app.route("/apply-coupon", methods=["POST"])
def apply_coupon():
    code = request.form.get("coupon")
    session["coupon_code"] = code.upper()
    return redirect(url_for("show_plans"))

# =====================================================
# STRIPE CHECKOUT
# =====================================================
@app.route("/create-checkout-session/<int:plan_id>")
def create_checkout_session(plan_id):
    p = get_plan(plan_id)
    if not p:
        return "Invalid plan", 404

    amount = p["price"]
    coupon = session.get("coupon_code")
    final_amount, error = apply_coupon_to_amount(coupon, amount)

    try:
        checkout = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "inr",
                    "product_data": { "name": p["name"] },
                    "unit_amount": final_amount * 100
                },
                "quantity": 1
            }],
            mode="payment",
            success_url=f"{YOUR_DOMAIN}/success?plan={p['id']}",
            cancel_url=f"{YOUR_DOMAIN}/plan/{p['id']}"
        )
        return redirect(checkout.url, code=303)

    except Exception as e:
        return str(e)

@app.route("/success")
def success():
    return render_template("success.html")

# =====================================================
# ADMIN SYSTEM
# =====================================================
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        user = request.form["username"]
        pw = request.form["password"]

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM admin WHERE username=? AND password=?", (user, pw))
        row = cur.fetchone()
        conn.close()

        if row:
            session["is_admin"] = True
            return redirect(url_for("admin_dashboard"))
        else:
            return render_template("admin-login.html", error="Invalid login")

    return render_template("admin-login.html")


@app.route("/admin")
@admin_required
def admin_dashboard():
    plans = query_plans()
    return render_template("admin.html", plans=plans)


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


# =====================================================
# START APP
# =====================================================
if __name__ == "__main__":
    app.run(debug=True)
