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
ADMIN_IDS = {1286638668, 580981359}

# Helpers
def get_current_time():
    return datetime.now().isoformat()

def get_next_id(key_prefix):
    counter_key = f"{key_prefix}:counter"
    try:
        return redis_client.incr(counter_key)
    except Exception as e:
        print(f"Error get_next_id for {key_prefix}: {e}")
        return None

# ================== PRODUCTS ==================
def get_all_products():
    try:
        product_keys = redis_client.keys(f"{PRODUCTS_KEY}:*")
        products = []
        for key in product_keys:
            if key.endswith(":counter"):
                continue
            data = redis_client.hgetall(key)
            if data:
                try:
                    data["id"] = int(data["id"])
                    data["price"] = int(data["price"])
                    data["stock"] = int(data["stock"])
                except (ValueError, KeyError) as e:
                    print(f"Error parsing product data for {key}: {e}")
                    continue
                products.append(data)
        return sorted(products, key=lambda x: x["id"])
    except Exception as e:
        print(f"Error get_all_products: {e}")
        return []

def save_product(product_data):
    try:
        if not product_data:
            print("Error save_product: No product data provided")
            return None
        required_fields = ['name', 'category', 'price', 'stock']
        for field in required_fields:
            if field not in product_data:
                print(f"Error save_product: Missing field {field}")
                return None
        product_id = product_data.get("id") or get_next_id(PRODUCTS_KEY)
        if not product_id:
            print("Error save_product: Failed to generate product ID")
            return None
        key = f"{PRODUCTS_KEY}:{product_id}"
        product_data["id"] = product_id
        product_data.setdefault("created_at", get_current_time())
        product_data["updated_at"] = get_current_time()
        redis_client.hset(key, mapping=product_data)
        update_stats()
        return product_data
    except Exception as e:
        print(f"Error save_product: {e}")
        return None

def delete_product(product_id):
    try:
        key = f"{PRODUCTS_KEY}:{product_id}"
        success = redis_client.delete(key) > 0
        if success:
            update_stats()
        return success
    except Exception as e:
        print(f"Error delete_product: {e}")
        return False

# ================== ORDERS ==================
def get_all_orders():
    try:
        keys = redis_client.keys(f"{ORDERS_KEY}:*")
        orders = []
        for key in keys:
            if key.endswith(":counter"):
                continue
            data = redis_client.hgetall(key)
            if data:
                try:
                    data["id"] = int(data["id"])
                    data["userId"] = int(data["userId"])
                    data["total"] = int(data["total"])
                    try:
                        data["items"] = json.loads(data.get("items", "[]"))
                    except:
                        data["items"] = []
                except (ValueError, KeyError) as e:
                    print(f"Error parsing order data for {key}: {e}")
                    continue
                orders.append(data)
        return sorted(orders, key=lambda x: x["id"], reverse=True)
    except Exception as e:
        print(f"Error get_all_orders: {e}")
        return []

def save_order(order_data):
    try:
        if not order_data:
            print("Error save_order: No order data provided")
            return None
        required_fields = ['userId', 'items', 'total']
        for field in required_fields:
            if field not in order_data:
                print(f"Error save_order: Missing field {field}")
                return None
        if not order_data['items']:
            print("Error save_order: Order must contain items")
            return None
        order_id = get_next_id(ORDERS_KEY)
        if not order_id:
            print("Error save_order: Failed to generate order ID")
            return None

        key = f"{ORDERS_KEY}:{order_id}"
        order_data["id"] = order_id
        order_data.setdefault("created_at", get_current_time())
        order_data.setdefault("status", "pending")

        # Обработка реферального кода
        referral_code = order_data.get('referralCode')
        if referral_code:
            referrer = None
            user_keys = redis_client.keys(f"{USERS_KEY}:*")
            for user_key in user_keys:
                if user_key.endswith(':counter'):
                    continue
                user_data = redis_client.hgetall(user_key)
                if user_data.get('referralCode') == referral_code:
                    referrer = user_data
                    break
            
            if referrer:
                referrer_id = int(referrer['id'])
                referrer_user = get_user(referrer_id)
                user_orders = get_orders_by_user(order_data['userId'])
                if len(user_orders) == 1:
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

        # Применение бонусов пользователя
        user = get_user(order_data['userId'])
        if user and user.get('bonus', 0) > 0:
            bonus_discount = min(user['bonus'], order_data['total'])
            order_data['total'] = max(0, order_data['total'] - bonus_discount)
            user['bonus'] = max(0, user['bonus'] - bonus_discount)
            save_user(user)

        # Сохранение заказа
        items = order_data.pop("items", [])
        order_data["items"] = json.dumps(items)
        redis_client.hset(key, mapping=order_data)
        order_data["items"] = items

        # Уменьшение стока
        for item in items:
            product_key = f"{PRODUCTS_KEY}:{item['id']}"
            if redis_client.exists(product_key):
                try:
                    current_stock = int(redis_client.hget(product_key, "stock") or 0)
                except:
                    current_stock = 0
                new_stock = max(0, current_stock - int(item["quantity"]))
                redis_client.hset(product_key, "stock", new_stock)

        update_stats()
        return order_data
    except Exception as e:
        print(f"Error save_order: {e}")
        return None

def get_orders_by_user(user_id):
    try:
        return [o for o in get_all_orders() if o["userId"] == user_id]
    except Exception as e:
        print(f"Error get_orders_by_user: {e}")
        return []

# ================== USERS ==================
def get_user(user_id):
    try:
        key = f"{USERS_KEY}:{user_id}"
        data = redis_client.hgetall(key)
        if data:
            data["id"] = int(data["id"])
            data["bonus"] = int(data.get("bonus", 0))
            data["referrals"] = json.loads(data.get("referrals", "[]"))
            data["isAdmin"] = user_id in ADMIN_IDS
            data["referralCode"] = data.get("referralCode", f"REF{user_id:06d}")
        return data
    except Exception as e:
        print(f"Error get_user: {e}")
        return None

def save_user(user_data):
    try:
        if not user_data:
            print("Error save_user: No user data provided")
            return None
        key = f"{USERS_KEY}:{user_data['id']}"
        referrals = user_data.get("referrals", [])
        user_data["referrals"] = json.dumps(referrals)
        user_data.setdefault("created_at", get_current_time())
        user_data["updated_at"] = get_current_time()
        user_data.setdefault("referralCode", f"REF{user_data['id']:06d}")
        redis_client.hset(key, mapping=user_data)
        user_data["referrals"] = referrals
        return user_data
    except Exception as e:
        print(f"Error save_user: {e}")
        return None

# ================== PROMOS ==================
def get_all_promos():
    try:
        keys = redis_client.keys(f"{PROMOS_KEY}:*")
        promos = []
        for key in keys:
            data = redis_client.hgetall(key)
            if data:
                try:
                    data["discount"] = int(data["discount"])
                    data["uses"] = int(data["uses"])
                    data["used"] = int(data.get("used", 0))
                except (ValueError, KeyError) as e:
                    print(f"Error parsing promo data for {key}: {e}")
                    continue
                promos.append(data)
        return promos
    except Exception as e:
        print(f"Error get_all_promos: {e}")
        return []

def save_promo(promo_data):
    try:
        if not promo_data:
            print("Error save_promo: No promo data provided")
            return None
        required_fields = ['code', 'discount', 'uses']
        for field in required_fields:
            if field not in promo_data:
                print(f"Error save_promo: Missing field {field}")
                return None
        key = f"{PROMOS_KEY}:{promo_data['code']}"
        if redis_client.exists(key):
            print(f"Error save_promo: Promo code {promo_data['code']} already exists")
            return None
        promo_data.setdefault("used", 0)
        promo_data.setdefault("created_at", get_current_time())
        promo_data["updated_at"] = get_current_time()
        redis_client.hset(key, mapping=promo_data)
        return promo_data
    except Exception as e:
        print(f"Error save_promo: {e}")
        return None

def delete_promo(code):
    try:
        key = f"{PROMOS_KEY}:{code}"
        return redis_client.delete(key) > 0
    except Exception as e:
        print(f"Error delete_promo: {e}")
        return False

# ================== STATS ==================
def update_stats():
    try:
        orders = get_all_orders()
        stats = {
            "total_orders": len(orders),
            "total_products": len(get_all_products()),
            "total_users": len(redis_client.keys(f"{USERS_KEY}:*")) - (1 if redis_client.exists(f"{USERS_KEY}:counter") else 0),
            "total_revenue": sum(o["total"] for o in orders if o["status"] == "completed"),
            "updated_at": get_current_time()
        }
        redis_client.hset(STATS_KEY, mapping=stats)
        return stats
    except Exception as e:
        print(f"Error update_stats: {e}")
        return {}

def get_stats():
    try:
        stats = redis_client.hgetall(STATS_KEY)
        if stats:
            for k in ["total_orders", "total_products", "total_users", "total_revenue"]:
                stats[k] = int(stats.get(k, 0))
        return stats or update_stats()
    except Exception as e:
        print(f"Error get_stats: {e}")
        return update_stats()

# ================== ROUTES ==================
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index_flask.html")

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(app.static_folder, path)

# API PRODUCTS
@app.route("/api/products", methods=["GET"])
def api_get_products():
    try:
        return jsonify(get_all_products())
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
        return jsonify({"error": "Failed to save product"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/products/<int:pid>", methods=["PUT"])
def api_update_product(pid):
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400
        data["id"] = pid
        product = save_product(data)
        if product:
            return jsonify({"success": True, "product": product})
        return jsonify({"error": "Failed to update product"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/products/<int:pid>", methods=["DELETE"])
def api_delete_product(pid):
    try:
        if delete_product(pid):
            return jsonify({"success": True})
        return jsonify({"error": "Product not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# API ORDERS
@app.route("/api/orders", methods=["GET"])
def api_get_orders():
    try:
        return jsonify(get_all_orders())
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
        order = save_order(data)
        if order:
            return jsonify({"success": True, "order": order})
        return jsonify({"error": "Failed to create order"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/orders/<int:uid>", methods=["GET"])
def api_get_user_orders(uid):
    try:
        return jsonify(get_orders_by_user(uid))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/orders/<int:oid>/status", methods=["PUT"])
def api_update_order_status(oid):
    try:
        data = request.json
        if not data or 'status' not in data:
            return jsonify({"error": "Status is required"}), 400
        key = f"{ORDERS_KEY}:{oid}"
        if redis_client.exists(key):
            redis_client.hset(key, "status", data.get("status", "pending"))
            redis_client.hset(key, "updated_at", get_current_time())
            update_stats()
            return jsonify({"success": True})
        return jsonify({"error": "Order not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# API USERS
@app.route("/api/users/<int:uid>", methods=["GET"])
def api_get_user(uid):
    try:
        user = get_user(uid)
        if user:
            return jsonify(user)
        new_user = {
            "id": uid,
            "username": f"user_{uid}",
            "bonus": 0,
            "referrals": [],
            "referralCode": f"REF{uid:06d}",
            "isAdmin": uid in ADMIN_IDS
        }
        return jsonify(save_user(new_user))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/users/<int:uid>", methods=["PUT"])
def api_update_user(uid):
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400
        data["id"] = uid
        user = save_user(data)
        if user:
            return jsonify({"success": True, "user": user})
        return jsonify({"error": "Failed to update user"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# API PROMOS
@app.route("/api/promos", methods=["GET"])
def api_get_promos():
    try:
        return jsonify(get_all_promos())
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
        key = f"{PROMOS_KEY}:{data['code']}"
        if redis_client.exists(key):
            return jsonify({"error": "Promo code already exists"}), 400
        promo = save_promo(data)
        if promo:
            return jsonify({"success": True, "promo": promo})
        return jsonify({"error": "Failed to create promo"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/promos/<code>/apply", methods=["POST"])
def api_apply_promo(code):
    try:
        data = request.json
        if not data or 'userId' not in data:
            return jsonify({"error": "User ID is required"}), 400
        key = f"{PROMOS_KEY}:{code}"
        promo = redis_client.hgetall(key)
        if not promo:
            return jsonify({"error": "Promo code not found"}), 404
        used = int(promo.get("used", 0))
        uses = int(promo.get("uses", 0))
        discount = int(promo.get("discount", 0))
        if used >= uses:
            return jsonify({"error": "Promo code limit reached"}), 400
        redis_client.hincrby(key, "used", 1)
        return jsonify({
            "success": True,
            "discount": discount,
            "message": f"Promo applied! {discount}% discount"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/promos/<code>", methods=["DELETE"])
def api_delete_promo(code):
    try:
        if delete_promo(code):
            return jsonify({"success": True})
        return jsonify({"error": "Promo not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# API STATS
@app.route("/api/stats", methods=["GET"])
def api_get_stats():
    try:
        return jsonify(get_stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/check-admin", methods=["GET"])
def api_check_admin():
    try:
        tg_id = request.args.get("tg_id")
        if not tg_id:
            return jsonify({"isAdmin": False})
        tg_id = int(tg_id)
        return jsonify({"isAdmin": tg_id in ADMIN_IDS})
    except:
        return jsonify({"isAdmin": False})

# INIT DATA
def init_sample_data():
    try:
        if not get_all_products():
            for p in [
                {"name": "Жидкость Mango", "category": "liquids", "price": 450, "stock": 10, "description": "Вкусный манго", "emoji": "🥭"},
                {"name": "Картридж JUUL", "category": "cartridges", "price": 300, "stock": 20, "description": "Оригинальные картриджи", "emoji": "💨"},
                {"name": "Под RELX Mint", "category": "pods", "price": 280, "stock": 12, "description": "Мятный вкус", "emoji": "🔥"},
                {"name": "Vaporesso XROS 3", "category": "devices", "price": 2800, "stock": 5, "description": "Компактная POD-система", "emoji": "⚡"}
            ]:
                save_product(p)
            print("✅ Sample products added")
        update_stats()
    except Exception as e:
        print(f"Error init_sample_data: {e}")

if __name__ == "__main__":
    print("🚀 Vape Shop Server starting...")
    init_sample_data()
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") == "development"
    print(f"🌐 Running on port {port} | Debug={debug}")
    print(f"👑 Admin IDs: {ADMIN_IDS}")
    app.run(host="0.0.0.0", port=port, debug=debug)
