import os
import ssl
import certifi
import logging
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError, OperationFailure
from datetime import datetime
from pydantic import BaseModel
import httpx
import requests

load_dotenv()

# ---------------------------- LOGGING ----------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------- MONGODB CONNECTION (PRODUCTION) ----------------------------
MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise ValueError("❌ MONGODB_URI environment variable not set")

# Ensure database name is present (if not, append default)
if "?" in MONGODB_URI:
    base, params = MONGODB_URI.split("?", 1)
    if "/" not in base.split("@")[-1]:  # no database name in path
        base += "/visits_db"
    MONGODB_URI = f"{base}?{params}"
else:
    if "/" not in MONGODB_URI.split("@")[-1]:
        MONGODB_URI += "/visits_db"

# Production MongoDB client with TLS 1.2 enforcement and short timeouts
try:
    client = MongoClient(
        MONGODB_URI,
        tlsCAFile=certifi.where(),
        tls=True,
        tlsVersion=ssl.PROTOCOL_TLSv1_2,          # Force TLS 1.2
        serverSelectionTimeoutMS=5000,            # 5 seconds to select server
        connectTimeoutMS=5000,                    # 5 seconds to connect
        socketTimeoutMS=30000,                     # 30 seconds for operations
        maxPoolSize=1,                             # For serverless (no pooling)
        retryWrites=True,
        retryReads=True,
    )
    # Force a connection to verify
    client.admin.command('ismaster')
    logger.info("✅ MongoDB connected successfully")
    db = client.get_default_database()              # Automatically picks from URI
    counters_collection = db["counters"]
except (ServerSelectionTimeoutError, ConnectionFailure) as e:
    logger.error(f"❌ MongoDB connection failed: {e}")
    client = None
    db = None
    counters_collection = None
except Exception as e:
    logger.error(f"❌ Unexpected MongoDB error: {e}")
    client = None
    db = None
    counters_collection = None

# ---------------------------- FASTAPI APP ----------------------------
app = FastAPI(title="FollowersHub Combined API", version="1.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://followerssupply.store",
        "https://www.followerssupply.store",
        # Add your frontend domain(s)
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------- TELEGRAM CONFIG ----------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BOT_TOKEN_NEW_USER = os.getenv("BOT_TOKEN_NEW_USER")
BOT_TOKEN_QR = os.getenv("BOT_TOKEN_QR")
BOT_TOKEN_ORDER = os.getenv("BOT_TOKEN_ORDER")
CHAT_ID = os.getenv("CHAT_ID")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "changeme")

# ---------------------------- PACKAGES DATA (unchanged) ----------------------------
PACKAGES = [ ... ]   # (your existing packages list)

# ---------------------------- PYDANTIC MODELS ----------------------------
class VisitRecord(BaseModel):
    browser: str

class AdminUpdateRequest(BaseModel):
    secret: str
    new_count: int

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

# ---------------------------- HELPER FUNCTIONS ----------------------------
def send_telegram_alert(ip: str, browser: str, visit_time: str, count: int):
    """Background task for visit alerts (synchronous)"""
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        return
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
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except Exception as e:
        logger.error(f"Telegram send error: {e}")

async def send_telegram(bot_token: str, chat_id: str, text: str, parse_mode: str = "HTML"):
    """Async telegram sender for other notifications"""
    if not bot_token or not chat_id:
        raise HTTPException(status_code=500, detail="Missing Telegram credentials")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Telegram async error: {e}")
            raise HTTPException(status_code=502, detail="Telegram API error")

# ---------------------------- ENDPOINTS ----------------------------
@app.get("/")
async def root():
    return {"message": "FollowersHub API", "endpoints": {"/packages": "GET", "/health": "GET"}}

@app.get("/health")
async def health_check():
    """Health check with MongoDB status"""
    mongo_status = "connected" if counters_collection is not None else "disconnected"
    return {"status": "ok", "mongo": mongo_status}

# Packages endpoints
@app.get("/packages")
async def get_all_packages():
    return {"packages": PACKAGES}

@app.get("/packages/{type}")
async def get_packages_by_type(type: str):
    filtered = [pkg for pkg in PACKAGES if pkg["type"] == type]
    return {"packages": filtered}

# Visit endpoints
@app.post("/api/visit")
async def record_visit(request: Request, background_tasks: BackgroundTasks, payload: VisitRecord):
    if counters_collection is None:
        raise HTTPException(status_code=503, detail="Database service unavailable")
    try:
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
    except (ConnectionFailure, OperationFailure, ServerSelectionTimeoutError) as e:
        logger.error(f"Database error in /api/visit: {e}")
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")
    except Exception as e:
        logger.error(f"Unexpected error in /api/visit: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/admin/visits")
async def get_current_count():
    if counters_collection is None:
        raise HTTPException(status_code=503, detail="Database service unavailable")
    try:
        doc = counters_collection.find_one({"_id": "visits"})
        count = doc["count"] if doc else 0
        return {"current_count": count}
    except Exception as e:
        logger.error(f"Error in get_current_count: {e}")
        raise HTTPException(status_code=503, detail="Database error")

@app.put("/api/admin/visits")
async def update_count(request: AdminUpdateRequest):
    if counters_collection is None:
        raise HTTPException(status_code=503, detail="Database service unavailable")
    if request.secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")
    if request.new_count < 0:
        raise HTTPException(status_code=400, detail="Count cannot be negative")
    try:
        result = counters_collection.find_one_and_update(
            {"_id": "visits"},
            {"$set": {"count": request.new_count}},
            upsert=True,
            return_document=True
        )
        return {"success": True, "new_count": result["count"]}
    except Exception as e:
        logger.error(f"Error in update_count: {e}")
        raise HTTPException(status_code=503, detail="Database error")

# Notification endpoints (from notify.py) – include all, each with db check if needed (none of them use db, so no db check needed)
@app.post("/api/notify/new-user")
async def notify_new_user(data: NewUserNotification):
    text = f"🔔 New User Submitted\n👤 Username: {data.username}\n📱 Mobile: {data.mobile}\n🌐 IP: {data.ip}\n📊 Status: {data.profile_status}"
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
