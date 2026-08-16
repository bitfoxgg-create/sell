import asyncio
from datetime import datetime, timedelta
import os
from threading import Thread
import urllib.parse
import re
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

# Fixed Prices in USD
PRICE_NEW_GMAIL = 0.35
PRICE_OLD_GMAIL = 0.45

# Deposit Payment Details
BINANCE_PAY_ID = "1230141397"
USDT_BEP20_ADDRESS = "0xFbaE715FeFAf06fdD6b203a769685DD25C18678C"
UPI_ID = "adarsh--hacker@fam"

# Currency Conversion (1 USD = 96.30 INR for display/deposit convenience)
USD_TO_INR = 96.30

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db_pool = None
USER_CACHE = {}

# ============================================
# DUMMY FLASK SERVER FOR RENDER KEEP-ALIVE
# ============================================

flask_app = Flask('')

@flask_app.route('/')
def home():
    return "Gmail Store Bot is active!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

# ============================================
# FSM STATES
# ============================================

class UserState(StatesGroup):
    deposit_method = State()
    deposit_amount = State()
    deposit_proof = State()
    support_message = State()

class AdminState(StatesGroup):
    add_stock_type = State()
    add_stock_bulk = State()
    add_balance_user = State()
    cut_balance_user = State()
    broadcast_message = State()
    support_reply = State()

# ============================================
# DATABASE INITIALIZATION
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
        
        # Stock Table (New vs Old Gmails)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS inventory (
                id SERIAL PRIMARY KEY,
                account_type TEXT, -- 'new' or 'old'
                credentials TEXT,  -- email:pass or details
                status TEXT DEFAULT 'available', -- 'available', 'sold'
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Orders Table with 7-Day Warranty Expiry
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
                status TEXT DEFAULT 'pending', -- 'pending', 'approved', 'declined'
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

# ============================================
# HELPERS & USER DATA
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

# ============================================
# KEYBOARDS
# ============================================

def get_main_menu_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="🛒 Buy Gmail", callback_data="menu_buy")
    kb.button(text="💳 Deposit Funds", callback_data="menu_deposit")
    kb.button(text="💰 Balance", callback_data="menu_balance")
    kb.button(text="📦 My Orders & Warranty", callback_data="menu_orders")
    kb.button(text="🛠 Support", callback_data="menu_support")
    kb.adjust(2, 2, 1)
    return kb.as_markup()

def get_buy_keyboard(new_stock: int, old_stock: int):
    kb = InlineKeyboardBuilder()
    kb.button(text=f"🆕 New Gmail (${PRICE_NEW_GMAIL:.2f}) [Stock: {new_stock}]", callback_data="buy_new")
    kb.button(text=f"🏛 Old Gmail (${PRICE_OLD_GMAIL:.2f}) [Stock: {old_stock}]", callback_data="buy_old")
    kb.button(text="⬅️ Back", callback_data="menu_back")
    kb.adjust(1)
    return kb.as_markup()

def get_deposit_methods_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="🟡 Binance ID", callback_data="dep_binance")
    kb.button(text="🪙 USDT (BEP-20)", callback_data="dep_usdt")
    kb.button(text="🏦 UPI (India)", callback_data="dep_upi")
    kb.button(text="⬅️ Back", callback_data="menu_back")
    kb.adjust(1)
    return kb.as_markup()

def get_admin_menu_keyboard():
    kb = ReplyKeyboardBuilder()
    kb.button(text="➕ Add Stock")
    kb.button(text="📦 View Inventory")
    kb.button(text="💰 Manage Balance")
    kb.button(text="📊 Bot Stats")
    kb.button(text="📢 Broadcast")
    kb.button(text="🏠 Main Menu")
    kb.adjust(2, 2, 2)
    return kb.as_markup(resize_keyboard=True)

# ============================================
# USER HANDLERS (START & STORE SYSTEM)
# ============================================

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await ensure_user(message.from_user.id, message.from_user.username)
    text = (
        f"👋 <b>Welcome to Gmail Store Bot!</b>\n\n"
        f"⚡ <b>Available Products & Services:</b>\n"
        f"• <b>New Gmail:</b> ${PRICE_NEW_GMAIL:.2f} / account\n"
        f"• <b>Old Gmail:</b> ${PRICE_OLD_GMAIL:.2f} / account\n"
        f"🛡 <b>Warranty:</b> 7 Days full replacement warranty on all purchases.\n\n"
        f"Please select an option below:"
    )
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())

@dp.callback_query(F.data == "menu_back")
async def cb_menu_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    text = (
        f"👋 <b>Welcome to Gmail Store Bot!</b>\n\n"
        f"⚡ <b>Available Products:</b>\n"
        f"• <b>New Gmail:</b> ${PRICE_NEW_GMAIL:.2f}\n"
        f"• <b>Old Gmail:</b> ${PRICE_OLD_GMAIL:.2f}\n"
        f"🛡 <b>Warranty:</b> 7 Days Replacement\n\n"
        f"Please select an option below:"
    )
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())

@dp.callback_query(F.data == "menu_balance")
async def cb_balance(call: CallbackQuery):
    await call.answer()
    bal = await get_user_balance(call.from_user.id)
    inr_bal = bal * USD_TO_INR
    text = (
        f"💰 <b>Your Account Balance:</b>\n\n"
        f"💵 <b>USD:</b> ${bal:.2f}\n"
        f"🇮🇳 <b>INR (approx):</b> ₹{inr_bal:.2f}\n\n"
        f"<i>Use the Deposit button to top up your balance.</i>"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Deposit Now", callback_data="menu_deposit")
    kb.button(text="⬅️ Back", callback_data="menu_back")
    kb.adjust(1)
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "menu_buy")
async def cb_buy_menu(call: CallbackQuery):
    await call.answer()
    new_stock, old_stock = await get_stock_counts()
    text = (
        f"🛒 <b>Choose Account Type to Purchase:</b>\n\n"
        f"1️⃣ <b>New Gmail:</b> ${PRICE_NEW_GMAIL:.2f} (Stock: {new_stock})\n"
        f"2️⃣ <b>Old Gmail:</b> ${PRICE_OLD_GMAIL:.2f} (Stock: {old_stock})\n\n"
        f"🛡 <i>Every account includes a strict 7-Day Guarantee.</i>"
    )
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=get_buy_keyboard(new_stock, old_stock))

@dp.callback_query(F.data.in_({"buy_new", "buy_old"}))
async def process_account_purchase(call: CallbackQuery):
    await call.answer()
    acc_type = "new" if call.data == "buy_new" else "old"
    price = PRICE_NEW_GMAIL if acc_type == "new" else PRICE_OLD_GMAIL
    user_id = call.from_user.id

    bal = await get_user_balance(user_id)
    if bal < price:
        await call.answer(f"❌ Insufficient balance! Price: ${price:.2f}, Your Balance: ${bal:.2f}", show_alert=True)
        return

    async with db_pool.acquire() as conn:
        # Atomic lock and fetch stock item
        async with conn.transaction():
            item = await conn.fetchrow(
                "SELECT id, credentials FROM inventory WHERE account_type=$1 AND status='available' ORDER BY id ASC LIMIT 1 FOR UPDATE",
                acc_type
            )
            if not item:
                await call.answer(f"❌ Sorry, {acc_type.upper()} Gmail is currently out of stock!", show_alert=True)
                return

            warranty_date = datetime.utcnow() + timedelta(days=7)
            # Deduct balance, mark stock as sold, record order
            await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id=$2", price, user_id)
            await conn.execute("UPDATE inventory SET status='sold' WHERE id=$1", item['id'])
            order_id = await conn.fetchval('''
                INSERT INTO orders (user_id, account_type, credentials, price, warranty_until)
                VALUES ($1, $2, $3, $4, $5) RETURNING id
            ''', user_id, acc_type, item['credentials'], price, warranty_date)

    text = (
        f"🎉 <b>Purchase Successful! Order #{order_id}</b>\n\n"
        f"📦 <b>Type:</b> {acc_type.upper()} Gmail\n"
        f"💵 <b>Price:</b> ${price:.2f}\n"
        f"🛡 <b>Warranty Active Until:</b> {warranty_date.strftime('%Y-%m-%d %H:%M:%S UTC')} (7 Days)\n\n"
        f"🔐 <b>Your Account Credentials:</b>\n"
        f"<code>{item['credentials']}</code>\n\n"
        f"<i>Please verify and secure the account. Contact support within 7 days if any login issues arise.</i>"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="🛒 Buy More", callback_data="menu_buy")
    kb.button(text="🏠 Main Menu", callback_data="menu_back")
    kb.adjust(2)
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "menu_orders")
async def cb_view_orders(call: CallbackQuery):
    await call.answer()
    user_id = call.from_user.id
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, account_type, credentials, warranty_until, created_at FROM orders WHERE user_id=$1 ORDER BY id DESC LIMIT 5", user_id)

    if not rows:
        text = "📦 <b>You have not purchased any accounts yet.</b>"
    else:
        text = "📦 <b>Your Recent Purchases:</b>\n\n"
        now = datetime.utcnow()
        for r in rows:
            warranty_left = r['warranty_until'] - now
            if warranty_left.total_seconds() > 0:
                days_left = warranty_left.days
                hours_left = int(warranty_left.seconds // 3600)
                warranty_status = f"🟢 Active ({days_left}d {hours_left}h left)"
            else:
                warranty_status = "🔴 Expired"

            text += (
                f"🆔 <b>Order #{r['id']}</b> ({r['account_type'].upper()})\n"
                f"🔑 <code>{r['credentials']}</code>\n"
                f"🛡 Warranty: {warranty_status}\n"
                f"📅 Date: {r['created_at'].strftime('%b %d, %Y')}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
            )

    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Back", callback_data="menu_back")
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

# ============================================
# DEPOSIT SYSTEM (BINANCE, USDT BEP-20, UPI)
# ============================================

@dp.callback_query(F.data == "menu_deposit")
async def cb_deposit_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    text = (
        f"💳 <b>Select a Deposit Method:</b>\n\n"
        f"1. <b>Binance Pay ID:</b> Fast Binance-to-Binance transfer\n"
        f"2. <b>USDT (BEP-20):</b> Crypto transfer on BSC network\n"
        f"3. <b>UPI ID:</b> Instant INR Payment (Exchange Rate: $1 = ₹{USD_TO_INR})\n\n"
        f"Select an option below to get payment details:"
    )
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=get_deposit_methods_keyboard())

@dp.callback_query(F.data.in_({"dep_binance", "dep_usdt", "dep_upi"}))
async def cb_select_deposit_method(call: CallbackQuery, state: FSMContext):
    await call.answer()
    method_key = call.data
    method_name = "Binance Pay" if method_key == "dep_binance" else ("USDT (BEP-20)" if method_key == "dep_usdt" else "UPI")
    address_val = BINANCE_PAY_ID if method_key == "dep_binance" else (USDT_BEP20_ADDRESS if method_key == "dep_usdt" else UPI_ID)

    await state.update_data(chosen_method=method_name)
    await state.set_state(UserState.deposit_amount)

    text = (
        f"💳 <b>Deposit via {method_name}</b>\n\n"
        f"📌 <b>Payment Details:</b>\n"
        f"<code>{address_val}</code>\n\n"
        f"👉 <b>Step 1:</b> Enter the amount in <b>USD ($)</b> you are depositing (e.g. <code>5.0</code> or <code>10</code>):"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="Copy Address/ID", copy_text=CopyTextButton(text=address_val))
    kb.button(text="⬅️ Cancel", callback_data="menu_back")
    kb.adjust(1)
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@dp.message(UserState.deposit_amount, F.text, ~F.text.startswith("/"))
async def process_deposit_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace("$", ""))
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("❌ Invalid amount. Please enter a valid number (e.g., <code>5</code> or <code>2.5</code>):", parse_mode=ParseMode.HTML)
        return

    await state.update_data(deposit_amount=amount)
    await state.set_state(UserState.deposit_proof)

    data = await state.get_data()
    method = data.get("chosen_method")
    approx_inr = amount * USD_TO_INR

    await message.answer(
        f"📸 <b>Step 2: Upload Payment Proof</b>\n\n"
        f"• <b>Method:</b> {method}\n"
        f"• <b>Amount:</b> ${amount:.2f} (~₹{approx_inr:.2f})\n\n"
        f"Please send the <b>transaction screenshot or receipt</b> now:",
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

    # Inline approval actions for admin
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Approve", callback_data=f"dep_app:{deposit_id}"),
        InlineKeyboardButton(text="Decline", callback_data=f"dep_dec:{deposit_id}")
    ]])

    admin_caption = (
        f"📥 <b>New Deposit Request #{deposit_id}</b>\n\n"
        f"👤 <b>User:</b> {username} (<code>{user_id}</code>)\n"
        f"💳 <b>Method:</b> {method}\n"
        f"💰 <b>Amount:</b> ${amount:.2f} (~₹{amount * USD_TO_INR:.2f})\n"
        f"📅 <b>Time:</b> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )

    await bot.send_photo(
        ADMIN_ID,
        photo=message.photo[-1].file_id,
        caption=admin_caption,
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

    await message.answer(
        f"✅ <b>Deposit receipt submitted! (Request #{deposit_id})</b>\n\n"
        f"The admin will verify your transaction and credit <b>${amount:.2f}</b> to your balance shortly.",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_menu_keyboard()
    )
    await state.clear()

# ============================================
# ADMIN DEPOSIT APPROVAL CALLBACKS
# ============================================

@dp.callback_query(F.data.startswith("dep_app:"))
async def cb_admin_approve_deposit(call: CallbackQuery):
    await call.answer()
    deposit_id = int(call.data.split(":")[1])

    async with db_pool.acquire() as conn:
        dep = await conn.fetchrow("SELECT user_id, amount, status FROM deposits WHERE id=$1", deposit_id)
        if not dep or dep['status'] != 'pending':
            await call.answer("⚠️ Deposit already processed.", show_alert=True)
            return

        user_id = dep['user_id']
        amount = dep['amount']

        async with conn.transaction():
            await conn.execute("UPDATE deposits SET status='approved' WHERE id=$1", deposit_id)
            await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id=$2", amount, user_id)

    new_caption = (call.message.caption or "") + "\n\n✅ <b>APPROVED BY ADMIN</b>"
    try:
        await call.message.edit_caption(caption=new_caption, reply_markup=None, parse_mode=ParseMode.HTML)
    except Exception:
        pass

    try:
        await bot.send_message(
            user_id,
            f"🎉 <b>Deposit Approved!</b>\n\n<b>${amount:.2f}</b> has been credited to your bot balance.",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

@dp.callback_query(F.data.startswith("dep_dec:"))
async def cb_admin_decline_deposit(call: CallbackQuery):
    await call.answer()
    deposit_id = int(call.data.split(":")[1])

    async with db_pool.acquire() as conn:
        dep = await conn.fetchrow("SELECT user_id, amount, status FROM deposits WHERE id=$1", deposit_id)
        if not dep or dep['status'] != 'pending':
            await call.answer("⚠️ Deposit already processed.", show_alert=True)
            return
        await conn.execute("UPDATE deposits SET status='declined' WHERE id=$1", deposit_id)

    new_caption = (call.message.caption or "") + "\n\n❌ <b>DECLINED BY ADMIN</b>"
    try:
        await call.message.edit_caption(caption=new_caption, reply_markup=None, parse_mode=ParseMode.HTML)
    except Exception:
        pass

    try:
        await bot.send_message(
            dep['user_id'],
            f"❌ <b>Deposit Declined.</b>\nYour deposit request #{deposit_id} was declined by admin.",
            parse_mode=ParseMode.HTML
        )
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
        f"🛠 <b>Support Center & Warranty Claims</b>\n\n"
        f"Please write your message or report your issue below.\n"
        f"If you are claiming a warranty replacement, include your <b>Order #</b> and the issue details."
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Cancel", callback_data="menu_back")
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@dp.message(UserState.support_message, ~F.text.startswith("/"))
async def process_support_msg(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = f"@{message.from_user.username}" if message.from_user.username else f"ID: {user_id}"

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💬 Reply User", callback_data=f"supp_reply:{user_id}")
    ]])

    header = f"🛠 <b>Support Message</b>\nFrom: {username} (<code>{user_id}</code>)\n\n"
    if message.photo:
        await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id, caption=header + (message.caption or ""), reply_markup=kb, parse_mode=ParseMode.HTML)
    else:
        await bot.send_message(ADMIN_ID, header + message.text, reply_markup=kb, parse_mode=ParseMode.HTML)

    await message.answer("✅ <b>Support request sent!</b> Admin will get back to you shortly.", parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())
    await state.clear()

@dp.callback_query(F.data.startswith("supp_reply:"))
async def cb_support_reply(call: CallbackQuery, state: FSMContext):
    await call.answer()
    target_id = int(call.data.split(":")[1])
    await state.set_state(AdminState.support_reply)
    await state.update_data(target_user=target_id)
    await call.message.answer(f"✉️ Send reply to User <code>{target_id}</code>:", parse_mode=ParseMode.HTML)

@dp.message(AdminState.support_reply, ~F.text.startswith("/"))
async def process_support_reply_step(message: Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("target_user")
    reply_text = f"🛠 <b>Support Reply:</b>\n\n{message.text}"
    try:
        await bot.send_message(target_id, reply_text, parse_mode=ParseMode.HTML)
        await message.answer("✅ Reply sent successfully.", reply_markup=get_admin_menu_keyboard())
    except Exception as e:
        await message.answer(f"❌ Failed to send reply: {e}", reply_markup=get_admin_menu_keyboard())
    await state.clear()

# ============================================
# ADMIN CONTROL PANEL
# ============================================

@dp.message(Command("admin"))
async def cmd_admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("🛠 <b>Store Admin Panel</b>", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_keyboard())

@dp.message(F.text == "➕ Add Stock")
async def admin_add_stock_prompt(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    kb = InlineKeyboardBuilder()
    kb.button(text="Add NEW Gmails", callback_data="add_stock_new")
    kb.button(text="Add OLD Gmails", callback_data="add_stock_old")
    kb.adjust(2)
    await message.answer("Select which inventory category you want to populate:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.in_({"add_stock_new", "add_stock_old"}))
async def cb_add_stock_type(call: CallbackQuery, state: FSMContext):
    await call.answer()
    acc_type = "new" if call.data == "add_stock_new" else "old"
    await state.update_data(stock_type=acc_type)
    await state.set_state(AdminState.add_stock_bulk)
    await call.message.answer(
        f"📦 <b>Bulk Upload for {acc_type.upper()} Gmails:</b>\n\n"
        f"Send accounts line by line (format: <code>email:password</code> or <code>email:password:recovery</code>):",
        parse_mode=ParseMode.HTML
    )

@dp.message(AdminState.add_stock_bulk, ~F.text.startswith("/"))
async def process_add_stock_bulk(message: Message, state: FSMContext):
    data = await state.get_data()
    acc_type = data.get("stock_type")
    lines = [line.strip() for line in message.text.strip().split("\n") if line.strip()]

    if not lines:
        await message.answer("❌ No accounts provided.", reply_markup=get_admin_menu_keyboard())
        await state.clear()
        return

    async with db_pool.acquire() as conn:
        for item in lines:
            await conn.execute(
                "INSERT INTO inventory (account_type, credentials, status) VALUES ($1, $2, 'available')",
                acc_type, item
            )

    await message.answer(
        f"✅ <b>Successfully added {len(lines)} {acc_type.upper()} Gmail account(s) to stock!</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_menu_keyboard()
    )
    await state.clear()

@dp.message(F.text == "📦 View Inventory")
async def admin_view_inventory(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    new_s, old_s = await get_stock_counts()
    async with db_pool.acquire() as conn:
        total_sold = await conn.fetchval("SELECT COUNT(*) FROM orders") or 0

    text = (
        f"📦 <b>Inventory & Sales Status:</b>\n\n"
        f"🟢 <b>Available New Gmails:</b> {new_s}\n"
        f"🟢 <b>Available Old Gmails:</b> {old_s}\n"
        f"🛒 <b>Total Accounts Sold:</b> {total_sold}"
    )
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "📊 Bot Stats")
async def admin_bot_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with db_pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users") or 0
        total_deposits = await conn.fetchval("SELECT SUM(amount) FROM deposits WHERE status='approved'") or 0.0
        total_orders = await conn.fetchval("SELECT COUNT(*) FROM orders") or 0

    text = (
        f"📊 <b>Store Statistics:</b>\n\n"
        f"👥 <b>Total Users:</b> {total_users}\n"
        f"💰 <b>Total Approved Deposits:</b> ${total_deposits:.2f}\n"
        f"📦 <b>Total Orders Fulfilled:</b> {total_orders}"
    )
    await message.answer(text, parse_mode=ParseMode.HTML)

# ============================================
# RUNNER
# ============================================

async def main():
    await init_db()
    
    server_thread = Thread(target=run_flask)
    server_thread.daemon = True
    server_thread.start()
    
    print("🤖 Gmail Purchase Bot running 24/7 on Render...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())