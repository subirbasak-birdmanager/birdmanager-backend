from fastapi import FastAPI, Request
import razorpay
import os
from dotenv import load_dotenv
from pydantic import BaseModel
import random
import string
from supabase import create_client
import hashlib
from datetime import datetime
import hmac
from fastapi.middleware.cors import CORSMiddleware
import resend
load_dotenv()

app = FastAPI()

# ✅ CORS FIRST
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ HEALTH CHECK (IMPORTANT)
@app.api_route("/", methods=["GET", "HEAD"])
def home():
    return {"status": "running"}

# Razorpay client
client = razorpay.Client(auth=(
    os.getenv("RAZORPAY_KEY_ID"),
    os.getenv("RAZORPAY_KEY_SECRET")
))

# Supabase
supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

# Request model
class PaymentRequest(BaseModel):
    payment_id: str
    email: str
    name: str

# Utils
def normalize_license_key(key: str):
    return key.replace("-", "").replace(" ", "").upper()

def hash_license(license_key: str):
    normalized = normalize_license_key(license_key)
    return hashlib.sha256(normalized.encode()).hexdigest()

def generate_license_key():
    return "-".join(
        ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        for _ in range(4)
    )
    
def verify_admin(secret: str):
    env_secret = os.getenv("ADMIN_SECRET") or ""

    print("INPUT:", repr(secret))
    print("ENV:", repr(env_secret))

    return secret.strip() == env_secret.strip()

# ✅ ADD HERE (exact place)
def send_email(to_email, license_key, payment_id):

    resend.api_key = os.getenv("RESEND_API_KEY")

    try:
        html = f"""
        <div style="font-family: Arial, sans-serif; max-width:600px; margin:auto; border:1px solid #eee; padding:20px;">

            <h2 style="color:#2c3e50;">Bird Manager Pro</h2>
            <hr>

            <p>Thank you for your purchase!</p>

            <h3>🧾 Order Details</h3>
            <p><strong>Product:</strong> Bird Manager Pro (Full License)</p>
            <p><strong>Amount:</strong> ₹2999</p>
            <p><strong>Payment ID:</strong> {payment_id}</p>

            <h3>🔑 Your License Key</h3>
            <div style="background:#f4f4f4; padding:15px; font-size:18px; letter-spacing:2px;">
                {license_key}
            </div>

            <p style="margin-top:20px;">
                Please keep this key safe. You will need it to activate your software.
            </p>

            <hr>

            <p style="font-size:12px; color:gray;">
                This is an automated email. Do not reply.
            </p>

        </div>
        """

        resend.Emails.send({
            "from": "Bird Manager Pro <mail@subirbasak.com>",
            "to": [to_email],
            "subject": "Your Bird Manager Pro License",
            "html": html
        })

        print("📧 Email sent to:", to_email)

    except Exception as e:
        print("❌ Email failed:", str(e))

# ✅ TEST ENDPOINT
@app.get("/test-webhook")
def test_webhook():
    license_key = generate_license_key()
    license_hash = hash_license(license_key)

    supabase.table("licenses").insert({
        "activation_code_hash": license_hash,
        "email": "",
        "name": "",
        "license_type": "full",
        "status": "unused",
        "issued_at": datetime.utcnow().isoformat()
    }).execute()

    return {"status": "ok", "license": license_key}

# ✅ CREATE ORDER
@app.post("/create-order")
async def create_order():
    for attempt in range(3):
        try:
            order = client.order.create({
                "amount": 1000,
                "currency": "INR",
                "payment_capture": 1
            })

            return {
                "status": "success",
                "order_id": order["id"]
            }

        except Exception as e:
            print(f"❌ ORDER ERROR (attempt {attempt+1}):", str(e))

    return {"status": "error", "message": "Failed after retries"}

# ✅ VERIFY PAYMENT
@app.post("/verify-payment")
async def verify_payment(data: PaymentRequest):
    try:
        payment = client.payment.fetch(data.payment_id)

        if payment["status"] != "captured":
            return {"status": "failed"}

        return {"status": "success"}

    except Exception as e:
        return {"status": "error", "message": str(e)}

# ✅ WEBHOOK (CORE LOGIC)
@app.post("/webhook")
async def razorpay_webhook(request: Request):

    print("🔥 WEBHOOK HIT 🔥")

    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")

    # ✅ STRICT VERIFICATION (NEW)
    if not signature:
        print("❌ Missing signature")
        return {"status": "error"}

    expected_signature = hmac.new(
        secret.encode(),
        body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, signature):
        print("❌ Invalid signature")
        return {"status": "invalid"}

    try:
        data = await request.json()
    except Exception:
        return {"status": "invalid json"}

    print("Webhook data:", data)

    if data.get("event") == "payment.captured":

        payment = data["payload"]["payment"]["entity"]

        payment_id = payment.get("id")

        existing = supabase.table("licenses") \
            .select("*") \
            .eq("payment_id", payment_id) \
            .execute()

        if existing.data:
            print("⚠️ Duplicate webhook ignored")
            return {"status": "duplicate"}

        email = payment.get("email")

        if not email or "@" not in email:
            print("⚠️ Invalid email")
            email = None

        license_key = generate_license_key()
        license_hash = hash_license(license_key)

        notes = payment.get("notes")

        if isinstance(notes, dict):
            name = notes.get("name", "")
        else:
            name = ""

        if not payment_id:
            print("❌ Missing payment_id")
            return {"status": "error"}

        supabase.table("licenses").insert({
            "activation_code_hash": license_hash,
            "license_key": license_key,
            "email": email or "",
            "name": name,
            "license_type": "full",
            "status": "unused",
            "payment_id": payment_id,
            "issued_at": datetime.utcnow().isoformat()
        }).execute()

        print("✅ LICENSE CREATED:", license_key)

        if email:
            try:
                send_email(email, license_key, payment_id)
            except Exception as e:
                print("❌ Email error:", str(e))

    # ✅ ALWAYS OUTSIDE
    return {"status": "ok"}
    
@app.post("/activate")
async def activate_license(data: dict):

    license_key = data.get("license_key")
    machine_hash = data.get("machine_hash")

    if not license_key or not machine_hash:
        return {"status": "error"}

    license_hash = hash_license(license_key)

    result = supabase.table("licenses") \
        .select("*") \
        .eq("activation_code_hash", license_hash) \
        .execute()

    if not result.data:
        return {"status": "invalid"}

    license_data = result.data[0]

    # ✅ CHECK STATUS
    if license_data["status"] not in ["unused", "active"]:
        return {"status": "blocked"}

    stored_machine = license_data.get("bound_machine_hash")

    # ✅ FIRST ACTIVATION
    if not stored_machine:

        supabase.table("licenses").update({
            "bound_machine_hash": machine_hash,
            "status": "active",
            "activated_at": datetime.utcnow().isoformat()
        }).eq("activation_code_hash", license_hash).execute()

        return {"status": "activated"}

    # ✅ SAME MACHINE
    if stored_machine == machine_hash:
        return {"status": "active"}

    # ❌ DIFFERENT MACHINE
    return {"status": "blocked"}
    
@app.get("/admin/licenses")
async def get_licenses(secret: str = ""):

    if not verify_admin(secret):
        return {"status": "unauthorized"}

    data = supabase.table("licenses") \
        .select("*") \
        .order("issued_at", desc=True) \
        .limit(100) \
        .execute()

    return {"status": "ok", "data": data.data}
    
@app.get("/admin/search")
async def search_licenses(query: str, secret: str = ""):

    if not verify_admin(secret):
        return {"status": "unauthorized"}

    data = supabase.table("licenses") \
        .select("*") \
        .ilike("email", f"%{query}%") \
        .execute()

    return {"status": "ok", "data": data.data}
    
@app.post("/admin/revoke")
async def revoke_license(data: dict):

    if not verify_admin(data.get("secret")):
        return {"status": "unauthorized"}

    supabase.table("licenses").update({
        "status": "revoked"
    }).eq("payment_id", data.get("payment_id")).execute()

    return {"status": "revoked"}
    
@app.post("/admin/resend")
async def resend_license(data: dict):

    if not verify_admin(data.get("secret")):
        return {"status": "unauthorized"}

    result = supabase.table("licenses") \
        .select("*") \
        .eq("payment_id", data.get("payment_id")) \
        .execute()

    if not result.data:
        return {"status": "not_found"}

    row = result.data[0]

    # ⚠️ You CANNOT recover original key from hash
    return {"status": "not_possible", "message": "Store raw key if you want resend"}