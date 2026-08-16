import asyncio
from datetime import datetime, timedelta
import os
from threading import Thread
from flask import Flask

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CopyTextButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ============================================
# CONFIGURATION & INITIALIZATION
# ============================================

BOT_TOKEN = os.environ.get('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
ADMIN_ID = int(os.environ.get('ADMIN_ID', 8856827908))
DATABASE_URL = os.environ.get('DATABASE_URL')

# Dynamic Default Values
PRICE_NEW_GMAIL = 0.35
PRICE_OLD_GMAIL = 0.45
WARRANTY_DAYS = 7
USD_TO_INR = 96.30

# Payment Gateways Info (Defaults)
BINANCE_PAY_ID = "1230141397"
USDT_BEP20_ADDRESS = "0xFbaE715FeFAf06fdD6b203a769685DD25C18678C"
UPI_ID = "adarsh--hacker@fam"

# Dynamic Bot Settings & Caches
BOT_STATUS = True
MUST_JOIN_CHANNEL = None
BANNED_USERS_CACHE = set()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db_pool = None

MENU_BUTTONS = {
    "➕ Add Stock", "📦 View Inventory", "💰 Add Balance", "➖ Cut Balance",
    "🔎 Check Balance", "🏆 Top Balances", "🚫 Ban User", "✅ Unban User",
    "📢 Broadcast", "⚙️ Change Values", "💳 Transactions", "📊 View Stats",
    "📢 Must Join Channel", "🔴 Bot Status: OFF", "🟢 Bot Status: ON",
    "📥 Pending Deposits", "🔍 Find ID", "🏠 Main Menu", "🚫 Cancel"
}

# ============================================
# DUMMY FLASK SERVER FOR RENDER KEEP-ALIVE
# ============================================

flask_app = Flask('')

@flask_app.route('/')
def home():
    return "Gmail Store Bot is active and running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

# ============================================
# FSM STATES
# ============================================

class UserState(StatesGroup):
    deposit_amount = State()
    deposit_proof = State()
    support_message = State()

class AdminState(StatesGroup):
    waiting_for_bulk_accounts = State()
    waiting_for_add_balance = State()
    waiting_for_cut_balance = State()
    waiting_for_check_balance = State()
    waiting_for_ban_user = State()
    waiting_for_unban_user = State()
    waiting_for_broadcast = State()
    waiting_for_user_transactions = State()
    waiting_for_find_id_query = State()
    waiting_for_channel_link = State()
    waiting_for_support_reply = State()
    waiting_for_new_price = State()
    waiting_for_old_price = State()
    waiting_for_warranty_days = State()
    waiting_for_binance_id = State()
    waiting_for_usdt_address = State()
    waiting_for_upi_id = State()

# ============================================
# DATABASE INITIALIZATION & CACHE
# ============================================

async def init_db():
    global db_pool
    url = DATABASE_URL
    if not url:
        raise ValueError("DATABASE_URL environment variable is missing!")
        
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
        
    db_pool = await asyncpg.create_pool(
        dsn=url, 
        ssl='require', 
        min_size=3, 
        max_size=15,
        statement_cache_size=0
    )
    
    async with db_pool.acquire() as conn:
        # Users Table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                balance DOUBLE PRECISION DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS balance DOUBLE PRECISION DEFAULT 0.0")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

        # Inventory Table (Stock)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS inventory (
                id SERIAL PRIMARY KEY,
                account_type TEXT,
                credentials TEXT,
                status TEXT DEFAULT 'available',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Orders Table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                account_type TEXT,
                credentials TEXT,
                price DOUBLE PRECISION,
                warranty_until TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Deposits Table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS deposits (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                method TEXT,
                amount DOUBLE PRECISION,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Transactions Ledger Table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                type TEXT,
                amount DOUBLE PRECISION,
                note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Banned Users
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id BIGINT PRIMARY KEY
            )
        ''')

        # Settings
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')

async def load_settings_and_cache():
    global BANNED_USERS_CACHE, MUST_JOIN_CHANNEL, BOT_STATUS, PRICE_NEW_GMAIL, PRICE_OLD_GMAIL, WARRANTY_DAYS
    global BINANCE_PAY_ID, USDT_BEP20_ADDRESS, UPI_ID

    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM banned_users")
        BANNED_USERS_CACHE = {r['user_id'] for r in rows}

        channel_val = await conn.fetchval("SELECT value FROM bot_settings WHERE key='must_join_channel'")
        MUST_JOIN_CHANNEL = channel_val if channel_val and channel_val != 'off' else None

        status_val = await conn.fetchval("SELECT value FROM bot_settings WHERE key='bot_status'")
        BOT_STATUS = (status_val != 'off')

        p_new = await conn.fetchval("SELECT value FROM bot_settings WHERE key='price_new_gmail'")
        if p_new:
            PRICE_NEW_GMAIL = float(p_new)

        p_old = await conn.fetchval("SELECT value FROM bot_settings WHERE key='price_old_gmail'")
        if p_old:
            PRICE_OLD_GMAIL = float(p_old)

        w_days = await conn.fetchval("SELECT value FROM bot_settings WHERE key='warranty_days'")
        if w_days:
            WARRANTY_DAYS = int(w_days)

        b_id = await conn.fetchval("SELECT value FROM bot_settings WHERE key='binance_pay_id'")
        if b_id:
            BINANCE_PAY_ID = b_id

        u_addr = await conn.fetchval("SELECT value FROM bot_settings WHERE key='usdt_bep20_address'")
        if u_addr:
            USDT_BEP20_ADDRESS = u_addr

        upi_val = await conn.fetchval("SELECT value FROM bot_settings WHERE key='upi_id'")
        if upi_val:
            UPI_ID = upi_val

# ============================================
# HELPERS & MIDDLEWARES
# ============================================

async def ensure_user(user_id: int, username: str = None):
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO users (user_id, username, balance) 
            VALUES ($1, $2, 0.0) 
            ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username
        ''', user_id, username)

async def get_user_balance(user_id: int) -> float:
    async with db_pool.acquire() as conn:
        bal = await conn.fetchval("SELECT balance FROM users WHERE user_id=$1", user_id)
        return bal if bal is not None else 0.0

async def get_stock_counts():
    async with db_pool.acquire() as conn:
        new_count = await conn.fetchval("SELECT COUNT(*) FROM inventory WHERE account_type='new' AND status='available'") or 0
        old_count = await conn.fetchval("SELECT COUNT(*) FROM inventory WHERE account_type='old' AND status='available'") or 0
    return new_count, old_count

@dp.message.outer_middleware()
async def global_message_middleware(handler, event: Message, data):
    if not event.from_user:
        return await handler(event, data)
    user_id = event.from_user.id
    if user_id == ADMIN_ID:
        return await handler(event, data)
    if not BOT_STATUS:
        await event.answer('<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji> Bot is currently off. Please wait for admin to enable it.')
        return
    if user_id in BANNED_USERS_CACHE:
        await event.answer('<tg-emoji emoji-id="5274099962655816924">🚫</tg-emoji> You are banned from using this bot.')
        return
    return await handler(event, data)

# ============================================
# PAGINATED TRANSACTION RENDERER
# ============================================

async def render_transaction_history_page(target_user_id: int, page: int = 1, is_admin: bool = False):
    items_per_page = 8

    async with db_pool.acquire() as conn:
        tx_rows = await conn.fetch('''
            SELECT type, amount, note, created_at 
            FROM transactions 
            WHERE user_id=$1 
            ORDER BY id DESC
        ''', target_user_id)

    total_items = len(tx_rows)
    total_pages = max(1, (total_items + items_per_page - 1) // items_per_page)

    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages

    start_idx = (page - 1) * items_per_page
    end_idx = min(start_idx + items_per_page, total_items)
    page_items = tx_rows[start_idx:end_idx]

    header_title = f'<tg-emoji emoji-id="5440410042773824003">📜</tg-emoji> <b>Transactions for User <code>{target_user_id}</code></b>' if is_admin else '<tg-emoji emoji-id="5440410042773824003">📜</tg-emoji> <b>Transaction History</b>'

    if total_items == 0:
        text = f"{header_title}\n\n📭 No transaction records found."
    else:
        text = (
            f"{header_title}\n"
            f"Showing <b>{start_idx + 1}-{end_idx}</b> of <b>{total_items}</b> record(s).\n\n"
        )
        for tx in page_items:
            amt = tx['amount']
            sign = "+" if amt >= 0 else "-"
            type_emoji = '<tg-emoji emoji-id="6217663806110175239">🟢</tg-emoji>' if amt >= 0 else '<tg-emoji emoji-id="5274099962655816924">🔴</tg-emoji>'
            date_fmt = tx['created_at'].strftime("%b %d, %Y %I:%M %p")
            text += (
                f"{type_emoji} <b>{sign}${abs(amt):.2f}</b> | <code>{tx['type'].upper()}</code>\n"
                f"📝 <i>{tx['note']}</i>\n"
                f"📅 {date_fmt}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
            )

    kb = InlineKeyboardBuilder()
    prefix = f"adm_tx_page:{target_user_id}" if is_admin else "user_tx_page"

    if total_pages > 1:
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"{prefix}:{page - 1}"))
        nav_buttons.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"{prefix}:{page + 1}"))
        kb.row(*nav_buttons)

    if not is_admin:
        kb.row(InlineKeyboardButton(text="⬅️ Back", callback_data="menu_back", icon_custom_emoji_id="5352759161945867747"))

    return text, kb.as_markup()

# ============================================
# KEYBOARDS
# ============================================

def get_main_menu_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="Buy Gmail", callback_data="menu_buy", icon_custom_emoji_id="5377548235709619284", style="success")
    kb.button(text="Deposit Funds", callback_data="menu_deposit", icon_custom_emoji_id="5445353829304387411", style="primary")
    kb.button(text="Balance", callback_data="menu_balance", icon_custom_emoji_id="5417924076503062111", style="primary")
    kb.button(text="My Orders & Warranty", callback_data="menu_orders", icon_custom_emoji_id="5445221832074483553", style="primary")
    kb.button(text="History", callback_data="menu_history", icon_custom_emoji_id="5440410042773824003", style="primary")
    kb.button(text="Support", callback_data="menu_support", icon_custom_emoji_id="5274099962655816924", style="danger")
    kb.adjust(2, 2, 2)
    return kb.as_markup()

def get_buy_keyboard(new_stock: int, old_stock: int):
    kb = InlineKeyboardBuilder()
    kb.button(text=f"New Gmail (${PRICE_NEW_GMAIL:.2f}) [Stock: {new_stock}]", callback_data="buy_new", icon_custom_emoji_id="5870458774455587120", style="success")
    kb.button(text=f"Old Gmail (${PRICE_OLD_GMAIL:.2f}) [Stock: {old_stock}]", callback_data="buy_old", icon_custom_emoji_id="5206607081334906820", style="primary")
    kb.button(text="Back", callback_data="menu_back", icon_custom_emoji_id="5352759161945867747")
    kb.adjust(1)
    return kb.as_markup()

def get_deposit_methods_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="Binance ID", callback_data="dep_binance", icon_custom_emoji_id="5417924076503062111", style="primary")
    kb.button(text="USDT (BEP-20)", callback_data="dep_usdt", icon_custom_emoji_id="5197434882321567830", style="primary")
    kb.button(text="UPI (India)", callback_data="dep_upi", icon_custom_emoji_id="6278557702109013266", style="success")
    kb.button(text="Back", callback_data="menu_back", icon_custom_emoji_id="5352759161945867747")
    kb.adjust(1)
    return kb.as_markup()

def get_change_values_inline_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="New Gmail Price", callback_data="ch_val:new_price", icon_custom_emoji_id="5417924076503062111", style="primary")
    kb.button(text="Old Gmail Price", callback_data="ch_val:old_price", icon_custom_emoji_id="5417924076503062111", style="primary")
    kb.button(text="Warranty Days", callback_data="ch_val:warranty", icon_custom_emoji_id="5251203410396458957", style="primary")
    kb.button(text="Binance Pay ID", callback_data="ch_val:binance_id", icon_custom_emoji_id="6005570495603282482", style="primary")
    kb.button(text="USDT BEP-20 Address", callback_data="ch_val:usdt_addr", icon_custom_emoji_id="5197434882321567830", style="primary")
    kb.button(text="UPI ID", callback_data="ch_val:upi_id", icon_custom_emoji_id="6278557702109013266", style="primary")
    kb.adjust(1, 1, 1, 1, 1, 1)
    return kb.as_markup()

def get_admin_menu_keyboard():
    kb = ReplyKeyboardBuilder()
    kb.button(text="➕ Add Stock")
    kb.button(text="📦 View Inventory")
    kb.button(text="📥 Pending Deposits")
    kb.button(text="🔍 Find ID")
    kb.button(text="💰 Add Balance")
    kb.button(text="➖ Cut Balance")
    kb.button(text="🔎 Check Balance")
    kb.button(text="🏆 Top Balances")
    kb.button(text="🚫 Ban User")
    kb.button(text="✅ Unban User")
    kb.button(text="📢 Broadcast")
    kb.button(text="⚙️ Change Values")
    kb.button(text="💳 Transactions")
    kb.button(text="📊 View Stats")
    kb.button(text="📢 Must Join Channel")
    status_btn_text = "🟢 Bot Status: ON" if BOT_STATUS else "🔴 Bot Status: OFF"
    kb.button(text=status_btn_text)
    kb.button(text="🏠 Main Menu")
    kb.adjust(2, 2, 2, 2, 2, 2, 2, 2, 1)
    return kb.as_markup(resize_keyboard=True)

# ============================================
# USER STORE & ORDER SYSTEM
# ============================================

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await ensure_user(message.from_user.id, message.from_user.username)
    text = (
        f'<tg-emoji emoji-id="5458904472598095631">👋</tg-emoji> <b>Welcome to Gmail Store!</b>\n\n'
        f'<tg-emoji emoji-id="5195033767969839232">⚡</tg-emoji> <b>Pricing & Stock:</b>\n'
        f'• <b>New Gmail:</b> ${PRICE_NEW_GMAIL:.2f} / account\n'
        f'• <b>Old Gmail:</b> ${PRICE_OLD_GMAIL:.2f} / account\n'
        f'<tg-emoji emoji-id="5251203410396458957">🛡</tg-emoji> <b>Warranty:</b> {WARRANTY_DAYS} Days replacement guarantee.\n\n'
        f'Choose an option below to proceed:'
    )
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())

@dp.callback_query(F.data == "menu_back")
async def cb_menu_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    text = (
        f'<tg-emoji emoji-id="5458904472598095631">👋</tg-emoji> <b>Welcome to Gmail Store!</b>\n\n'
        f'<tg-emoji emoji-id="5195033767969839232">⚡</tg-emoji> <b>Available Products:</b>\n'
        f'• <b>New Gmail:</b> ${PRICE_NEW_GMAIL:.2f}\n'
        f'• <b>Old Gmail:</b> ${PRICE_OLD_GMAIL:.2f}\n'
        f'<tg-emoji emoji-id="5251203410396458957">🛡</tg-emoji> <b>Warranty:</b> {WARRANTY_DAYS} Days Replacement\n\n'
        f'Choose an option below to proceed:'
    )
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())

@dp.callback_query(F.data == "menu_balance")
async def cb_balance(call: CallbackQuery):
    await call.answer()
    bal = await get_user_balance(call.from_user.id)
    inr_bal = bal * USD_TO_INR
    text = (
        f'<tg-emoji emoji-id="5417924076503062111">💰</tg-emoji> <b>Your Balance Overview:</b>\n\n'
        f'<tg-emoji emoji-id="5278467510604160626">💵</tg-emoji> <b>USD Balance:</b> ${bal:.2f}\n'
        f'<tg-emoji emoji-id="6278557702109013266">🇮🇳</tg-emoji> <b>Approx INR:</b> ₹{inr_bal:.2f}\n\n'
        f'<i>Tap below to add funds to your account.</i>'
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="Deposit Now", callback_data="menu_deposit", icon_custom_emoji_id="5445353829304387411", style="primary")
    kb.button(text="Back", callback_data="menu_back", icon_custom_emoji_id="5352759161945867747")
    kb.adjust(1)
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "menu_buy")
async def cb_buy_menu(call: CallbackQuery):
    await call.answer()
    new_stock, old_stock = await get_stock_counts()
    text = (
        f'<tg-emoji emoji-id="5377548235709619284">🛒</tg-emoji> <b>Choose Gmail Category:</b>\n\n'
        f'<tg-emoji emoji-id="5870458774455587120">1️⃣</tg-emoji> <b>New Gmail:</b> ${PRICE_NEW_GMAIL:.2f} (Stock: {new_stock})\n'
        f'<tg-emoji emoji-id="5206607081334906820">2️⃣</tg-emoji> <b>Old Gmail:</b> ${PRICE_OLD_GMAIL:.2f} (Stock: {old_stock})\n\n'
        f'<tg-emoji emoji-id="5251203410396458957">🛡</tg-emoji> <i>Every purchase is covered by an automated {WARRANTY_DAYS}-day warranty.</i>'
    )
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=get_buy_keyboard(new_stock, old_stock))

@dp.callback_query(F.data.in_({"buy_new", "buy_old"}))
async def process_purchase(call: CallbackQuery):
    await call.answer()
    acc_type = "new" if call.data == "buy_new" else "old"
    price = PRICE_NEW_GMAIL if acc_type == "new" else PRICE_OLD_GMAIL
    user_id = call.from_user.id

    bal = await get_user_balance(user_id)
    if bal < price:
        await call.answer(f"❌ Insufficient balance! Required: ${price:.2f}, Balance: ${bal:.2f}", show_alert=True)
        return

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            item = await conn.fetchrow(
                "SELECT id, credentials FROM inventory WHERE account_type=$1 AND status='available' ORDER BY id ASC LIMIT 1 FOR UPDATE",
                acc_type
            )
            if not item:
                await call.answer(f"❌ Sorry, {acc_type.upper()} Gmail is currently out of stock!", show_alert=True)
                return

            warranty_date = datetime.utcnow() + timedelta(days=WARRANTY_DAYS)
            await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id=$2", price, user_id)
            await conn.execute("UPDATE inventory SET status='sold' WHERE id=$1", item['id'])
            order_id = await conn.fetchval('''
                INSERT INTO orders (user_id, account_type, credentials, price, warranty_until)
                VALUES ($1, $2, $3, $4, $5) RETURNING id
            ''', user_id, acc_type, item['credentials'], price, warranty_date)
            await conn.execute('''
                INSERT INTO transactions (user_id, type, amount, note)
                VALUES ($1, 'purchase', $2, $3)
            ''', user_id, -price, f"Purchased {acc_type.upper()} Gmail #{order_id}")

    text = (
        f'<tg-emoji emoji-id="6217663806110175239">🎉</tg-emoji> <b>Purchase Successful! Order #{order_id}</b>\n\n'
        f'<tg-emoji emoji-id="5445221832074483553">📦</tg-emoji> <b>Type:</b> {acc_type.upper()} Gmail\n'
        f'<tg-emoji emoji-id="5417924076503062111">💵</tg-emoji> <b>Price:</b> ${price:.2f}\n'
        f'<tg-emoji emoji-id="5251203410396458957">🛡</tg-emoji> <b>Warranty Active Until:</b> {warranty_date.strftime("%Y-%m-%d %H:%M:%S UTC")} ({WARRANTY_DAYS} Days)\n\n'
        f'<tg-emoji emoji-id="6005570495603282482">🔑</tg-emoji> <b>Account Credentials:</b>\n'
        f'<code>{item["credentials"]}</code>\n\n'
        f'<i>Please secure this account. Contact support if there are any login issues during your warranty period.</i>'
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="Buy Another", callback_data="menu_buy", icon_custom_emoji_id="5377548235709619284", style="success")
    kb.button(text="Main Menu", callback_data="menu_back", icon_custom_emoji_id="5352759161945867747")
    kb.adjust(2)
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "menu_orders")
async def cb_view_orders(call: CallbackQuery):
    await call.answer()
    user_id = call.from_user.id
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, account_type, credentials, warranty_until, created_at FROM orders WHERE user_id=$1 ORDER BY id DESC LIMIT 5", user_id)

    if not rows:
        text = '<tg-emoji emoji-id="5445221832074483553">📦</tg-emoji> <b>You have not purchased any accounts yet.</b>'
    else:
        text = '<tg-emoji emoji-id="5445221832074483553">📦</tg-emoji> <b>Your Recent Orders:</b>\n\n'
        now = datetime.utcnow()
        for r in rows:
            warranty_left = r['warranty_until'] - now
            if warranty_left.total_seconds() > 0:
                warranty_status = f'<tg-emoji emoji-id="6217663806110175239">🟢</tg-emoji> Active ({warranty_left.days}d {warranty_left.seconds // 3600}h left)'
            else:
                warranty_status = '<tg-emoji emoji-id="5274099962655816924">🔴</tg-emoji> Expired'

            text += (
                f'<tg-emoji emoji-id="5197269100878907942">🆔</tg-emoji> <b>Order #{r["id"]}</b> ({r["account_type"].upper()})\n'
                f'<tg-emoji emoji-id="6005570495603282482">🔑</tg-emoji> <code>{r["credentials"]}</code>\n'
                f'🛡 Warranty: {warranty_status}\n'
                f'📅 Date: {r["created_at"].strftime("%b %d, %Y")}\n'
                f'━━━━━━━━━━━━━━━━━━\n'
            )

    kb = InlineKeyboardBuilder()
    kb.button(text="Back", callback_data="menu_back", icon_custom_emoji_id="5352759161945867747")
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "menu_history")
async def cb_history(call: CallbackQuery):
    await call.answer()
    text, markup = await render_transaction_history_page(call.from_user.id, page=1, is_admin=False)
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)

@dp.callback_query(F.data.startswith("user_tx_page:"))
async def cb_user_tx_page(call: CallbackQuery):
    await call.answer()
    page = int(call.data.split(":")[1])
    text, markup = await render_transaction_history_page(call.from_user.id, page=page, is_admin=False)
    try:
        await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except Exception:
        pass

@dp.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer()

# ============================================
# DEPOSIT SYSTEM & PROOF
# ============================================

@dp.callback_query(F.data == "menu_deposit")
async def cb_deposit_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    text = (
        f'<tg-emoji emoji-id="5445353829304387411">💳</tg-emoji> <b>Choose Payment Method:</b>\n\n'
        f'• <b>Binance ID:</b> Instant transfer via Binance Pay\n'
        f'• <b>USDT (BEP-20):</b> Binance Smart Chain network\n'
        f'• <b>UPI (India):</b> Instant INR transfer ($1 = ₹{USD_TO_INR:.2f})\n\n'
        f'Select an option below:'
    )
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=get_deposit_methods_keyboard())

@dp.callback_query(F.data.in_({"dep_binance", "dep_usdt", "dep_upi"}))
async def cb_select_deposit_method(call: CallbackQuery, state: FSMContext):
    await call.answer()
    method_name = "Binance Pay" if call.data == "dep_binance" else ("USDT (BEP-20)" if call.data == "dep_usdt" else "UPI")
    address_val = BINANCE_PAY_ID if call.data == "dep_binance" else (USDT_BEP20_ADDRESS if call.data == "dep_usdt" else UPI_ID)

    await state.update_data(chosen_method=method_name)
    await state.set_state(UserState.deposit_amount)

    text = (
        f'<tg-emoji emoji-id="5445353829304387411">💳</tg-emoji> <b>Deposit via {method_name}</b>\n\n'
        f'<tg-emoji emoji-id="5902449142575141204">📌</tg-emoji> <b>Address / ID:</b>\n'
        f'<code>{address_val}</code>\n\n'
        f'👉 <b>Step 1:</b> Enter the deposit amount in <b>USD ($)</b>:'
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="Copy Address / ID", copy_text=CopyTextButton(text=address_val), icon_custom_emoji_id="5271604874419647061")
    kb.button(text="Cancel", callback_data="menu_back", icon_custom_emoji_id="5352759161945867747")
    kb.adjust(1)
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@dp.message(UserState.deposit_amount, F.text, ~F.text.startswith("/"))
async def process_deposit_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace("$", ""))
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await message.answer('<tg-emoji emoji-id="5274099962655816924">❌</tg-emoji> Invalid amount. Enter a positive number (e.g., <code>5</code> or <code>10.5</code>):', parse_mode=ParseMode.HTML)
        return

    await state.update_data(deposit_amount=amount)
    await state.set_state(UserState.deposit_proof)
    data = await state.get_data()
    method = data.get("chosen_method")

    await message.answer(
        f'<tg-emoji emoji-id="5206607081334906820">📸</tg-emoji> <b>Step 2: Upload Screenshot Proof</b>\n\n'
        f'• <b>Method:</b> {method}\n'
        f'• <b>Amount:</b> ${amount:.2f} (~₹{amount * USD_TO_INR:.2f})\n\n'
        f'Please send your transaction screenshot now:',
        parse_mode=ParseMode.HTML
    )

@dp.message(UserState.deposit_proof, F.photo)
async def process_deposit_proof(message: Message, state: FSMContext):
    data = await state.get_data()
    method = data.get("chosen_method")
    amount = data.get("deposit_amount")
    user_id = message.from_user.id
    username = f"@{message.from_user.username}" if message.from_user.username else f"ID: {user_id}"

    async with db_pool.acquire() as conn:
        deposit_id = await conn.fetchval(
            "INSERT INTO deposits (user_id, method, amount, status) VALUES ($1, $2, $3, 'pending') RETURNING id",
            user_id, method, amount
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Approve", callback_data=f"dep_app:{deposit_id}", icon_custom_emoji_id="6217663806110175239", style="success"),
        InlineKeyboardButton(text="Decline", callback_data=f"dep_dec:{deposit_id}", icon_custom_emoji_id="5274099962655816924", style="danger")
    ]])

    admin_caption = (
        f'<tg-emoji emoji-id="5445353829304387411">📥</tg-emoji> <b>New Deposit Request #{deposit_id}</b>\n\n'
        f'<tg-emoji emoji-id="5870458774455587120">👤</tg-emoji> <b>User:</b> {username} (<code>{user_id}</code>)\n'
        f'💳 <b>Method:</b> {method}\n'
        f'<tg-emoji emoji-id="5417924076503062111">💰</tg-emoji> <b>Amount:</b> ${amount:.2f} (~₹{amount * USD_TO_INR:.2f})\n'
        f'📅 <b>Time:</b> {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}'
    )

    await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id, caption=admin_caption, reply_markup=kb, parse_mode=ParseMode.HTML)
    await message.answer(
        f'<tg-emoji emoji-id="6217663806110175239">✅</tg-emoji> <b>Proof submitted successfully! (Request #{deposit_id})</b>\n\n'
        f'Your deposit will be verified by the admin and added to your balance shortly.',
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_menu_keyboard()
    )
    await state.clear()

@dp.callback_query(F.data.startswith("dep_app:"))
async def cb_admin_approve_deposit(call: CallbackQuery):
    await call.answer()
    deposit_id = int(call.data.split(":")[1])
    async with db_pool.acquire() as conn:
        dep = await conn.fetchrow("SELECT user_id, amount, status FROM deposits WHERE id=$1", deposit_id)
        if not dep or dep['status'] != 'pending':
            await call.answer("⚠️ Already processed.", show_alert=True)
            return

        user_id = dep['user_id']
        amount = dep['amount']

        async with conn.transaction():
            await conn.execute("UPDATE deposits SET status='approved' WHERE id=$1", deposit_id)
            await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id=$2", amount, user_id)
            await conn.execute("INSERT INTO transactions (user_id, type, amount, note) VALUES ($1, 'deposit', $2, $3)", user_id, amount, f"Deposit #{deposit_id} Approved")

    new_caption = (call.message.caption or "") + "\n\n<tg-emoji emoji-id=\"6217663806110175239\">✅</tg-emoji> <b>APPROVED BY ADMIN</b>"
    try:
        await call.message.edit_caption(caption=new_caption, reply_markup=None, parse_mode=ParseMode.HTML)
    except Exception:
        pass

    try:
        await bot.send_message(user_id, f'<tg-emoji emoji-id="6217663806110175239">🎉</tg-emoji> <b>Deposit Approved!</b>\n\n+${amount:.2f} has been added to your balance.', parse_mode=ParseMode.HTML)
    except Exception:
        pass

@dp.callback_query(F.data.startswith("dep_dec:"))
async def cb_admin_decline_deposit(call: CallbackQuery):
    await call.answer()
    deposit_id = int(call.data.split(":")[1])
    async with db_pool.acquire() as conn:
        dep = await conn.fetchrow("SELECT user_id, amount, status FROM deposits WHERE id=$1", deposit_id)
        if not dep or dep['status'] != 'pending':
            await call.answer("⚠️ Already processed.", show_alert=True)
            return
        await conn.execute("UPDATE deposits SET status='declined' WHERE id=$1", deposit_id)

    new_caption = (call.message.caption or "") + "\n\n<tg-emoji emoji-id=\"5274099962655816924\">❌</tg-emoji> <b>DECLINED BY ADMIN</b>"
    try:
        await call.message.edit_caption(caption=new_caption, reply_markup=None, parse_mode=ParseMode.HTML)
    except Exception:
        pass

    try:
        await bot.send_message(dep['user_id'], f'<tg-emoji emoji-id="5274099962655816924">❌</tg-emoji> <b>Deposit Declined.</b>\nYour deposit request #{deposit_id} was rejected.', parse_mode=ParseMode.HTML)
    except Exception:
        pass

# ============================================
# SUPPORT SYSTEM
# ============================================

@dp.callback_query(F.data == "menu_support")
async def cb_support(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await state.set_state(UserState.support_message)
    text = (
        f'<tg-emoji emoji-id="5274099962655816924">🛠</tg-emoji> <b>Support Center & Warranty Claims</b>\n\n'
        f'Send your inquiry or replacement claim below. If claiming warranty, include your <b>Order #</b>.'
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="Cancel", callback_data="menu_back", icon_custom_emoji_id="5352759161945867747")
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@dp.message(UserState.support_message, ~F.text.startswith("/"))
async def process_user_support_msg(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = f"@{message.from_user.username}" if message.from_user.username else f"ID: {user_id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Reply User", callback_data=f"sr:{user_id}", icon_custom_emoji_id="5870458774455587120", style="primary")
    ]])
    header = f'<tg-emoji emoji-id="5274099962655816924">🛠</tg-emoji> <b>New Support Request</b>\nUser: {username} (<code>{user_id}</code>)\n\n'
    if message.photo:
        await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id, caption=header + (message.caption or ""), reply_markup=kb, parse_mode=ParseMode.HTML)
    else:
        await bot.send_message(ADMIN_ID, header + message.text, reply_markup=kb, parse_mode=ParseMode.HTML)

    await message.answer('<tg-emoji emoji-id="6217663806110175239">✅</tg-emoji> <b>Message delivered to support.</b> We will get back to you shortly.', parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())
    await state.clear()

@dp.callback_query(F.data.startswith("sr:"))
async def cb_support_reply(call: CallbackQuery, state: FSMContext):
    await call.answer()
    target_id = int(call.data.split(":")[1])
    await state.set_state(AdminState.waiting_for_support_reply)
    await state.update_data(target_user=target_id)
    await call.message.answer(f'<tg-emoji emoji-id="5870458774455587120">✉️</tg-emoji> Send your reply message to User <code>{target_id}</code>:', parse_mode=ParseMode.HTML)

@dp.message(AdminState.waiting_for_support_reply, ~F.text.startswith("/"))
async def process_admin_support_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("target_user")
    reply_text = f'<tg-emoji emoji-id="5274099962655816924">🛠</tg-emoji> <b>Support Reply:</b>\n\n{message.text}'
    try:
        await bot.send_message(target_id, reply_text, parse_mode=ParseMode.HTML)
        await message.answer('<tg-emoji emoji-id="6217663806110175239">✅</tg-emoji> Reply delivered successfully.', reply_markup=get_admin_menu_keyboard())
    except Exception as e:
        await message.answer(f'<tg-emoji emoji-id="5274099962655816924">❌</tg-emoji> Delivery failed: {e}', reply_markup=get_admin_menu_keyboard())
    await state.clear()

# ============================================
# FULL ADMIN CONTROL PANEL & COMMANDS
# ============================================

@dp.message(Command("adminpanel"), StateFilter("*"))
@dp.message(Command("admin"), StateFilter("*"))
async def open_admin_panel(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    await message.answer('<tg-emoji emoji-id="5893161718179173515">🛠</tg-emoji> <b>Store Admin Control Panel</b>\n\nChoose an action below:', parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_keyboard())

# --- TRANSACTIONS HANDLER (FIXED) ---
@dp.message(F.text == "💳 Transactions", StateFilter("*"))
async def admin_btn_transactions(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminState.waiting_for_user_transactions)
    await message.answer('<tg-emoji emoji-id="5440410042773824003">💳</tg-emoji> Send the <b>User ID</b> to check their transaction records:', parse_mode=ParseMode.HTML)

@dp.message(AdminState.waiting_for_user_transactions, ~F.text.in_(MENU_BUTTONS))
async def process_user_transactions_step(message: Message, state: FSMContext):
    try:
        target_id = int(message.text.strip())
        text, reply_markup = await render_transaction_history_page(target_id, page=1, is_admin=True)
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    except ValueError:
        await message.answer('<tg-emoji emoji-id="5274099962655816924">❌</tg-emoji> Invalid numeric User ID.', reply_markup=get_admin_menu_keyboard())
    await state.clear()

@dp.callback_query(F.data.startswith("adm_tx_page:"))
async def cb_admin_tx_page(call: CallbackQuery):
    await call.answer()
    parts = call.data.split(":")
    target_user_id = int(parts[1])
    page = int(parts[2])
    text, reply_markup = await render_transaction_history_page(target_user_id, page=page, is_admin=True)
    try:
        await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    except Exception:
        pass

@dp.message(F.text == "➕ Add Stock", StateFilter("*"))
async def admin_add_stock(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="Add NEW Gmails", callback_data="admin_stock_type:new", icon_custom_emoji_id="5870458774455587120", style="success")
    kb.button(text="Add OLD Gmails", callback_data="admin_stock_type:old", icon_custom_emoji_id="5206607081334906820", style="primary")
    kb.adjust(2)
    await message.answer("Select which inventory category you want to add stock to:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("admin_stock_type:"))
async def cb_admin_stock_type(call: CallbackQuery, state: FSMContext):
    await call.answer()
    acc_type = call.data.split(":")[1]
    await state.update_data(stock_category=acc_type)
    await state.set_state(AdminState.waiting_for_bulk_accounts)
    await call.message.answer(
        f'<tg-emoji emoji-id="5445221832074483553">📦</tg-emoji> <b>Add Stock for {acc_type.upper()} Gmails</b>\n\n'
        f'Send accounts line by line (format: <code>email:password</code> or <code>email:password:recovery</code>):',
        parse_mode=ParseMode.HTML
    )

@dp.message(AdminState.waiting_for_bulk_accounts, ~F.text.in_(MENU_BUTTONS))
async def process_admin_bulk_stock(message: Message, state: FSMContext):
    data = await state.get_data()
    acc_type = data.get("stock_category", "new")
    lines = [l.strip() for l in message.text.strip().split("\n") if l.strip()]

    if not lines:
        await message.answer('<tg-emoji emoji-id="5274099962655816924">❌</tg-emoji> No valid lines found.', reply_markup=get_admin_menu_keyboard())
        await state.clear()
        return

    async with db_pool.acquire() as conn:
        for item in lines:
            await conn.execute("INSERT INTO inventory (account_type, credentials, status) VALUES ($1, $2, 'available')", acc_type, item)

    await message.answer(f'<tg-emoji emoji-id="6217663806110175239">✅</tg-emoji> <b>Successfully added {len(lines)} {acc_type.upper()} Gmail account(s) to inventory!</b>', parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_keyboard())
    await state.clear()

@dp.message(F.text == "📦 View Inventory", StateFilter("*"))
async def admin_view_inv(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    new_s, old_s = await get_stock_counts()
    async with db_pool.acquire() as conn:
        total_sold = await conn.fetchval("SELECT COUNT(*) FROM orders") or 0
    text = (
        f'<tg-emoji emoji-id="5445221832074483553">📦</tg-emoji> <b>Current Inventory:</b>\n\n'
        f'<tg-emoji emoji-id="6217663806110175239">🟢</tg-emoji> <b>Available New Gmails:</b> {new_s}\n'
        f'<tg-emoji emoji-id="6217663806110175239">🟢</tg-emoji> <b>Available Old Gmails:</b> {old_s}\n'
        f'<tg-emoji emoji-id="5377548235709619284">🛒</tg-emoji> <b>Total Accounts Sold:</b> {total_sold}'
    )
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "📥 Pending Deposits", StateFilter("*"))
async def admin_view_pending_deposits(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, user_id, method, amount, created_at FROM deposits WHERE status='pending' ORDER BY id ASC")

    if not rows:
        await message.answer("📭 No pending deposits found.")
        return

    await message.answer(f'<tg-emoji emoji-id="5445353829304387411">📥</tg-emoji> <b>{len(rows)} Pending Deposit Request(s):</b>', parse_mode=ParseMode.HTML)
    for r in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Approve", callback_data=f"dep_app:{r['id']}", icon_custom_emoji_id="6217663806110175239", style="success"),
            InlineKeyboardButton(text="Decline", callback_data=f"dep_dec:{r['id']}", icon_custom_emoji_id="5274099962655816924", style="danger")
        ]])
        await message.answer(
            f'<tg-emoji emoji-id="5197269100878907942">🆔</tg-emoji> <b>Deposit #{r["id"]}</b>\n'
            f'👤 User: <code>{r["user_id"]}</code>\n'
            f'💳 Method: {r["method"]}\n'
            f'💰 Amount: ${r["amount"]:.2f}\n'
            f'📅 Date: {r["created_at"].strftime("%Y-%m-%d %H:%M")}',
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )

@dp.message(F.text == "💰 Add Balance", StateFilter("*"))
async def admin_add_balance_prompt(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminState.waiting_for_add_balance)
    await message.answer('<tg-emoji emoji-id="5417924076503062111">💰</tg-emoji> Send the User ID and Amount in USD separated by space:\n\n<i>Example: 123456789 10.5</i>', parse_mode=ParseMode.HTML)

@dp.message(AdminState.waiting_for_add_balance, ~F.text.in_(MENU_BUTTONS))
async def process_admin_add_balance(message: Message, state: FSMContext):
    try:
        parts = message.text.strip().split()
        target_id = int(parts[0])
        amount = float(parts[1])
        await ensure_user(target_id)
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id=$2", amount, target_id)
                await conn.execute("INSERT INTO transactions (user_id, type, amount, note) VALUES ($1, 'admin_add', $2, 'Credited by admin')", target_id, amount)

        await message.answer(f'<tg-emoji emoji-id="6217663806110175239">✅</tg-emoji> Credited ${amount:.2f} to User <code>{target_id}</code>.', parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_keyboard())
        try:
            await bot.send_message(target_id, f'<tg-emoji emoji-id="5417924076503062111">💰</tg-emoji> <b>Admin added ${amount:.2f} to your bot balance!</b>', parse_mode=ParseMode.HTML)
        except Exception:
            pass
    except Exception as e:
        await message.answer(f'<tg-emoji emoji-id="5274099962655816924">❌</tg-emoji> Error: {e}', reply_markup=get_admin_menu_keyboard())
    await state.clear()

@dp.message(F.text == "➖ Cut Balance", StateFilter("*"))
async def admin_cut_balance_prompt(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminState.waiting_for_cut_balance)
    await message.answer('<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji> Send the User ID and Amount in USD to deduct:\n\n<i>Example: 123456789 5.0</i>', parse_mode=ParseMode.HTML)

@dp.message(AdminState.waiting_for_cut_balance, ~F.text.in_(MENU_BUTTONS))
async def process_admin_cut_balance(message: Message, state: FSMContext):
    try:
        parts = message.text.strip().split()
        target_id = int(parts[0])
        amount = float(parts[1])
        await ensure_user(target_id)
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE users SET balance = GREATEST(0.0, balance - $1) WHERE user_id=$2", amount, target_id)
                await conn.execute("INSERT INTO transactions (user_id, type, amount, note) VALUES ($1, 'admin_cut', $2, 'Deducted by admin')", target_id, -amount)

        await message.answer(f'<tg-emoji emoji-id="6217663806110175239">✅</tg-emoji> Deducted ${amount:.2f} from User <code>{target_id}</code>.', parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_keyboard())
    except Exception as e:
        await message.answer(f'<tg-emoji emoji-id="5274099962655816924">❌</tg-emoji> Error: {e}', reply_markup=get_admin_menu_keyboard())
    await state.clear()

@dp.message(F.text == "🔎 Check Balance", StateFilter("*"))
async def admin_check_bal_prompt(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminState.waiting_for_check_balance)
    await message.answer('<tg-emoji emoji-id="5870458774455587120">🔎</tg-emoji> Send numeric User ID to inspect:', parse_mode=ParseMode.HTML)

@dp.message(AdminState.waiting_for_check_balance, ~F.text.in_(MENU_BUTTONS))
async def process_admin_check_balance(message: Message, state: FSMContext):
    try:
        target_id = int(message.text.strip())
        async with db_pool.acquire() as conn:
            u = await conn.fetchrow("SELECT user_id, username, balance, created_at FROM users WHERE user_id=$1", target_id)
        if not u:
            await message.answer("📭 User not found.", reply_markup=get_admin_menu_keyboard())
        else:
            await message.answer(
                f'<tg-emoji emoji-id="5870458774455587120">👤</tg-emoji> <b>User Info:</b>\n'
                f'• ID: <code>{u["user_id"]}</code>\n'
                f'• Username: @{u["username"] or "None"}\n'
                f'• Balance: <b>${u["balance"]:.2f}</b> (~₹{u["balance"] * USD_TO_INR:.2f})\n'
                f'• Joined: {u["created_at"].strftime("%Y-%m-%d")}',
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_menu_keyboard()
            )
    except Exception as e:
        await message.answer(f'<tg-emoji emoji-id="5274099962655816924">❌</tg-emoji> Error: {e}', reply_markup=get_admin_menu_keyboard())
    await state.clear()

@dp.message(F.text == "🏆 Top Balances", StateFilter("*"))
async def admin_top_balances(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id, username, balance FROM users ORDER BY balance DESC LIMIT 10")
    if not rows:
        await message.answer("📭 No users found.")
        return
    text = '<tg-emoji emoji-id="5417924076503062111">🏆</tg-emoji> <b>Top 10 Balances:</b>\n\n'
    for i, r in enumerate(rows, 1):
        uname = f"@{r['username']}" if r['username'] else f"<code>{r['user_id']}</code>"
        text += f"{i}. {uname} — <b>${r['balance']:.2f}</b>\n"
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "🚫 Ban User", StateFilter("*"))
async def admin_ban_prompt(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminState.waiting_for_ban_user)
    await message.answer('<tg-emoji emoji-id="5274099962655816924">🚫</tg-emoji> Send numeric User ID to ban:', parse_mode=ParseMode.HTML)

@dp.message(AdminState.waiting_for_ban_user, ~F.text.in_(MENU_BUTTONS))
async def process_admin_ban_user(message: Message, state: FSMContext):
    try:
        target_id = int(message.text.strip())
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO banned_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", target_id)
        BANNED_USERS_CACHE.add(target_id)
        await message.answer(f'<tg-emoji emoji-id="5274099962655816924">🚫</tg-emoji> User <code>{target_id}</code> banned successfully.', parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_keyboard())
    except Exception as e:
        await message.answer(f'<tg-emoji emoji-id="5274099962655816924">❌</tg-emoji> Error: {e}', reply_markup=get_admin_menu_keyboard())
    await state.clear()

@dp.message(F.text == "✅ Unban User", StateFilter("*"))
async def admin_unban_prompt(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminState.waiting_for_unban_user)
    await message.answer('<tg-emoji emoji-id="6217663806110175239">✅</tg-emoji> Send numeric User ID to unban:', parse_mode=ParseMode.HTML)

@dp.message(AdminState.waiting_for_unban_user, ~F.text.in_(MENU_BUTTONS))
async def process_admin_unban_user(message: Message, state: FSMContext):
    try:
        target_id = int(message.text.strip())
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM banned_users WHERE user_id=$1", target_id)
        BANNED_USERS_CACHE.discard(target_id)
        await message.answer(f'<tg-emoji emoji-id="6217663806110175239">✅</tg-emoji> User <code>{target_id}</code> unbanned successfully.', parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_keyboard())
    except Exception as e:
        await message.answer(f'<tg-emoji emoji-id="5274099962655816924">❌</tg-emoji> Error: {e}', reply_markup=get_admin_menu_keyboard())
    await state.clear()

@dp.message(F.text == "📢 Broadcast", StateFilter("*"))
async def admin_broadcast_prompt(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminState.waiting_for_broadcast)
    await message.answer('<tg-emoji emoji-id="5206607081334906820">📢</tg-emoji> Send or forward the message you want to broadcast to all users:', parse_mode=ParseMode.HTML)

@dp.message(AdminState.waiting_for_broadcast, ~F.text.in_(MENU_BUTTONS))
async def process_admin_broadcast(message: Message, state: FSMContext):
    async with db_pool.acquire() as conn:
        users = await conn.fetch("SELECT user_id FROM users")

    if not users:
        await message.answer("📭 No users found.", reply_markup=get_admin_menu_keyboard())
        await state.clear()
        return

    status_msg = await message.answer(f"⏳ Broadcasting to {len(users)} users...")
    sent, failed = 0, 0
    for idx, u in enumerate(users, 1):
        try:
            await bot.copy_message(chat_id=u['user_id'], from_chat_id=message.chat.id, message_id=message.message_id)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.04)

    await status_msg.edit_text(f'<tg-emoji emoji-id="6217663806110175239">✅</tg-emoji> <b>Broadcast Completed!</b>\n\n🟢 Sent: {sent}\n🔴 Failed: {failed}', parse_mode=ParseMode.HTML)
    await state.clear()

# --- CHANGE VALUES MENU & ACTIONS (UPDATED) ---
@dp.message(F.text == "⚙️ Change Values", StateFilter("*"))
async def admin_change_values_menu(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    text = (
        f'<tg-emoji emoji-id="5893161718179173515">⚙️</tg-emoji> <b>Current System Values & Deposit Info:</b>\n\n'
        f'• <b>New Gmail Price:</b> ${PRICE_NEW_GMAIL:.2f}\n'
        f'• <b>Old Gmail Price:</b> ${PRICE_OLD_GMAIL:.2f}\n'
        f'• <b>Warranty Duration:</b> {WARRANTY_DAYS} Days\n'
        f'• <b>Binance Pay ID:</b> <code>{BINANCE_PAY_ID}</code>\n'
        f'• <b>USDT Address:</b> <code>{USDT_BEP20_ADDRESS}</code>\n'
        f'• <b>UPI ID:</b> <code>{UPI_ID}</code>\n\n'
        f'Select a value to update:'
    )
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=get_change_values_inline_keyboard())

@dp.callback_query(F.data.startswith("ch_val:"))
async def cb_change_val_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    action = call.data.split(":")[1]
    if action == "new_price":
        await state.set_state(AdminState.waiting_for_new_price)
        await call.message.answer("Send new price in USD for <b>NEW Gmail</b> (e.g. <code>0.35</code>):", parse_mode=ParseMode.HTML)
    elif action == "old_price":
        await state.set_state(AdminState.waiting_for_old_price)
        await call.message.answer("Send new price in USD for <b>OLD Gmail</b> (e.g. <code>0.45</code>):", parse_mode=ParseMode.HTML)
    elif action == "warranty":
        await state.set_state(AdminState.waiting_for_warranty_days)
        await call.message.answer("Send new warranty duration in <b>days</b> (e.g. <code>7</code>):", parse_mode=ParseMode.HTML)
    elif action == "binance_id":
        await state.set_state(AdminState.waiting_for_binance_id)
        await call.message.answer("Send the new <b>Binance Pay ID</b>:", parse_mode=ParseMode.HTML)
    elif action == "usdt_addr":
        await state.set_state(AdminState.waiting_for_usdt_address)
        await call.message.answer("Send the new <b>USDT (BEP-20) Address</b>:", parse_mode=ParseMode.HTML)
    elif action == "upi_id":
        await state.set_state(AdminState.waiting_for_upi_id)
        await call.message.answer("Send the new <b>UPI ID</b> (e.g. <code>name@upi</code>):", parse_mode=ParseMode.HTML)

@dp.message(AdminState.waiting_for_new_price, ~F.text.in_(MENU_BUTTONS))
async def process_change_new_price(message: Message, state: FSMContext):
    global PRICE_NEW_GMAIL
    try:
        val = float(message.text.strip().replace("$", ""))
        PRICE_NEW_GMAIL = val
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO bot_settings (key, value) VALUES ('price_new_gmail', $1) ON CONFLICT (key) DO UPDATE SET value = $1", str(val))
        await message.answer(f'<tg-emoji emoji-id="6217663806110175239">✅</tg-emoji> New Gmail price set to <b>${val:.2f}</b>.', parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_keyboard())
    except Exception as e:
        await message.answer(f'<tg-emoji emoji-id="5274099962655816924">❌</tg-emoji> Error: {e}', reply_markup=get_admin_menu_keyboard())
    await state.clear()

@dp.message(AdminState.waiting_for_old_price, ~F.text.in_(MENU_BUTTONS))
async def process_change_old_price(message: Message, state: FSMContext):
    global PRICE_OLD_GMAIL
    try:
        val = float(message.text.strip().replace("$", ""))
        PRICE_OLD_GMAIL = val
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO bot_settings (key, value) VALUES ('price_old_gmail', $1) ON CONFLICT (key) DO UPDATE SET value = $1", str(val))
        await message.answer(f'<tg-emoji emoji-id="6217663806110175239">✅</tg-emoji> Old Gmail price set to <b>${val:.2f}</b>.', parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_keyboard())
    except Exception as e:
        await message.answer(f'<tg-emoji emoji-id="5274099962655816924">❌</tg-emoji> Error: {e}', reply_markup=get_admin_menu_keyboard())
    await state.clear()

@dp.message(AdminState.waiting_for_warranty_days, ~F.text.in_(MENU_BUTTONS))
async def process_change_warranty(message: Message, state: FSMContext):
    global WARRANTY_DAYS
    try:
        val = int(message.text.strip())
        WARRANTY_DAYS = val
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO bot_settings (key, value) VALUES ('warranty_days', $1) ON CONFLICT (key) DO UPDATE SET value = $1", str(val))
        await message.answer(f'<tg-emoji emoji-id="6217663806110175239">✅</tg-emoji> Warranty duration set to <b>{val} Days</b>.', parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_keyboard())
    except Exception as e:
        await message.answer(f'<tg-emoji emoji-id="5274099962655816924">❌</tg-emoji> Error: {e}', reply_markup=get_admin_menu_keyboard())
    await state.clear()

@dp.message(AdminState.waiting_for_binance_id, ~F.text.in_(MENU_BUTTONS))
async def process_change_binance_id(message: Message, state: FSMContext):
    global BINANCE_PAY_ID
    new_val = message.text.strip()
    BINANCE_PAY_ID = new_val
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO bot_settings (key, value) VALUES ('binance_pay_id', $1) ON CONFLICT (key) DO UPDATE SET value = $1", new_val)
    await message.answer(f'<tg-emoji emoji-id="6217663806110175239">✅</tg-emoji> Binance Pay ID updated to: <code>{new_val}</code>', parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_keyboard())
    await state.clear()

@dp.message(AdminState.waiting_for_usdt_address, ~F.text.in_(MENU_BUTTONS))
async def process_change_usdt_addr(message: Message, state: FSMContext):
    global USDT_BEP20_ADDRESS
    new_val = message.text.strip()
    USDT_BEP20_ADDRESS = new_val
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO bot_settings (key, value) VALUES ('usdt_bep20_address', $1) ON CONFLICT (key) DO UPDATE SET value = $1", new_val)
    await message.answer(f'<tg-emoji emoji-id="6217663806110175239">✅</tg-emoji> USDT (BEP-20) Address updated to: <code>{new_val}</code>', parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_keyboard())
    await state.clear()

@dp.message(AdminState.waiting_for_upi_id, ~F.text.in_(MENU_BUTTONS))
async def process_change_upi_id(message: Message, state: FSMContext):
    global UPI_ID
    new_val = message.text.strip()
    UPI_ID = new_val
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO bot_settings (key, value) VALUES ('upi_id', $1) ON CONFLICT (key) DO UPDATE SET value = $1", new_val)
    await message.answer(f'<tg-emoji emoji-id="6217663806110175239">✅</tg-emoji> UPI ID updated to: <code>{new_val}</code>', parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_keyboard())
    await state.clear()

@dp.message(F.text == "🔍 Find ID", StateFilter("*"))
async def admin_find_id_prompt(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminState.waiting_for_find_id_query)
    await message.answer('<tg-emoji emoji-id="5870458774455587120">🔍</tg-emoji> Send search keyword (email, User ID, or Order ID):', parse_mode=ParseMode.HTML)

@dp.message(AdminState.waiting_for_find_id_query, ~F.text.in_(MENU_BUTTONS))
async def process_admin_find_id(message: Message, state: FSMContext):
    query = message.text.strip().lower()
    async with db_pool.acquire() as conn:
        inv_item = await conn.fetchrow("SELECT * FROM inventory WHERE LOWER(credentials) LIKE $1 LIMIT 1", f"%{query}%")
        order_item = await conn.fetchrow("SELECT * FROM orders WHERE LOWER(credentials) LIKE $1 OR CAST(id AS TEXT) = $2 LIMIT 1", f"%{query}%", query)

    if not inv_item and not order_item:
        await message.answer(f"📭 No records found matching <code>{query}</code>.", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_keyboard())
    else:
        text = f"🔍 <b>Results for:</b> <code>{query}</code>\n\n"
        if order_item:
            text += f'<tg-emoji emoji-id="5445221832074483553">📦</tg-emoji> <b>Order #{order_item["id"]}</b>:\nUser: <code>{order_item["user_id"]}</code>\nCreds: <code>{order_item["credentials"]}</code>\nDate: {order_item["created_at"]}\n\n'
        if inv_item:
            text += f'<tg-emoji emoji-id="5445221832074483553">🏷</tg-emoji> <b>Stock Item #{inv_item["id"]}</b>:\nType: {inv_item["account_type"]}\nStatus: {inv_item["status"]}\nCreds: <code>{inv_item["credentials"]}</code>'
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_keyboard())
    await state.clear()

@dp.message(F.text == "📊 View Stats", StateFilter("*"))
async def admin_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with db_pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users") or 0
        total_orders = await conn.fetchval("SELECT COUNT(*) FROM orders") or 0
        total_deposits = await conn.fetchval("SELECT SUM(amount) FROM deposits WHERE status='approved'") or 0.0
        pending_deposits = await conn.fetchval("SELECT COUNT(*) FROM deposits WHERE status='pending'") or 0
        new_s, old_s = await get_stock_counts()

    text = (
        f'<tg-emoji emoji-id="5440410042773824003">📊</tg-emoji> <b>Store Statistics:</b>\n\n'
        f'<tg-emoji emoji-id="5870458774455587120">👥</tg-emoji> <b>Total Users:</b> <code>{total_users}</code>\n'
        f'<tg-emoji emoji-id="5445221832074483553">📦</tg-emoji> <b>Total Sold Orders:</b> <code>{total_orders}</code>\n'
        f'<tg-emoji emoji-id="5417924076503062111">💰</tg-emoji> <b>Total Approved Deposits:</b> <b>${total_deposits:.2f}</b>\n'
        f'<tg-emoji emoji-id="5445353829304387411">⏳</tg-emoji> <b>Pending Deposit Requests:</b> <code>{pending_deposits}</code>\n'
        f'<tg-emoji emoji-id="6217663806110175239">🟢</tg-emoji> <b>Available Stock:</b> New: {new_s} | Old: {old_s}'
    )
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text.in_({"🔴 Bot Status: OFF", "🟢 Bot Status: ON"}), StateFilter("*"))
async def admin_toggle_bot_status(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    global BOT_STATUS
    BOT_STATUS = not BOT_STATUS
    new_val = 'on' if BOT_STATUS else 'off'
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO bot_settings (key, value) VALUES ('bot_status', $1) ON CONFLICT (key) DO UPDATE SET value = $1", new_val)

    status_str = '<tg-emoji emoji-id="6217663806110175239">🟢</tg-emoji> <b>Bot is now ONLINE for all users!</b>' if BOT_STATUS else '<tg-emoji emoji-id="5274099962655816924">🔴</tg-emoji> <b>Bot is now OFF and disabled for users!</b>'
    await message.answer(status_str, parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_keyboard())

@dp.message(F.text == "🏠 Main Menu", StateFilter("*"))
async def return_to_main_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🏠 Returned to User Main Menu.", reply_markup=get_main_menu_keyboard())

# ============================================
# RUNNER
# ============================================

async def main():
    await init_db()
    await load_settings_and_cache()
    
    server_thread = Thread(target=run_flask)
    server_thread.daemon = True
    server_thread.start()
    
    print("🤖 Gmail Store Bot & Admin Control Panel running 24/7 on Render...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
