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
import hashlib
from fastapi.middleware.cors import CORSMiddleware
load_dotenv()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    parts = []
    for _ in range(4):
        part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        parts.append(part)
    return '-'.join(parts)

@app.get("/")
def home():
    return {"message": "Backend is running"}
    
@app.post("/create-order")
async def create_order():

    try:
        order = client.order.create({
            "amount": 299900,  # ₹2999
            "currency": "INR",
            "payment_capture": 1
        })

        return {
            "status": "success",
            "order_id": order["id"]
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}
    

@app.post("/verify-payment")
async def verify_payment(data: PaymentRequest):

    try:
        payment_id = data.payment_id

        # Verify with Razorpay
        payment = client.payment.fetch(payment_id)

        if payment["status"] != "captured":
            return {"status": "failed", "message": "Payment not captured"}

        return {
            "status": "success",
            "message": "Payment verified"
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}
        
from fastapi import Request

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