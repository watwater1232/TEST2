import os
import json
import time
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import redis

app = Flask(__name__, static_folder="static")
CORS(app)

# Redis connection
redis_url = os.getenv("REDIS_URL", "redis://red-d2m4543uibrs73fqt7c0:6379")
try:
    redis_client = redis.from_url(redis_url, decode_responses=True)
    redis_client.ping()
    print("✅ Connected to Redis")
except Exception as e:
    print(f"❌ Redis connection failed: {e}")
    exit(1)

# Redis keys
PRODUCTS_KEY = "vape_shop:products"
ORDERS_KEY = "vape_shop:orders"
USERS_KEY = "vape_shop:users"
PROMOS_KEY = "vape_shop:promos"
STATS_KEY = "vape_shop:stats"

# Admin Telegram IDs
ADMIN_IDS = {1286638668}

# Helper functions
def get_current_time():
    return datetime.now().isoformat()

def get_next_id(key_prefix):
    return redis_client.incr(f"{key_prefix}:counter")

# Product management
def get_all_products():
    try:
        product_keys = redis_client.keys(f"{PRODUCTS_KEY}:*")
        if not product_keys:
            return []
        
        products = []
        for key in product_keys:
            if key.endswith(':counter'):
                continue
            product_data = redis_client.hgetall(key)
            if product_data:
                product_data['id'] = int(product_data['id'])
                product_data['price'] = int(product_data['price'])
                product_data['stock'] = int(product_data['stock'])
                products.append(product_data)
        
        return sorted(products, key=lambda x: x['id'])
    except Exception as e:
        print(f"Error getting products: {e}")
        return []

def save_product(product_data):
    try:
        if 'id' not in product_data:
            product_data['id'] = get_next_id(PRODUCTS_KEY)
        
        product_key = f"{PRODUCTS_KEY}:{product_data['id']}"
        
        product_data.setdefault('created_at', get_current_time())
        product_data['updated_at'] = get_current_time()
        
        redis_client.hset(product_key, mapping=product_data)
        return product_data
    except Exception as e:
        print(f"Error saving product: {e}")
        return None

def delete_product(product_id):
    try:
        product_key = f"{PRODUCTS_KEY}:{product_id}"
        return redis_client.delete(product_key) > 0
    except Exception as e:
        print(f"Error deleting product: {e}")
        return False

# Order management
def get_all_orders():
    try:
        order_keys = redis_client.keys(f"{ORDERS_KEY}:*")
        if not order_keys:
            return []
        
        orders = []
        for key in order_keys:
            if key.endswith(':counter'):
                continue
            order_data = redis_client.hgetall(key)
            if order_data:
                order_data['id'] = int(order_data['id'])
                order_data['userId'] = int(order_data['userId'])
                order_data['total'] = int(order_data['total'])
                order_data['items'] = json.loads(order_data['items'])
                orders.append(order_data)
        
        return sorted(orders, key=lambda x: x['id'], reverse=True)
    except Exception as e:
        print(f"Error getting orders: {e}")
        return []

def save_order(order_data):
    try:
        if 'id' not in order_data:
            order_data['id'] = get_next_id(ORDERS_KEY)
        
        order_key = f"{ORDERS_KEY}:{order_data['id']}"
        
        # Обработка реферального кода
        referral_code = order_data.get('referralCode')
        if referral_code:
            referrer = None
            user_keys = redis_client.keys(f"{USERS_KEY}:*")
            for key in user_keys:
                if key.endswith(':counter'):
                    continue
                user_data = redis_client.hgetall(key)
                if user_data.get('referralCode') == referral_code:
                    referrer = user_data
                    break
            
            if referrer:
                referrer_id = int(referrer['id'])
                referrer_user = get_user(referrer_id)
                bonus = int(order_data['total'] * 0.1)
                referrer_user['bonus'] = int(referrer_user.get('bonus', 0)) + bonus
                referrer_user['referrals'] = json.loads(referrer_user.get('referrals', '[]'))
                if order_data['userId'] not in referrer_user['referrals']:
                    referrer_user['referrals'].append(order_data['userId'])
                save_user(referrer_user)

                invited_user = get_user(order_data['userId'])
                invited_user['bonus'] = int(invited_user.get('bonus', 0)) + bonus
                save_user(invited_user)

        # Применение промокода
        promo_code = order_data.get('promoCode')
        if promo_code:
            promo_key = f"{PROMOS_KEY}:{promo_code}"
            promo_data = redis_client.hgetall(promo_key)
            if promo_data:
                promo_data['used'] = int(promo_data.get('used', 0))
                promo_data['uses'] = int(promo_data['uses'])
                if promo_data['used'] < promo_data['uses']:
                    discount = int(promo_data['discount'])
                    order_data['total'] = int(order_data['total'] * (1 - discount / 100))
                    redis_client.hincrby(promo_key, 'used', 1)

        order_data.setdefault('created_at', get_current_time())
        order_data.setdefault('status', 'pending')
        
        items = order_data.pop('items', [])
        order_data['items'] = json.dumps(items)
        
        redis_client.hset(order_key, mapping=order_data)
        
        order_data['items'] = items
        update_stats()
        
        return order_data
    except Exception as e:
        print(f"Error saving order: {e}")
        return None

def get_orders_by_user(user_id):
    try:
        all_orders = get_all_orders()
        return [order for order in all_orders if order['userId'] == user_id]
    except Exception as e:
        print(f"Error getting user orders: {e}")
        return []

# User management
def get_user(user_id):
    try:
        user_key = f"{USERS_KEY}:{user_id}"
        user_data = redis_client.hgetall(user_key)
        if user_data:
            user_data['id'] = int(user_data['id'])
            user_data['bonus'] = int(user_data.get('bonus', 0))
            user_data['referrals'] = json.loads(user_data.get('referrals', '[]'))
            user_data['isAdmin'] = user_id in ADMIN_IDS
        return user_data
    except Exception as e:
        print(f"Error getting user: {e}")
        return None

def save_user(user_data):
    try:
        user_key = f"{USERS_KEY}:{user_data['id']}"
        referrals = user_data.get('referrals', [])
        user_data['referrals'] = json.dumps(referrals)
        user_data.setdefault('created_at', get_current_time())
        user_data['updated_at'] = get_current_time()
        user_data.setdefault('referralCode', f'REF{user_data["id"]:06d}')
        
        redis_client.hset(user_key, mapping=user_data)
        user_data['referrals'] = referrals
        return user_data
    except Exception as e:
        print(f"Error saving user: {e}")
        return None

# Promo management
def get_all_promos():
    try:
        promo_keys = redis_client.keys(f"{PROMOS_KEY}:*")
        if not promo_keys:
            return []
        
        promos = []
        for key in promo_keys:
            promo_data = redis_client.hgetall(key)
            if promo_data:
                promo_data['discount'] = int(promo_data['discount'])
                promo_data['uses'] = int(promo_data['uses'])
                promo_data['used'] = int(promo_data.get('used', 0))
                promos.append(promo_data)
        
        return promos
    except Exception as e:
        print(f"Error getting promos: {e}")
        return []

def save_promo(promo_data):
    try:
        promo_key = f"{PROMOS_KEY}:{promo_data['code']}"
        
        promo_data.setdefault('used', 0)
        promo_data.setdefault('created_at', get_current_time())
        promo_data['updated_at'] = get_current_time()
        
        redis_client.hset(promo_key, mapping=promo_data)
        return promo_data
    except Exception as e:
        print(f"Error saving promo: {e}")
        return None

def delete_promo(code):
    try:
        promo_key = f"{PROMOS_KEY}:{code}"
        return redis_client.delete(promo_key) > 0
    except Exception as e:
        print(f"Error deleting promo: {e}")
        return False

# Stats management
def update_stats():
    try:
        orders = get_all_orders()
        stats = {
            'total_orders': len(orders),
            'total_products': len(get_all_products()),
            'total_users': len(redis_client.keys(f"{USERS_KEY}:*")) - (1 if redis_client.exists(f"{USERS_KEY}:counter") else 0),
            'total_revenue': sum(order['total'] for order in orders if order['status'] == 'completed'),
            'updated_at': get_current_time()
        }
        redis_client.hset(STATS_KEY, mapping=stats)
        return stats
    except Exception as e:
        print(f"Error updating stats: {e}")
        return {}

def get_stats():
    try:
        stats = redis_client.hgetall(STATS_KEY)
        if stats:
            for key in ['total_orders', 'total_products', 'total_users', 'total_revenue']:
                stats[key] = int(stats.get(key, 0))
        return stats or update_stats()
    except Exception as e:
        print(f"Error getting stats: {e}")
        return update_stats()

# Routes
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index_flask.html")

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(app.static_folder, path)

# API Routes
@app.route("/api/products", methods=["GET"])
def api_get_products():
    try:
        products = get_all_products()
        return jsonify(products)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/products", methods=["POST"])
def api_add_product():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        required_fields = ['name', 'category', 'price', 'stock']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing field: {field}"}), 400
        
        product = save_product(data)
        if product:
            return jsonify({"success": True, "product": product})
        else:
            return jsonify({"error": "Failed to save product"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/products/<int:product_id>", methods=["PUT"])
def api_update_product(product_id):
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        data['id'] = product_id
        product = save_product(data)
        if product:
            return jsonify({"success": True, "product": product})
        else:
            return jsonify({"error": "Failed to update product"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/products/<int:product_id>", methods=["DELETE"])
def api_delete_product(product_id):
    try:
        if delete_product(product_id):
            return jsonify({"success": True})
        else:
            return jsonify({"error": "Product not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/orders", methods=["GET"])
def api_get_orders():
    try:
        orders = get_all_orders()
        return jsonify(orders)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/orders", methods=["POST"])
def api_create_order():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        required_fields = ['userId', 'items', 'total']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing field: {field}"}), 400
        
        if not data['items']:
            return jsonify({"error": "Order must contain items"}), 400
        
        user = get_user(data['userId'])
        if user and user.get('bonus', 0) > 0:
            bonus_discount = min(user['bonus'], data['total'])
            data['total'] = max(0, data['total'] - bonus_discount)
            user['bonus'] = max(0, user['bonus'] - bonus_discount)
            save_user(user)
        
        order = save_order(data)
        if order:
            return jsonify({"success": True, "order": order})
        else:
            return jsonify({"error": "Failed to create order"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/orders/<int:user_id>", methods=["GET"])
def api_get_user_orders(user_id):
    try:
        orders = get_orders_by_user(user_id)
        return jsonify(orders)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/orders/<int:order_id>/status", methods=["PUT"])
def api_update_order_status(order_id):
    try:
        data = request.json
        if not data or 'status' not in data:
            return jsonify({"error": "Status is required"}), 400
        
        order_key = f"{ORDERS_KEY}:{order_id}"
        if redis_client.exists(order_key):
            redis_client.hset(order_key, 'status', data['status'])
            redis_client.hset(order_key, 'updated_at', get_current_time())
            update_stats()
            return jsonify({"success": True})
        else:
            return jsonify({"error": "Order not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/users/<int:user_id>", methods=["GET"])
def api_get_user(user_id):
    try:
        user = get_user(user_id)
        if user:
            return jsonify(user)
        else:
            new_user = {
                'id': user_id,
                'username': f'user_{user_id}',
                'bonus': 0,
                'referrals': [],
                'referralCode': f'REF{user_id:06d}',
                'isAdmin': user_id in ADMIN_IDS
            }
            saved_user = save_user(new_user)
            return jsonify(saved_user)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/users/<int:user_id>", methods=["PUT"])
def api_update_user(user_id):
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        data['id'] = user_id
        user = save_user(data)
        if user:
            return jsonify({"success": True, "user": user})
        else:
            return jsonify({"error": "Failed to update user"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/promos", methods=["GET"])
def api_get_promos():
    try:
        promos = get_all_promos()
        return jsonify(promos)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/promos", methods=["POST"])
def api_create_promo():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        required_fields = ['code', 'discount', 'uses']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing field: {field}"}), 400
        
        promo_key = f"{PROMOS_KEY}:{data['code']}"
        if redis_client.exists(promo_key):
            return jsonify({"error": "Promo code already exists"}), 400
        
        promo = save_promo(data)
        if promo:
            return jsonify({"success": True, "promo": promo})
        else:
            return jsonify({"error": "Failed to create promo"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/promos/<code>/apply", methods=["POST"])
def api_apply_promo(code):
    try:
        data = request.json
        if not data or 'userId' not in data:
            return jsonify({"error": "User ID is required"}), 400
        
        promo_key = f"{PROMOS_KEY}:{code}"
        promo_data = redis_client.hgetall(promo_key)
        
        if not promo_data:
            return jsonify({"error": "Promo code not found"}), 404
        
        promo_data['used'] = int(promo_data.get('used', 0))
        promo_data['uses'] = int(promo_data['uses'])
        promo_data['discount'] = int(promo_data['discount'])
        
        if promo_data['used'] >= promo_data['uses']:
            return jsonify({"error": "Promo code limit reached"}), 400
        
        redis_client.hincrby(promo_key, 'used', 1)
        
        return jsonify({
            "success": True,
            "discount": promo_data['discount'],
            "message": f"Promo applied! {promo_data['discount']}% discount"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/promos/<code>", methods=["DELETE"])
def api_delete_promo(code):
    try:
        if delete_promo(code):
            return jsonify({"success": True})
        else:
            return jsonify({"error": "Promo not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/stats", methods=["GET"])
def api_get_stats():
    try:
        stats = get_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/check-admin", methods=["GET"])
def api_check_admin():
    try:
        tg_id = request.args.get('tg_id')
        if not tg_id:
            return jsonify({"isAdmin": False})
        
        try:
            tg_id = int(tg_id)
            is_admin = tg