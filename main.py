"""
PLUXO Combined Server
Runs both the API server and Telegram bot for Railway deployment
"""

import json
import os
import logging
import threading
import asyncio
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

ADMIN_IDS = load_admins()

# ==================== FLASK APP ====================
app = Flask(__name__)
# Allow CORS from any origin (needed for GitHub Pages -> Railway)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=False)

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "server": "PLUXO API"})

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
