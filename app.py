from flask import Flask, render_template, request, redirect, url_for, session
import stripe
import os
import random
import json

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")

# Stripe config (use your test key in Render env)
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_test_123")
YOUR_DOMAIN = os.getenv("YOUR_DOMAIN", "https://your-render-url.onrender.com")

# Demo plans (logo filenames must be in static/)
plans = [
    {"id": 1, "name": "Netflix Standard", "price": 199, "logo": "netflix.png"},
    {"id": 2, "name": "Amazon Prime Video", "price": 149, "logo": "prime.png"},
    {"id": 3, "name": "Disney+ Hotstar Premium", "price": 299, "logo": "hotstar.png"},
    {"id": 4, "name": "Sony LIV Premium", "price": 129, "logo": "sonyliv.png"},
    {"id": 5, "name": "Zee5 Premium", "price": 99, "logo": "zee5.png"}
]

# simple in-memory "users" & "orders" for demo (not persistent)
USERS = []
ORDERS = []

def get_plan(plan_id):
    for p in plans:
        if p["id"] == plan_id:
            return p
    return None

# ----------------- Routes -----------------
@app.route("/")
def home():
    return render_template("index.html", plans=plans)

@app.route("/plans")
def show_plans():
    return render_template("plans.html", plans=plans)

@app.route("/plan/<int:id>")
def plan_details(id):
    p = get_plan(id)
    if not p:
        return "Plan not found", 404
    return render_template("plan-details.html", plan=p)

@app.route("/create-checkout-session/<int:id>", methods=["GET"])
def create_checkout_session(id):
    plan = get_plan(id)
    if not plan:
        return "Invalid plan", 404
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'inr',
                    'product_data': {'name': plan["name"]},
                    'unit_amount': int(plan["price"] * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"{YOUR_DOMAIN}/success?plan={plan['id']}",
            cancel_url=f"{YOUR_DOMAIN}/plans",
        )
        return redirect(session.url, code=303)
    except Exception as e:
        return str(e)

@app.route("/success")
def success():
    plan_id = request.args.get("plan")
    plan = get_plan(int(plan_id)) if plan_id else None
    # record fake order
    ORDERS.append({"id": len(ORDERS)+1, "plan": plan["name"] if plan else "unknown", "amount": plan["price"] if plan else 0})
    return render_template("success.html", plan=plan)

@app.route("/contact")
def contact():
    return render_template("contact.html")

@app.route("/faq")
def faq():
    return render_template("faq.html")

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/testimonials")
def testimonials():
    sample = [
        {"name":"Ravi","note":"Great demo project!"},
        {"name":"Priya","note":"Clean UI, well built."}
    ]
    return render_template("testimonials.html", testimonials=sample)

@app.route("/comparison")
def comparison():
    return render_template("comparison.html", plans=plans)

# ----------------- Auth (demo only) -----------------
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        password = request.form.get("password")
        USERS.append({"name": name, "email": email, "password": password})
        session['user'] = email
        return redirect(url_for('home'))
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        user = next((u for u in USERS if u["email"]==email and u["password"]==password), None)
        if user:
            session['user'] = email
            return redirect(url_for('home'))
        else:
            return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop('user', None)
    return redirect(url_for('home'))

# ----------------- Admin (demo, read-only) -----------------
@app.route("/admin")
def admin():
    # demo stats for charts
    orders_by_plan = {}
    for o in ORDERS:
        orders_by_plan[o["plan"]] = orders_by_plan.get(o["plan"], 0) + 1
    # fill zeros for missing
    labels = [p["name"] for p in plans]
    data = [orders_by_plan.get(p, 0) for p in labels]
    fake_users = random.randint(20,120)
    revenue = sum(o["amount"] for o in ORDERS)
    stats = {"users": fake_users, "revenue": revenue, "orders": len(ORDERS)}
    return render_template("admin.html", plans=plans, labels=json.dumps(labels), chartdata=json.dumps(data), stats=stats)

# ----------------- Static helpers  -----------------
@app.context_processor
def inject_user():
    return dict(user=session.get('user'))

# ----------------- Run -----------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
