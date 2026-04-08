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
def hash_license(license_key: str):
    return hashlib.sha256(license_key.encode()).hexdigest()

def generate_license_key():
    return "-".join(
        ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        for _ in range(4)
    )

# ✅ ADD HERE (exact place)
def send_email(to_email, license_key):

    resend.api_key = os.getenv("RESEND_API_KEY")

    try:
        resend.Emails.send({
            "from": "Bird Manager Pro <mail@subirbasak.com>",
            "to": [to_email],
            "subject": "Your Bird Manager Pro License",
            "html": f"""
            <h2>Thank you for your purchase!</h2>
            <p>Your license key:</p>
            <h1>{license_key}</h1>
            <p>Please keep this key safe.</p>
            """
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

    # Verify signature (skip if testing)
    if signature:
        generated_signature = hmac.new(
            secret.encode(),
            body,
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(generated_signature, signature):
            print("❌ INVALID SIGNATURE")
            return {"status": "invalid signature"}

    try:
        data = await request.json()
    except Exception:
        return {"status": "invalid json"}

    print("Webhook data:", data)

    if data.get("event") == "payment.captured":

        payment = data["payload"]["payment"]["entity"]

        email = payment.get("email")

        if not email or "@" not in email:
            print("⚠️ Invalid email")
            email = None

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

        print("✅ LICENSE CREATED:", license_key)
        if email:
            try:
                send_email(email, license_key)
            except Exception as e:
                print("❌ Email error:", str(e))

    return {"status": "ok"}