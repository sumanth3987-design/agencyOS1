import os
os.environ.setdefault("STRIPE_SECRET_KEY", "")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test_secret")

from app import create_app
from app.extensions import db
from app.models import User, Project, StripeWebhookEvent
from unittest.mock import patch
import json

app = create_app("default", {
    "TESTING": True,
    "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    "SQLALCHEMY_BINDS": {},
    "SQLALCHEMY_ENGINE_OPTIONS": {},
    "WTF_CSRF_ENABLED": True,
    "RATELIMIT_ENABLED": True,
    "RATELIMIT_STORAGE_URI": "memory://",
    "SECRET_KEY": "test-secret-key-0123456789abcdef0123456789abcdef",
})

with app.app_context():
    db.session.query(StripeWebhookEvent).delete()
    db.session.query(Project).delete()
    db.session.query(User).delete()
    db.session.commit()

    customer = User(name="Webhook Paid", email="webhook-paid@example.com",
                    password="x", role="customer")
    db.session.add(customer)
    db.session.flush()
    project = Project(customer_id=customer.id, package="Basic Website",
                      currency="USD", status="Payment Pending")
    db.session.add(project)
    db.session.commit()
    pid = project.id
    print("Initial project status:", project.status)

    event = {
        "id": "evt_paid_123",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_123",
                "metadata": {"project_id": str(pid)},
                "amount_total": 34950,
                "currency": "usd",
                "payment_status": "paid",
                "payment_intent": "pi_test_123",
            }
        },
    }

    with app.test_client() as client:
        with patch("app.blueprints.payments.stripe.stripe.Webhook.construct_event", return_value=event), \
             patch("app.blueprints.payments.stripe.STRIPE_WEBHOOK_SECRET", "whsec_test_secret"):
            resp = client.post("/payment/stripe/webhook",
                               data=json.dumps(event),
                               content_type="application/json",
                               headers={"Stripe-Signature": "test_sig"})
        print("Response status:", resp.status_code)
        print("Response body:", resp.get_data(as_text=True))

    with app.app_context():
        p2 = db.session.get(Project, pid)
        print("Final project status:", p2.status)

