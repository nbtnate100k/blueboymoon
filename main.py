"""
PLUXO Combined Server
Runs both the API server and Telegram bot for Railway deployment
"""

import json
import os
import logging
import threading
import asyncio
import re
import random
import string
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, request, jsonify
from flask_cors import CORS
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8282351962:AAFXNMRR2_0Y1z4lTkvwwD_9EXzINPVI538")
OWNER_ID = int(os.getenv("OWNER_ID", "7173346586"))
OWNER_USERNAME = "@Xeeznk"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "pluxo_secret_2024")
PORT = int(os.getenv("PORT", 5000))

# Data files
DATA_DIR = "bot_data"
ADMINS_FILE = os.path.join(DATA_DIR, "admins.json")
BALANCES_FILE = os.path.join(DATA_DIR, "balances.json")
PURCHASES_FILE = os.path.join(DATA_DIR, "purchases.json")
LOGS_FILE = os.path.join(DATA_DIR, "action_logs.json")
SHOP_PRODUCTS_FILE = "shop_products.json"

SYSTEM_LOCKED = False

# ==================== LOGGING ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== DATA FUNCTIONS ====================
def ensure_data_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

def load_json(filepath, default=None):
    if default is None:
        default = {}
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading {filepath}: {e}")
    return default

def save_json(filepath, data):
    ensure_data_dir()
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Error saving {filepath}: {e}")
        return False

def load_admins():
    data = load_json(ADMINS_FILE, {"admins": [OWNER_ID]})
    admins = set(data.get("admins", [OWNER_ID]))
    admins.add(OWNER_ID)
    return admins

def save_admins(admin_set):
    admin_list = list(admin_set)
    if OWNER_ID not in admin_list:
        admin_list.insert(0, OWNER_ID)
    save_json(ADMINS_FILE, {"admins": admin_list})

def load_balances():
    return load_json(BALANCES_FILE, {})

def save_balances(balances):
    save_json(BALANCES_FILE, balances)

def load_purchases():
    return load_json(PURCHASES_FILE, {"purchases": []})

def save_purchases(purchases):
    save_json(PURCHASES_FILE, purchases)

def log_action(admin_id, admin_name, action, details=""):
    logs = load_json(LOGS_FILE, {"logs": []})
    logs["logs"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "admin_id": admin_id,
        "admin_name": admin_name,
        "action": action,
        "details": details
    })
    logs["logs"] = logs["logs"][-1000:]
    save_json(LOGS_FILE, logs)

def generate_key(length=16):
    """Generate a random alphanumeric key"""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def parse_bulk_cards(text: str):
    """Parse bulk pipe-delimited cards (card|mm|yyyy|cvv)"""
    cards = []
    lines = text.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Match: 5355851164846467|02|2026|358
        match = re.match(r'^(\d{15,16})\|(\d{1,2})\|(\d{4})\|(\d{3,4})$', line)
        if match:
            card_number = match.group(1)
            exp_month = match.group(2).zfill(2)
            exp_year = match.group(3)
            cvv = match.group(4)
            
            if len(card_number) == 15:
                card_number = '0' + card_number
            
            cards.append({
                'card_number': card_number,
                'exp_month': exp_month,
                'exp_year': exp_year,
                'cvv': cvv,
                'full_text': f"{card_number}|{exp_month}|{exp_year}|{cvv}",
                'name': '',
                'address': '',
                'city_state_zip': '',
                'country': ''
            })
    
    return cards

def parse_multiline_cards(text: str):
    """Parse multi-line cards with address info (card exp cvv + 4 lines of info)"""
    cards = []
    lines = text.strip().split('\n')
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        
        # Match: 4145670692391812 01/29 651
        match = re.match(r'^(\d{15,16})\s+(\d{2})/(\d{2})\s+(\d{3,4})$', line)
        if match:
            card_number = match.group(1)
            exp_month = match.group(2)
            exp_year_short = match.group(3)
            cvv = match.group(4)
            
            # Convert 2-digit year to 4-digit
            exp_year = '20' + exp_year_short
            
            if len(card_number) == 15:
                card_number = '0' + card_number
            
            # Get next 4 lines for address info
            name = lines[i + 1].strip() if i + 1 < len(lines) else ''
            address = lines[i + 2].strip() if i + 2 < len(lines) else ''
            city_state_zip = lines[i + 3].strip() if i + 3 < len(lines) else ''
            country = lines[i + 4].strip() if i + 4 < len(lines) else ''
            
            full_text = f"{card_number} {exp_month}/{exp_year_short} {cvv}\n{name}\n{address}\n{city_state_zip}\n{country}"
            
            cards.append({
                'card_number': card_number,
                'exp_month': exp_month,
                'exp_year': exp_year,
                'cvv': cvv,
                'full_text': full_text,
                'name': name,
                'address': address,
                'city_state_zip': city_state_zip,
                'country': country
            })
            
            i += 5  # Skip to next card block
            continue
        
        i += 1
    
    return cards

def parse_all_formats(text: str):
    """Try both card formats and return parsed cards"""
    # Try pipe format first
    cards = parse_bulk_cards(text)
    if cards:
        return cards
    
    # Try multi-line format
    cards = parse_multiline_cards(text)
    if cards:
        return cards
    
    return []

def get_brand_from_bin(bin_str):
    """Determine card brand from BIN"""
    if not bin_str or len(bin_str) < 6:
        return "VISA"
    first_digit = bin_str[0]
    if first_digit == "4":
        return "VISA"
    elif first_digit == "5":
        return "MASTERCARD"
    elif first_digit == "3":
        return "AMEX"
    return "VISA"

ADMIN_IDS = load_admins()

# ==================== FLASK APP ====================
app = Flask(__name__)
# Allow CORS from any origin (needed for GitHub Pages -> Railway)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=False)

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "server": "PLUXO API"})

@app.route('/api/products', methods=['GET', 'OPTIONS'])
def get_products():
    """Serve shop products for the website"""
    if request.method == 'OPTIONS':
        return '', 204
    try:
        shop_products = load_json(SHOP_PRODUCTS_FILE, [])
        return jsonify(shop_products)
    except Exception as e:
        logger.error(f"Products API error: {e}")
        return jsonify([]), 200  # Return empty array on error

@app.route('/api/register', methods=['POST', 'OPTIONS'])
def webhook_register():
    if request.method == 'OPTIONS':
        return '', 204
    try:
        secret = request.headers.get('X-Webhook-Secret', '')
        if secret != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401
        
        data = request.json
        username = data.get('username', '').lower().strip()
        email = data.get('email', '')
        
        if not username:
            return jsonify({"error": "Username required"}), 400
        
        balances = load_balances()
        is_new = username not in balances
        
        if is_new:
            balances[username] = {
                "balance": 0,
                "totalRecharge": 0,
                "email": email,
                "registeredAt": datetime.now(timezone.utc).isoformat()
            }
            save_balances(balances)
            log_action(0, "WEBSITE", "NEW_USER", f"User registered: {username}")
            logger.info(f"New user registered: {username}")
        
        return jsonify({"success": True, "username": username, "isNew": is_new})
    except Exception as e:
        logger.error(f"Register error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/balance/<username>', methods=['GET', 'OPTIONS'])
def get_user_balance(username):
    if request.method == 'OPTIONS':
        return '', 204
    try:
        secret = request.headers.get('X-Webhook-Secret', '')
        if secret != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401
        
        username = username.lower().strip()
        balances = load_balances()
        
        if username in balances:
            user_data = balances[username]
            return jsonify({
                "success": True,
                "username": username,
                "balance": user_data.get("balance", 0),
                "totalRecharge": user_data.get("totalRecharge", 0)
            })
        else:
            return jsonify({"success": True, "username": username, "balance": 0, "totalRecharge": 0})
    except Exception as e:
        logger.error(f"Balance API error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/balance/update', methods=['POST', 'OPTIONS'])
def update_user_balance():
    if request.method == 'OPTIONS':
        return '', 204
    try:
        secret = request.headers.get('X-Webhook-Secret', '')
        if secret != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401
        
        data = request.json
        username = data.get('username', '').lower().strip()
        action = data.get('action', '')
        amount = float(data.get('amount', 0))
        
        if not username or not action or amount <= 0:
            return jsonify({"error": "Invalid parameters"}), 400
        
        balances = load_balances()
        if username not in balances:
            balances[username] = {"balance": 0, "totalRecharge": 0}
        
        old_balance = balances[username].get("balance", 0)
        
        if action == 'subtract':
            if old_balance < amount:
                return jsonify({"error": "Insufficient balance"}), 400
            new_balance = old_balance - amount
        elif action == 'add':
            new_balance = old_balance + amount
        else:
            return jsonify({"error": "Invalid action"}), 400
        
        balances[username]["balance"] = new_balance
        save_balances(balances)
        
        return jsonify({"success": True, "username": username, "oldBalance": old_balance, "newBalance": new_balance})
    except Exception as e:
        logger.error(f"Balance update error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/purchase/notify', methods=['POST', 'OPTIONS'])
def notify_purchase():
    """Notify admins about a purchase made on the website"""
    if request.method == 'OPTIONS':
        return '', 204
    try:
        secret = request.headers.get('X-Webhook-Secret', '')
        if secret != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401
        
        data = request.json
        username = data.get('username', '').lower().strip()
        item_count = data.get('item_count', 1)
        total_amount = float(data.get('total_amount', 0))
        
        if not username:
            return jsonify({"error": "Username required"}), 400
        
        # Send notification to all admins via Telegram bot (run in background)
        threading.Thread(target=lambda: asyncio.run(notify_admins_purchase(username, item_count, total_amount)), daemon=True).start()
        
        return jsonify({"success": True, "message": "Notification sent"})
    except Exception as e:
        logger.error(f"Purchase notification error: {e}")
        return jsonify({"error": str(e)}), 500

async def notify_admins_purchase(username, item_count, total_amount):
    """Send purchase notification to all admins"""
    try:
        bot = Bot(token=BOT_TOKEN)
        purchase_time = datetime.now(timezone.utc)
        date_str = purchase_time.strftime('%Y-%m-%d')
        time_str = purchase_time.strftime('%H:%M:%S UTC')
        
        message = f"""🛒 **Purchase Made**
━━━━━━━━━━━━━━━━━━
👤 Username: `{username}`
📦 Items: {item_count}
💵 Total: **${total_amount:.2f}**
📅 Date: {date_str}
🕐 Time: {time_str}
━━━━━━━━━━━━━━━━━━"""
        
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(chat_id=admin_id, text=message, parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
    except Exception as e:
        logger.error(f"Error sending purchase notification: {e}")

# ==================== TELEGRAM BOT DECORATORS ====================
def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            return
        return await func(update, context)
    return wrapper

def owner_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != OWNER_ID:
            return
        return await func(update, context)
    return wrapper

# ==================== TELEGRAM BOT COMMANDS ====================
@admin_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_owner = user.id == OWNER_ID
    msg = f"""
🔐 **PLUXO Admin Bot**
━━━━━━━━━━━━━━━━━━
👤 Welcome, {user.first_name}
👑 Role: {"OWNER" if is_owner else "ADMIN"}

💰 **Balance:**
/balance <user> - View balance
/setbalance <user> <amt> - Set balance
/addbalance <user> <amt> - Add balance
/removebalance <user> <amt> - Remove balance
/users - List all users

📦 **Stock:**
/stock <price> <cards> - Add cards to shop

👥 **Admin (Owner only):**
/addadmin <id> - Add admin
/removeadmin <id> - Remove admin
/admins - List admins
"""
    await update.message.reply_text(msg, parse_mode='Markdown')

@admin_only
async def view_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: /balance <username>")
        return
    username = context.args[0].lower().strip().lstrip('@')
    balances = load_balances()
    if username not in balances:
        await update.message.reply_text(f"❌ User `{username}` not found.", parse_mode='Markdown')
        return
    user_data = balances[username]
    await update.message.reply_text(f"""
💰 **Balance Info**
━━━━━━━━━━━━━━━━━━
👤 User: `{username}`
💵 Balance: **${user_data.get('balance', 0):.2f}**
📊 Total Recharge: ${user_data.get('totalRecharge', 0):.2f}
""", parse_mode='Markdown')

@admin_only
async def set_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❌ Usage: /setbalance <username> <amount>")
        return
    username = context.args[0].lower().strip().lstrip('@')
    try:
        amount = float(context.args[1].replace('$', '').replace(',', ''))
    except ValueError:
        await update.message.reply_text("❌ Invalid amount")
        return
    balances = load_balances()
    old_balance = balances.get(username, {}).get("balance", 0)
    if username not in balances:
        balances[username] = {"balance": 0, "totalRecharge": 0, "registeredAt": datetime.now(timezone.utc).isoformat()}
    balances[username]["balance"] = amount
    save_balances(balances)
    user = update.effective_user
    log_action(user.id, user.first_name, "SET_BALANCE", f"{username}: ${old_balance:.2f} -> ${amount:.2f}")
    await update.message.reply_text(f"✅ **Balance Set**\n👤 User: `{username}`\n💵 New Balance: **${amount:.2f}**", parse_mode='Markdown')

@admin_only
async def add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❌ Usage: /addbalance <username> <amount>")
        return
    username = context.args[0].lower().strip().lstrip('@')
    try:
        amount = float(context.args[1].replace('$', '').replace(',', ''))
    except ValueError:
        await update.message.reply_text("❌ Invalid amount")
        return
    balances = load_balances()
    if username not in balances:
        balances[username] = {"balance": 0, "totalRecharge": 0, "registeredAt": datetime.now(timezone.utc).isoformat()}
    old_balance = balances[username].get("balance", 0)
    new_balance = old_balance + amount
    balances[username]["balance"] = new_balance
    balances[username]["totalRecharge"] = balances[username].get("totalRecharge", 0) + amount
    save_balances(balances)
    user = update.effective_user
    log_action(user.id, user.first_name, "ADD_BALANCE", f"{username}: +${amount:.2f}")
    await update.message.reply_text(f"""
✅ **Balance Added**
👤 User: `{username}`
➕ Added: +${amount:.2f}
💵 New Balance: **${new_balance:.2f}**
""", parse_mode='Markdown')

@admin_only
async def remove_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❌ Usage: /removebalance <username> <amount>")
        return
    username = context.args[0].lower().strip().lstrip('@')
    try:
        amount = float(context.args[1].replace('$', '').replace(',', ''))
    except ValueError:
        await update.message.reply_text("❌ Invalid amount")
        return
    balances = load_balances()
    if username not in balances:
        await update.message.reply_text(f"❌ User `{username}` not found.", parse_mode='Markdown')
        return
    old_balance = balances[username].get("balance", 0)
    new_balance = max(0, old_balance - amount)
    balances[username]["balance"] = new_balance
    save_balances(balances)
    user = update.effective_user
    log_action(user.id, user.first_name, "REMOVE_BALANCE", f"{username}: -${amount:.2f}")
    await update.message.reply_text(f"""
✅ **Balance Removed**
👤 User: `{username}`
➖ Removed: -${amount:.2f}
💵 New Balance: **${new_balance:.2f}**
""", parse_mode='Markdown')

@admin_only
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balances = load_balances()
    if not balances:
        await update.message.reply_text("📭 No users registered yet.")
        return
    msg = "👥 **Registered Users**\n━━━━━━━━━━━━━━━━━━\n"
    for i, (username, data) in enumerate(balances.items(), 1):
        balance = data.get("balance", 0)
        reg_date = data.get("registeredAt", "Unknown")[:10]
        msg += f"{i}. `{username}` - ${balance:.2f} ({reg_date})\n"
    msg += f"\n📊 Total Users: {len(balances)}"
    await update.message.reply_text(msg, parse_mode='Markdown')

@owner_only
async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_IDS
    if not context.args:
        await update.message.reply_text("❌ Usage: /addadmin <user_id>")
        return
    try:
        new_admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID")
        return
    if new_admin_id in ADMIN_IDS:
        await update.message.reply_text("⚠️ User is already an admin")
        return
    ADMIN_IDS.add(new_admin_id)
    save_admins(ADMIN_IDS)
    log_action(update.effective_user.id, update.effective_user.first_name, "ADD_ADMIN", f"Added admin: {new_admin_id}")
    await update.message.reply_text(f"✅ Added admin: `{new_admin_id}`", parse_mode='Markdown')

@owner_only
async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_IDS
    if not context.args:
        await update.message.reply_text("❌ Usage: /removeadmin <user_id>")
        return
    try:
        admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID")
        return
    if admin_id == OWNER_ID:
        await update.message.reply_text("❌ Cannot remove owner")
        return
    if admin_id not in ADMIN_IDS:
        await update.message.reply_text("⚠️ User is not an admin")
        return
    ADMIN_IDS.discard(admin_id)
    save_admins(ADMIN_IDS)
    log_action(update.effective_user.id, update.effective_user.first_name, "REMOVE_ADMIN", f"Removed admin: {admin_id}")
    await update.message.reply_text(f"✅ Removed admin: `{admin_id}`", parse_mode='Markdown')

@owner_only
async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "👥 **Admin List**\n━━━━━━━━━━━━━━━━━━\n"
    for admin_id in ADMIN_IDS:
        role = "👑 OWNER" if admin_id == OWNER_ID else "🔐 Admin"
        msg += f"• `{admin_id}` - {role}\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

@admin_only
async def add_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add stock with price and BIN, optional key: /stock [price] [BIN] [key]"""
    if len(context.args) < 2:
        await update.message.reply_text("""📦 **Stock Command Usage:**

```
/stock [price] [BIN] [key]
```

**Examples:**
```
/stock 15 5355
/stock 15 5355 IEVJ073E3TFBZVJC
/stock $15 5355 IEVJ073E3TFBZVJC
/stock 12.50 4145
```

**Options:**
- Price: Number (with or without $)
- BIN: 4-6 digits
- Key: Optional, 16 characters (auto-generated if not provided)

This will create a product with:
- Price: $15.00
- BIN: 5355
- Display: `5355********** $15.0`
- Key: Provided or auto-generated""", parse_mode='Markdown')
        return
    
    # Parse price
    try:
        price_str = context.args[0].replace('$', '').replace(',', '')
        price = float(price_str)
        if price <= 0 or price > 10000:
            await update.message.reply_text("❌ Invalid price! Price must be between $0.01 and $10,000", parse_mode='Markdown')
            return
    except ValueError:
        await update.message.reply_text("❌ Invalid price format! Use a number like: 15 or 12.50", parse_mode='Markdown')
        return
    
    # Parse BIN (first 4-6 digits)
    bin_input = context.args[1].strip()
    if not bin_input.isdigit():
        await update.message.reply_text("❌ Invalid BIN! BIN must be digits only (4-6 digits)", parse_mode='Markdown')
        return
    
    # Ensure BIN is 4-6 digits
    if len(bin_input) < 4 or len(bin_input) > 6:
        await update.message.reply_text("❌ BIN must be 4-6 digits!", parse_mode='Markdown')
        return
    
    # Pad BIN to 6 digits for consistency (first 6 digits)
    bin_str = bin_input[:6].ljust(6, '0') if len(bin_input) < 6 else bin_input[:6]
    
    # Parse optional key (handle "Key: XXX" format or just the key)
    provided_key = None
    if len(context.args) >= 3:
        # Join all remaining args in case key has spaces or "Key:" prefix
        remaining_args = ' '.join(context.args[2:]).strip()
        logger.info(f"Raw key input: {remaining_args}")
        
        # Handle "Key: IEVJ073E3TFBZVJC" format
        if remaining_args.lower().startswith('key:'):
            remaining_args = remaining_args[4:].strip()
        
        # Remove any non-alphanumeric characters and uppercase
        cleaned_key = ''.join(c for c in remaining_args if c.isalnum()).upper()
        logger.info(f"Cleaned key: {cleaned_key}")
        
        # Validate key format (alphanumeric, at least 8 chars)
        if cleaned_key and len(cleaned_key) >= 8:
            provided_key = cleaned_key
            logger.info(f"Using provided key: {provided_key}")
        else:
            await update.message.reply_text(f"❌ Invalid key format! Key must be alphanumeric and at least 8 characters. Got: `{cleaned_key}` (length: {len(cleaned_key)})", parse_mode='Markdown')
            return
    
    # Load existing shop products
    shop_products = load_json(SHOP_PRODUCTS_FILE, [])
    if not isinstance(shop_products, list):
        shop_products = []
    
    # Get next ID
    next_id = max([p.get('id', 0) for p in shop_products], default=0) + 1
    
    # Use provided key or generate unique key
    existing_keys = {p.get('key', '') for p in shop_products}
    if provided_key:
        # Check if key already exists
        if provided_key in existing_keys:
            await update.message.reply_text(f"❌ Key `{provided_key}` already exists! Please use a different key.", parse_mode='Markdown')
            return
        key = provided_key
    else:
        # Generate unique key
        key = generate_key()
        while key in existing_keys:
            key = generate_key()
    
    # Determine brand from BIN
    brand = get_brand_from_bin(bin_str)
    
    # Create product entry matching shop_products.json format
    product_entry = {
        "id": next_id,
        "bin": bin_str,
        "brand": brand,
        "type": "CREDIT",
        "country": {
            "flag": "🇺🇸",
            "flagClass": "fi-us",
            "code": "US",
            "name": "USA"
        },
        "hasName": True,
        "hasAddress": True,
        "hasZip": True,
        "hasPhone": False,
        "hasMail": False,
        "hasSSN": False,
        "hasDOB": False,
        "bank": "BANK",
        "base": "2026_US_Base",
        "refundable": True,
        "price": str(price),
        "key": key,
        "seller_id": str(update.effective_user.id),
        "full_info": ""
    }
    
    shop_products.append(product_entry)
    
    # Save shop products
    save_json(SHOP_PRODUCTS_FILE, shop_products)
    
    # Build response
    masked_display = bin_str + "**********"
    key_source = "Provided" if provided_key else "Auto-generated"
    response = f"""✅ **Stock Added Successfully!**

📦 **Product Details:**
• BIN: `{bin_str}`
• Display: `{masked_display}`
• Price: **${price:.2f}**
• Brand: {brand}

🔑 **Key:** `{key}` ({key_source})

📊 Total stock: {len(shop_products)} products"""
    
    user = update.effective_user
    log_action(user.id, user.first_name, "ADD_STOCK", f"Added BIN {bin_str} at ${price:.2f} with key {key}")
    
    await update.message.reply_text(response, parse_mode='Markdown')

# ==================== BOT THREAD ====================
def run_bot():
    """Run the Telegram bot in a separate thread"""
    async def main():
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", start))
        application.add_handler(CommandHandler("balance", view_balance))
        application.add_handler(CommandHandler("setbalance", set_balance))
        application.add_handler(CommandHandler("addbalance", add_balance))
        application.add_handler(CommandHandler("removebalance", remove_balance))
        application.add_handler(CommandHandler("users", list_users))
        application.add_handler(CommandHandler("addadmin", add_admin))
        application.add_handler(CommandHandler("removeadmin", remove_admin))
        application.add_handler(CommandHandler("admins", list_admins))
        application.add_handler(CommandHandler("stock", add_stock))
        
        logger.info("Bot started with polling...")
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        
        # Keep running
        while True:
            await asyncio.sleep(3600)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())

# ==================== MAIN ====================
ensure_data_dir()

# Start bot in background thread when module loads (works with gunicorn)
# Use a flag to prevent multiple starts
_bot_started = False
def start_bot_once():
    global _bot_started
    if not _bot_started:
        _bot_started = True
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        logger.info("Telegram bot thread started")

start_bot_once()

if __name__ == "__main__":
    # Run Flask server (for local development)
    logger.info(f"Starting PLUXO API Server on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
