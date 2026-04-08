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
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
load_dotenv()

app = FastAPI()

@app.get("/")
def home():
    return {"status": "running"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
conf = ConnectionConfig(
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_FROM=os.getenv("MAIL_FROM"),
    MAIL_PORT=int(os.getenv("MAIL_PORT")),
    MAIL_SERVER=os.getenv("MAIL_SERVER"),
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True
)

# Razorpay client
client = razorpay.Client(auth=(
    os.getenv("RAZORPAY_KEY_ID"),
    os.getenv("RAZORPAY_KEY_SECRET")
))

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

class PaymentRequest(BaseModel):
    payment_id: str
    email: str
    name: str


def hash_license(license_key: str):
    return hashlib.sha256(license_key.encode()).hexdigest()


def generate_license_key():
    return "-".join(
        ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        for _ in range(4)
    )


@app.get("/")
def home():
    return {"message": "Backend is running"}


# ✅ TEST ENDPOINT (VERY IMPORTANT)
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


@app.post("/create-order")
async def create_order():

    for attempt in range(3):  # retry 3 times
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


@app.post("/verify-payment")
async def verify_payment(data: PaymentRequest):
    try:
        payment = client.payment.fetch(data.payment_id)

        if payment["status"] != "captured":
            return {"status": "failed"}

        # ✅ Do NOT create license here
        # License is created via webhook

        return {
            "status": "success"
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/webhook")
async def razorpay_webhook(request: Request):

    print("🔥 WEBHOOK HIT 🔥")

    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")

    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")

    # 🟡 Allow manual testing (no signature)
    if signature:
        generated_signature = hmac.new(
            secret.encode(),
            body,
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(generated_signature, signature):
            print("❌ INVALID SIGNATURE")
            return {"status": "invalid signature"}

    data = await request.json()
    print("Webhook data:", data)

    if data.get("event") == "payment.captured":

        payment = data["payload"]["payment"]["entity"]

        # ✅ Get email safely
        email = payment.get("email")

        # ✅ Validate email
        if not email or "@" not in email:
            print("⚠️ Invalid or missing email")
            email = None

        # ✅ Generate license
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

        print("📧 Email sending skipped (temporary)")
    return {"status": "ok"}
    
async def send_license_email(email: str, license_key: str):

    message = MessageSchema(
        subject="Your Bird Manager Pro License",
        recipients=[email],
        body=f"""
        Thank you for purchasing Bird Manager Pro.

        Your License Key:
        {license_key}

        Payment Details:
        - Product: Bird Manager Pro
        - Amount: ₹2999
        - Date: {datetime.utcnow().strftime("%Y-%m-%d")}

        Please keep this email for your records.

        Bird Manager Pro
        """,
        subtype="plain"
    )

    fm = FastMail(conf)
    await fm.send_message(message)
    