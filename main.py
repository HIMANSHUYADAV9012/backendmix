import os
import requests
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pymongo import MongoClient
from datetime import datetime
from pydantic import BaseModel
import httpx

# ---------- Load environment variables ----------
load_dotenv()

# ---------- MongoDB Connection (from visit.py) ----------
client = MongoClient(os.getenv("MONGODB_URI"))
db = client["visits_db"]
counters_collection = db["counters"]

# ---------- FastAPI App ----------
app = FastAPI(
    title="FollowersHub Combined API",
    description="Visit counter, packages, and Telegram notifications",
    version="1.0.0"
)

# ---------- CORS Middleware (combined from all files) ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,          # from notify.py
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Telegram Configuration (from visit.py and notify.py) ----------
# visit.py uses a single bot token for visit alerts
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")          # for visit alerts
# notify.py uses multiple tokens
BOT_TOKEN_NEW_USER = os.getenv("BOT_TOKEN_NEW_USER")
BOT_TOKEN_QR = os.getenv("BOT_TOKEN_QR")
BOT_TOKEN_ORDER = os.getenv("BOT_TOKEN_ORDER")
CHAT_ID = os.getenv("CHAT_ID")                                 # common chat id

# ---------- Admin Secret (from visit.py) ----------
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "changeme")

# --------------------------------------------------------------
# 📦 PACKAGES DATA (from packages.py)
# --------------------------------------------------------------
PACKAGES = [
    {
        "id": 1,
        "title": "10K Followers",
        "type": "followers",
        "price": 149,
        "desc": "Real • Active • Permanent",
        "popular": False,
        "discount": False,
    },
    {
        "id": 2,
        "title": "20K Followers",
        "type": "followers",
        "price": 199,
        "desc": "Real • Active • Permanent",
        "popular": True,
        "discount": False,
    },
    {
        "id": 3,
        "title": "30K Followers",
        "type": "followers",
        "price": 259,
        "desc": "Real • Active • Permanent",
        "popular": False,
        "discount": True,
    },
    {
        "id": 4,
        "title": "50K Followers",
        "type": "followers",
        "price": 399,
        "desc": "Real • Active • Permanent",
        "popular": False,
        "discount": False,
    },
    {
        "id": 5,
        "title": "100K Followers",
        "type": "followers",
        "price": 699,
        "desc": "Real • Active • Permanent",
        "popular": True,
        "discount": True,
    },

    {
        "id": 6,
        "title": "Story Views 5K",
        "type": "views",
        "price": 110,
        "desc": "Ultra-fast • Refill",
        "popular": False,
        "discount": False,
    },
    {
        "id": 7,
        "title": "Story Views 10k",
        "type": "views",
        "price": 179,
        "desc": "Ultra-fast • Refill",
        "popular": False,
        "discount": True,
    },
    {
        "id": 8,
        "title": "Story Views 15k",
        "type": "views",
        "price": 239,
        "desc": "Ultra-fast • Refill",
        "popular": False,
        "discount": False,
    },
    {
        "id": 9,
        "title": "Story Views 20K",
        "type": "views",
        "price": 299,
        "desc": "Ultra-fast • Refill",
        "popular": True,
        "discount": True,
    },
    {
        "id": 10,
        "title": "Blue Tick",
        "type": "verify",
        "price": 299,
        "desc": "Lifetime Verified Badge",
        "popular": False,
        "discount": False,
    },
    {
        "id": 11,
        "title": "Reels Boost 10K",
        "type": "views",
        "price": 199,
        "desc": "High-retention • Instant",
        "popular": False,
        "discount": False,
    },
    {
        "id": 12,
        "title": "Reels Boost 25K",
        "type": "views",
        "price": 399,
        "desc": "Explore • High Reach",
        "popular": True,
        "discount": True,
    },
]
# --------------------------------------------------------------
# 🔷 Pydantic Models (from all files)
# --------------------------------------------------------------

# From visit.py
class VisitRecord(BaseModel):
    browser: str

class AdminUpdateRequest(BaseModel):
    secret: str
    new_count: int

# From notify.py
class NewUserNotification(BaseModel):
    username: str
    mobile: str
    ip: str
    profile_status: str

class QRPaymentStarted(BaseModel):
    username: str
    mobile: str
    package: str
    amount: str
    ip: str
    is_special: bool

class PaymentStarted(BaseModel):
    username: str
    mobile: str
    package: str
    amount: str
    ip: str
    method: str

class PaymentTimeEnded(BaseModel):
    username: str
    mobile: str
    package: str
    amount: str
    ip: str
    method: str

class OrderNotification(BaseModel):
    username: str
    mobile: str
    package: str
    price: int
    ip: str

# --------------------------------------------------------------
# 📬 Helper Functions
# --------------------------------------------------------------

# From visit.py (synchronous, used in background tasks)
def send_telegram_alert(ip: str, browser: str, visit_time: str, count: int):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    text = (
        f"🚨 *Visit Alert* 🚨\n\n"
        f"🌐 IP: {ip}\n"
        f"📄 Page: HOMEPAGE\n"
        f"🧭 Browser: {browser}\n"
        f"⏰ Time: {visit_time}\n"
        f"#️⃣ Visitor Count: {count}"
    )
    try:
        requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "Markdown"
        })
    except Exception as e:
        print(f"Telegram send error: {e}")

# From notify.py (asynchronous)
async def send_telegram(bot_token: str, chat_id: str, text: str, parse_mode: str = "HTML"):
    if not bot_token or not chat_id:
        raise HTTPException(status_code=500, detail="Missing Telegram credentials")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, timeout=10.0)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"Telegram API error: {e.response.text}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

# --------------------------------------------------------------
# 🔌 API ENDPOINTS
# --------------------------------------------------------------

# ---------- Root (from packages.py) ----------
@app.get("/")
def root():
    return {
        "message": "FollowersHub Packages API",
        "endpoints": {
            "all_packages": "/packages",
            "filter_by_type": "/packages/{type}  (followers, views, verify)"
        }
    }

# ---------- Health (from notify.py) ----------
@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "Telegram Notifier"}

# ---------- Packages endpoints (from packages.py) ----------
@app.get("/packages")
def get_all_packages():
    return {"packages": PACKAGES}

@app.get("/packages/{type}")
def get_packages_by_type(type: str):
    filtered = [pkg for pkg in PACKAGES if pkg["type"] == type]
    return {"packages": filtered}

# ---------- Visit endpoints (from visit.py) ----------
@app.post("/api/visit")
async def record_visit(request: Request, background_tasks: BackgroundTasks, payload: VisitRecord):
    forwarded = request.headers.get("X-Forwarded-For")
    ip = forwarded.split(",")[0].strip() if forwarded else request.client.host

    result = counters_collection.find_one_and_update(
        {"_id": "visits"},
        {"$inc": {"count": 1}},
        upsert=True,
        return_document=True
    )
    new_count = result["count"]

    visit_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    background_tasks.add_task(send_telegram_alert, ip, payload.browser, visit_time, new_count)

    return {"success": True, "count": new_count}

@app.get("/api/admin/visits")
async def get_current_count():
    doc = counters_collection.find_one({"_id": "visits"})
    count = doc["count"] if doc else 0
    return {"current_count": count}

@app.put("/api/admin/visits")
async def update_count(request: AdminUpdateRequest):
    if request.secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")
    if request.new_count < 0:
        raise HTTPException(status_code=400, detail="Count cannot be negative")
    result = counters_collection.find_one_and_update(
        {"_id": "visits"},
        {"$set": {"count": request.new_count}},
        upsert=True,
        return_document=True
    )
    return {"success": True, "new_count": result["count"]}

# ---------- Notification endpoints (from notify.py) ----------
@app.post("/api/notify/new-user")
async def notify_new_user(data: NewUserNotification):
    text = (
        f"🔔 New User Submitted\n"
        f"👤 Username: {data.username}\n"
        f"📱 Mobile: {data.mobile}\n"
        f"🌐 IP: {data.ip}\n"
        f"📊 Status: {data.profile_status}"
    )
    result = await send_telegram(BOT_TOKEN_NEW_USER, CHAT_ID, text)
    return {"success": True, "telegram_response": result}

@app.post("/api/notify/qr-payment-started")
async def notify_qr_payment_started(data: QRPaymentStarted):
    special_text = "YES" if data.is_special else "NO"
    text = (
        f"📲 <b>QR PAYMENT STARTED 🎉</b>\n"
        f"👤 Username: <code>{data.username or 'Unknown'}</code>\n"
        f"📱 Mobile: <code>{data.mobile or 'Not Provided'}</code>\n"
        f"📦 Package: <code>{data.package}</code>\n"
        f"💰 Amount: <code>{data.amount}</code>\n"
        f"🌐 IP: <code>{data.ip}</code>\n"
        f"💎 Special User: <b>{special_text}</b>"
    )
    result = await send_telegram(BOT_TOKEN_QR, CHAT_ID, text, parse_mode="HTML")
    return {"success": True, "telegram_response": result}

@app.post("/api/notify/payment-started")
async def notify_payment_started(data: PaymentStarted):
    text = (
        f"💳 <b>New UPI PAYMENT STARTED 🎉</b>\n\n"
        f"👤 Username: <code>{data.username}</code>\n"
        f"📱 Mobile: <code>{data.mobile or 'Not Provided'}</code>\n"
        f"📦 Package: <code>{data.package}</code>\n"
        f"💰 Amount: <code>₹{data.amount}</code>\n"
        f"🏦 Payment Method: <b>{data.method}</b>\n"
        f"🌐 IP: <code>{data.ip}</code>"
    )
    result = await send_telegram(BOT_TOKEN_QR, CHAT_ID, text, parse_mode="HTML")
    return {"success": True, "telegram_response": result}

@app.post("/api/notify/payment-time-ended")
async def notify_payment_time_ended(data: PaymentTimeEnded):
    text = (
        f"⚠️ <b>PAYMENT TIME ENDED</b>\n\n"
        f"👤 Username: <code>{data.username}</code>\n"
        f"📱 Mobile: <code>{data.mobile or 'Not Provided'}</code>\n"
        f"📦 Package: <code>{data.package}</code>\n"
        f"💰 Amount: <code>₹{data.amount}</code>\n"
        f"🏦 Payment Method: <b>{data.method}</b>\n"
        f"🌐 IP: <code>{data.ip}</code>"
    )
    result = await send_telegram(BOT_TOKEN_QR, CHAT_ID, text, parse_mode="HTML")
    return {"success": True, "telegram_response": result}

@app.post("/api/notify/order")
async def notify_order(data: OrderNotification):
    text = (
        f"🛒 <b>New Purchase Request</b>\n\n"
        f"👤 Username: <code>{data.username}</code>\n"
        f"📱 Mobile: <code>{data.mobile or 'Not Provided'}</code>\n"
        f"📦 Package: {data.package}\n"
        f"💰 Amount: ₹{data.price}\n"
        f"🌐 IP: {data.ip}"
    )
    result = await send_telegram(BOT_TOKEN_ORDER, CHAT_ID, text, parse_mode="HTML")
    return {"success": True, "telegram_response": result}

# To run: uvicorn main:app --reload







