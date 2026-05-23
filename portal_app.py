import os, json, requests
from flask import Flask, render_template, request, redirect, flash, jsonify, session, Response
from datetime import datetime, date
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, template_folder='templates/portal')
app.secret_key = os.getenv('PORTAL_SECRET_KEY', os.getenv('SECRET_KEY', 'portal-cxe-2026'))

# ── Shared DB (same PostgreSQL as Optic) ──────────────────────────────
DATABASE_URL = os.getenv('DATABASE_URL', '')
USE_PG = bool(DATABASE_URL and 'postgresql' in DATABASE_URL)

WHEELSIZE_KEY = os.getenv('WHEELSIZE_API_KEY', 'ec8f53e4a758566f89605631bb5a5fe3')
WHEELSIZE_BASE = 'https://api.wheel-size.com/v2'

if USE_PG:
    import psycopg2
    def get_db():
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
    PH = '%s'
else:
    import sqlite3
    DB_PATH = os.path.join(os.path.dirname(__file__), 'optic.db')
    def get_db():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    PH = '?'

def _row(cursor, row):
    if row is None: return None
    if hasattr(row, 'keys'): return dict(row)
    cols = [d[0] for d in cursor.description] if cursor.description else []
    return dict(zip(cols, row))

def _cast(v):
    import decimal
    if isinstance(v, decimal.Decimal): return float(v)
    return v

def fetchall(conn, sql, params=None):
    c = conn.cursor(); c.execute(sql, params or [])
    return [{k: _cast(v) for k,v in _row(c,r).items()} for r in c.fetchall()]

def fetchone(conn, sql, params=None):
    c = conn.cursor(); c.execute(sql, params or [])
    r = c.fetchone()
    if not r: return None
    return {k: _cast(v) for k,v in _row(c,r).items()}

def scalar(conn, sql, params=None):
    import decimal
    try:
        c = conn.cursor(); c.execute(sql, params or [])
        r = c.fetchone()
        if not r: return 0
        v = r[0]
        if v is None: return 0
        if isinstance(v, decimal.Decimal): return float(v)
        return v
    except: return 0

def execute(conn, sql, params=None):
    c = conn.cursor(); c.execute(sql, params or [])

def next_ref():
    import random, string
    return 'DP-' + ''.join(random.choices(string.digits, k=6))

# ── Auth ──────────────────────────────────────────────────────────────
def dealer_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('dealer_id'):
            return redirect('/portal/login')
        return f(*args, **kwargs)
    return decorated

def current_dealer():
    return {
        'id': session.get('dealer_id'),
        'name': session.get('dealer_name'),
        'email': session.get('dealer_email'),
        'business': session.get('dealer_business'),
    }

# ── WheelSize API helpers ─────────────────────────────────────────────
def ws_get(endpoint, params=None):
    p = params or {}
    p['user_key'] = WHEELSIZE_KEY
    try:
        r = requests.get(f"{WHEELSIZE_BASE}{endpoint}", params=p, timeout=8)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict) and 'data' in data:
                return data['data']
            return data
        return []
    except Exception as e:
        print(f"WheelSize API error: {e}")
        return []

# ── Routes ────────────────────────────────────────────────────────────

@app.route('/portal/')
@app.route('/portal')
def portal_home():
    if session.get('dealer_id'):
        return redirect('/portal/shop')
    return redirect('/portal/login')

# ── LOGIN ─────────────────────────────────────────────────────────────
@app.route('/portal/login', methods=['GET','POST'])
def portal_login():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','')
        conn = get_db()
        dealer = fetchone(conn, f"SELECT * FROM dealers WHERE LOWER(email)={PH}", (email,))
        conn.close()
        if not dealer:
            flash('No account found with that email')
            return render_template('login.html')
        if dealer['status'] == 'Pending':
            flash('Your application is pending approval. You will receive an email when approved.')
            return render_template('login.html')
        if dealer['status'] == 'Rejected':
            flash('Your dealer application was not approved. Contact CXE Global for more information.')
            return render_template('login.html')
        if dealer['status'] == 'Suspended':
            flash('Your account has been suspended. Contact CXE Global.')
            return render_template('login.html')
        if not dealer.get('password_hash') or not check_password_hash(dealer['password_hash'], password):
            flash('Incorrect password')
            return render_template('login.html')
        session['dealer_id'] = dealer['id']
        session['dealer_name'] = dealer['contact_name']
        session['dealer_email'] = dealer['email']
        session['dealer_business'] = dealer['business_name']
        return redirect('/portal/shop')
    return render_template('login.html')

@app.route('/portal/logout')
def portal_logout():
    session.clear()
    return redirect('/portal/login')

# ── REGISTER ──────────────────────────────────────────────────────────
@app.route('/portal/register', methods=['GET','POST'])
def portal_register():
    if request.method == 'POST':
        d = request.form
        email = d.get('email','').strip().lower()
        conn = get_db()
        existing = fetchone(conn, f"SELECT id FROM dealers WHERE LOWER(email)={PH}", (email,))
        if existing:
            conn.close()
            flash('An account with this email already exists')
            return render_template('register.html')
        pw_hash = generate_password_hash(d.get('password',''))
        execute(conn, f"""INSERT INTO dealers
            (business_name,contact_name,email,phone,business_type,monthly_volume,password_hash,status)
            VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH},'Pending')""",
            (d.get('business_name',''), d.get('contact_name',''), email,
             d.get('phone',''), d.get('business_type',''), d.get('monthly_volume',''), pw_hash))
        conn.commit(); conn.close()
        flash('Application submitted! CXE Global will review and email you within 1 business day.')
        return redirect('/portal/login')
    return render_template('register.html')

# ── SHOP ──────────────────────────────────────────────────────────────
@app.route('/portal/shop')
@dealer_login_required
def portal_shop():
    conn = get_db()
    # Filters
    brand_f   = request.args.get('brand','')
    dia_f     = request.args.get('diameter','')
    width_f   = request.args.get('width','')
    bolt_f    = request.args.get('bolt_pattern','')
    finish_f  = request.args.get('finish','')
    offset_f  = request.args.get('offset','')
    instock_f = request.args.get('instock','')
    pmin_f    = request.args.get('pmin','')
    pmax_f    = request.args.get('pmax','')
    q         = request.args.get('q','').strip()
    sort      = request.args.get('sort','name')

    # YMM from session or params
    ymm_year  = request.args.get('year', session.get('ymm_year',''))
    ymm_make  = request.args.get('make', session.get('ymm_make',''))
    ymm_model = request.args.get('model', session.get('ymm_model',''))
    if ymm_year: session['ymm_year'] = ymm_year
    if ymm_make: session['ymm_make'] = ymm_make
    if ymm_model: session['ymm_model'] = ymm_model

    # If YMM selected — get fitment specs from WheelSize API and filter
    ymm_fitment = None
    ymm_bolt = ''
    if ymm_year and ymm_make and ymm_model:
        fitment_data = ws_get('/search/by_model/', {
            'make': ymm_make, 'model': ymm_model, 'year': ymm_year, 'region': 'us'
        })
        if fitment_data:
            # Extract bolt patterns from fitment
            bolts = set()
            for item in fitment_data:
                if isinstance(item, dict):
                    for stud in [item.get('bolt_pattern',''), item.get('stud_holes','')]:
                        if stud: bolts.add(str(stud))
                    # Also try nested
                    for fp in item.get('front', [{}]):
                        if isinstance(fp, dict) and fp.get('bolt_pattern'):
                            bolts.add(str(fp['bolt_pattern']))
            ymm_bolt = ','.join(bolts)
            ymm_fitment = fitment_data[0] if fitment_data else None
            if ymm_bolt and not bolt_f:
                bolt_f = list(bolts)[0] if bolts else ''

    sql = "SELECT * FROM wheel_inventory WHERE show_in_portal=1"
    params = []
    if q:
        if USE_PG:
            sql += f" AND (name ILIKE {PH} OR brand ILIKE {PH} OR sku ILIKE {PH} OR bolt_pattern ILIKE {PH})"
        else:
            sql += f" AND (LOWER(name) LIKE LOWER({PH}) OR LOWER(brand) LIKE LOWER({PH}) OR LOWER(sku) LIKE LOWER({PH}) OR LOWER(bolt_pattern) LIKE LOWER({PH}))"
        params += [f'%{q}%']*4
    if brand_f:
        sql += f" AND brand={PH}"; params.append(brand_f)
    if dia_f:
        sql += f" AND diameter={PH}"; params.append(dia_f)
    if width_f:
        sql += f" AND width={PH}"; params.append(width_f)
    if bolt_f:
        sql += f" AND bolt_pattern={PH}"; params.append(bolt_f)
    if finish_f:
        if USE_PG:
            sql += f" AND finish ILIKE {PH}"; params.append(f'%{finish_f}%')
        else:
            sql += f" AND LOWER(finish) LIKE LOWER({PH})"; params.append(f'%{finish_f}%')
    if offset_f:
        sql += f" AND wheel_offset={PH}"; params.append(offset_f)
    if instock_f:
        sql += " AND stock_qty > 0"
    if pmin_f:
        try: sql += f" AND dealer_price>={PH}"; params.append(float(pmin_f))
        except: pass
    if pmax_f:
        try: sql += f" AND dealer_price<={PH}"; params.append(float(pmax_f))
        except: pass

    if sort == 'price-asc': sql += " ORDER BY dealer_price ASC"
    elif sort == 'price-desc': sql += " ORDER BY dealer_price DESC"
    elif sort == 'stock': sql += " ORDER BY stock_qty DESC"
    else: sql += " ORDER BY brand, name"

    products = fetchall(conn, sql, params or None)

    # Filter dropdowns
    brands   = [r['brand'] for r in fetchall(conn,"SELECT DISTINCT brand FROM wheel_inventory WHERE show_in_portal=1 AND brand IS NOT NULL ORDER BY brand") if r.get('brand')]
    dias     = [r['diameter'] for r in fetchall(conn,"SELECT DISTINCT diameter FROM wheel_inventory WHERE show_in_portal=1 AND diameter IS NOT NULL ORDER BY diameter") if r.get('diameter')]
    widths   = [r['width'] for r in fetchall(conn,"SELECT DISTINCT width FROM wheel_inventory WHERE show_in_portal=1 AND width IS NOT NULL ORDER BY width") if r.get('width')]
    bolts    = [r['bolt_pattern'] for r in fetchall(conn,"SELECT DISTINCT bolt_pattern FROM wheel_inventory WHERE show_in_portal=1 AND bolt_pattern IS NOT NULL ORDER BY bolt_pattern") if r.get('bolt_pattern')]
    finishes = [r['finish'] for r in fetchall(conn,"SELECT DISTINCT finish FROM wheel_inventory WHERE show_in_portal=1 AND finish IS NOT NULL ORDER BY finish") if r.get('finish')]
    offsets  = [r['wheel_offset'] for r in fetchall(conn,"SELECT DISTINCT wheel_offset FROM wheel_inventory WHERE show_in_portal=1 AND wheel_offset IS NOT NULL ORDER BY wheel_offset") if r.get('wheel_offset')]
    conn.close()

    cart = session.get('cart', {})
    cart_count = sum(v.get('qty',0) for v in cart.values())

    return render_template('shop.html',
        products=products, brands=brands, dias=dias, widths=widths,
        bolts=bolts, finishes=finishes, offsets=offsets,
        brand_f=brand_f, dia_f=dia_f, width_f=width_f, bolt_f=bolt_f,
        finish_f=finish_f, offset_f=offset_f, instock_f=instock_f,
        pmin_f=pmin_f, pmax_f=pmax_f, q=q, sort=sort,
        ymm_year=ymm_year, ymm_make=ymm_make, ymm_model=ymm_model,
        ymm_fitment=ymm_fitment, ymm_bolt=ymm_bolt,
        cart_count=cart_count, dealer=current_dealer())

@app.route('/portal/shop/clear-ymm')
@dealer_login_required
def portal_clear_ymm():
    session.pop('ymm_year', None)
    session.pop('ymm_make', None)
    session.pop('ymm_model', None)
    return redirect('/portal/shop')

# ── WHEELSIZE API PROXY (called by JS) ───────────────────────────────
@app.route('/portal/api/makes')
@dealer_login_required
def portal_api_makes():
    data = ws_get('/makes/', {'region': 'us'})
    if isinstance(data, list):
        makes = sorted(set(
            item.get('name','') or item.get('slug','') or str(item)
            for item in data if item
        ))
    else:
        makes = []
    return jsonify(makes)

@app.route('/portal/api/models')
@dealer_login_required
def portal_api_models():
    make = request.args.get('make','')
    if not make: return jsonify([])
    data = ws_get('/models/', {'make': make, 'region': 'us'})
    if isinstance(data, list):
        models = sorted(set(
            item.get('name','') or item.get('slug','') or str(item)
            for item in data if item
        ))
    else:
        models = []
    return jsonify(models)

@app.route('/portal/api/years')
@dealer_login_required
def portal_api_years():
    make  = request.args.get('make','')
    model = request.args.get('model','')
    if not make or not model: return jsonify([])
    data = ws_get('/years/', {'make': make, 'model': model, 'region': 'us'})
    if isinstance(data, list):
        years = sorted(set(
            str(item.get('year','') or item.get('slug','') or item)
            for item in data if item
        ), reverse=True)
    else:
        years = []
    return jsonify(years)

@app.route('/portal/api/fitment')
@dealer_login_required
def portal_api_fitment():
    make  = request.args.get('make','')
    model = request.args.get('model','')
    year  = request.args.get('year','')
    if not all([make, model, year]): return jsonify({})
    data = ws_get('/search/by_model/', {'make': make, 'model': model, 'year': year, 'region': 'us'})
    bolts = set()
    hubs  = set()
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict): continue
            if item.get('bolt_pattern'): bolts.add(str(item['bolt_pattern']))
            if item.get('hub_bore'): hubs.add(str(item['hub_bore']))
            for fp in item.get('front', [{}]):
                if isinstance(fp, dict):
                    if fp.get('bolt_pattern'): bolts.add(str(fp['bolt_pattern']))
                    if fp.get('hub_bore'): hubs.add(str(fp['hub_bore']))
    return jsonify({'bolt_patterns': list(bolts), 'hub_bores': list(hubs), 'raw': data[:3] if data else []})

# ── CART ──────────────────────────────────────────────────────────────
@app.route('/portal/cart/add', methods=['POST'])
@dealer_login_required
def portal_cart_add():
    pid = str(request.form.get('product_id',''))
    qty = int(request.form.get('qty', 1))
    conn = get_db()
    p = fetchone(conn, f"SELECT * FROM wheel_inventory WHERE id={PH} AND show_in_portal=1", (pid,))
    conn.close()
    if not p: flash('Product not found'); return redirect('/portal/shop')
    cart = session.get('cart', {})
    if pid in cart:
        cart[pid]['qty'] = min(cart[pid]['qty'] + qty, p['stock_qty'])
    else:
        cart[pid] = {
            'id': p['id'], 'name': p['name'], 'brand': p['brand'],
            'sku': p['sku'], 'price': p['dealer_price'],
            'qty': min(qty, p['stock_qty']),
            'diameter': p.get('diameter',''), 'finish': p.get('finish',''),
            'bolt_pattern': p.get('bolt_pattern',''), 'width': p.get('width',''),
            'wheel_offset': p.get('wheel_offset',''),
        }
    session['cart'] = cart
    flash(f"{p['name']} added to cart")
    return redirect(request.referrer or '/portal/shop')

@app.route('/portal/cart/remove/<pid>', methods=['POST'])
@dealer_login_required
def portal_cart_remove(pid):
    cart = session.get('cart', {})
    cart.pop(str(pid), None)
    session['cart'] = cart
    return redirect('/portal/cart')

@app.route('/portal/cart/update', methods=['POST'])
@dealer_login_required
def portal_cart_update():
    cart = session.get('cart', {})
    for pid, item in cart.items():
        new_qty = request.form.get(f'qty_{pid}')
        if new_qty:
            try:
                cart[pid]['qty'] = max(1, int(new_qty))
            except: pass
    session['cart'] = cart
    return redirect('/portal/cart')

@app.route('/portal/cart')
@dealer_login_required
def portal_cart():
    cart = session.get('cart', {})
    items = list(cart.values())
    subtotal = sum(i['price'] * i['qty'] for i in items)
    tax = round(subtotal * 0.08, 2)
    total = subtotal + tax
    return render_template('cart.html',
        items=items, subtotal=subtotal, tax=tax, total=total,
        cart_count=len(items), dealer=current_dealer())

# ── CHECKOUT ──────────────────────────────────────────────────────────
@app.route('/portal/checkout', methods=['POST'])
@dealer_login_required
def portal_checkout():
    cart = session.get('cart', {})
    if not cart: flash('Your cart is empty'); return redirect('/portal/shop')
    items = list(cart.values())
    subtotal = sum(i['price'] * i['qty'] for i in items)
    tax = round(subtotal * 0.08, 2)
    total = subtotal + tax
    po_number = request.form.get('po_number','')
    notes = request.form.get('notes','')
    dealer = current_dealer()
    order_ref = next_ref()
    items_json = json.dumps(items)
    conn = get_db()
    execute(conn, f"""INSERT INTO dealer_orders
        (order_ref,dealer_id,dealer_name,items_json,subtotal,tax,total,po_number,notes,status)
        VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},'New')""",
        (order_ref, dealer['id'], dealer['business'],
         items_json, subtotal, tax, total, po_number, notes))
    # Deduct stock
    for item in items:
        execute(conn, f"UPDATE wheel_inventory SET stock_qty=GREATEST(0,stock_qty-{PH}) WHERE id={PH}",
            (item['qty'], item['id']))
        execute(conn, f"""INSERT INTO inventory_log
            (product_id,action,qty_change,qty_after,reason,created_by)
            SELECT {PH},'Portal order',{PH},GREATEST(0,stock_qty),{PH},{PH}
            FROM wheel_inventory WHERE id={PH}""",
            (item['id'], -item['qty'], f'Order {order_ref}', dealer['business'], item['id']))
    conn.commit(); conn.close()
    session['cart'] = {}
    session['last_order'] = order_ref
    flash(f'Order {order_ref} placed successfully!')
    return redirect(f'/portal/orders/{order_ref}')

# ── ORDERS ────────────────────────────────────────────────────────────
@app.route('/portal/orders')
@dealer_login_required
def portal_orders():
    dealer = current_dealer()
    conn = get_db()
    orders = fetchall(conn, f"SELECT * FROM dealer_orders WHERE dealer_id={PH} ORDER BY created_at DESC", (dealer['id'],))
    total_spend = float(scalar(conn, f"SELECT COALESCE(SUM(total),0) FROM dealer_orders WHERE dealer_id={PH} AND status!='Cancelled'", (dealer['id'],)))
    pending_count = int(scalar(conn, f"SELECT COUNT(*) FROM dealer_orders WHERE dealer_id={PH} AND status IN ('New','Processing')", (dealer['id'],)))
    conn.close()
    # Parse items_json for each order
    for o in orders:
        try:
            o['items'] = json.loads(o.get('items_json','[]') or '[]')
        except:
            o['items'] = []
    cart_count = sum(v.get('qty',0) for v in session.get('cart',{}).values())
    return render_template('orders.html',
        orders=orders, total_spend=total_spend, pending_count=pending_count,
        cart_count=cart_count, dealer=current_dealer())

@app.route('/portal/orders/<ref>')
@dealer_login_required
def portal_order_detail(ref):
    dealer = current_dealer()
    conn = get_db()
    order = fetchone(conn, f"SELECT * FROM dealer_orders WHERE order_ref={PH} AND dealer_id={PH}", (ref, dealer['id']))
    conn.close()
    if not order: flash('Order not found'); return redirect('/portal/orders')
    try:
        items = json.loads(order.get('items_json','[]'))
    except:
        items = []
    cart_count = sum(v.get('qty',0) for v in session.get('cart',{}).values())
    return render_template('order_detail.html',
        order=order, items=items, cart_count=cart_count, dealer=current_dealer())

# ── INVOICES ──────────────────────────────────────────────────────────
@app.route('/portal/invoices')
@dealer_login_required
def portal_invoices():
    dealer = current_dealer()
    conn = get_db()
    invoices = fetchall(conn, f"""SELECT i.* FROM invoices i
        LEFT JOIN customers c ON i.customer_id=c.id
        WHERE LOWER(c.email)={PH} OR LOWER(i.customer_name) ILIKE {PH}
        ORDER BY i.created_at DESC LIMIT 50""",
        (dealer['email'].lower(), f"%{dealer['business']}%") if USE_PG else
        (dealer['email'].lower(), f"%{dealer['business']}%"))
    outstanding = float(scalar(conn, f"""SELECT COALESCE(SUM(amount_due),0) FROM invoices i
        LEFT JOIN customers c ON i.customer_id=c.id
        WHERE (LOWER(c.email)={PH} OR LOWER(i.customer_name) ILIKE {PH})
        AND i.status IN ('Posted','Partial')""",
        (dealer['email'].lower(), f"%{dealer['business']}%")))
    conn.close()
    cart_count = sum(v.get('qty',0) for v in session.get('cart',{}).values())
    return render_template('invoices.html',
        invoices=invoices, outstanding=outstanding,
        cart_count=cart_count, dealer=current_dealer())

# ── ACCOUNT ───────────────────────────────────────────────────────────
@app.route('/portal/account', methods=['GET','POST'])
@dealer_login_required
def portal_account():
    dealer = current_dealer()
    conn = get_db()
    d = fetchone(conn, f"SELECT * FROM dealers WHERE id={PH}", (dealer['id'],))
    if request.method == 'POST':
        action = request.form.get('action','')
        if action == 'update':
            execute(conn, f"UPDATE dealers SET contact_name={PH},phone={PH} WHERE id={PH}",
                (request.form.get('contact_name',''), request.form.get('phone',''), dealer['id']))
            conn.commit()
            session['dealer_name'] = request.form.get('contact_name','')
            flash('Profile updated')
        elif action == 'password':
            old_pw = request.form.get('old_password','')
            new_pw = request.form.get('new_password','')
            if check_password_hash(d['password_hash'], old_pw):
                execute(conn, f"UPDATE dealers SET password_hash={PH} WHERE id={PH}",
                    (generate_password_hash(new_pw), dealer['id']))
                conn.commit()
                flash('Password updated')
            else:
                flash('Current password is incorrect')
        conn.close()
        return redirect('/portal/account')
    conn.close()
    cart_count = sum(v.get('qty',0) for v in session.get('cart',{}).values())
    return render_template('account.html',
        d=d, cart_count=cart_count, dealer=current_dealer())

# ── ENTRY POINT ───────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)
