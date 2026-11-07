"""Payment gateway adapters.

The merchant's customers pay real money through a gateway; we only credit the
wallet after the gateway confirms. In production each merchant would plug in
their own local gateway (Easebuzz, Razorpay, Paystack, ...). For the demo we
ship two adapters: Stripe test mode, and a mock gateway that needs no keys.
"""
import config

try:
    import stripe
except ImportError:  # pragma: no cover
    stripe = None


def provider_name():
    return "stripe" if config.STRIPE_SECRET_KEY and stripe else "mock"


def create_checkout(topup, merchant):
    """Return (provider_ref, checkout_url) for a pending top-up."""
    return_url = f"{config.PUBLIC_URL}/pay/return/{topup['public_id']}"
    if provider_name() == "stripe":
        stripe.api_key = config.STRIPE_SECRET_KEY
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": topup["currency"],
                    "unit_amount": topup["amount"],
                    "product_data": {"name": f"{merchant['business_name']} wallet top-up"},
                },
                "quantity": 1,
            }],
            success_url=return_url,
            cancel_url=return_url,
            metadata={"topup_id": topup["public_id"]},
        )
        return session.id, session.url
    return None, f"{config.PUBLIC_URL}/pay/mock/{topup['public_id']}"


def is_paid(topup):
    """Ask the gateway whether this top-up has been paid."""
    if topup["provider"] == "stripe":
        stripe.api_key = config.STRIPE_SECRET_KEY
        session = stripe.checkout.Session.retrieve(topup["provider_ref"])
        return session.payment_status == "paid"
    return False  # mock top-ups are confirmed explicitly on the mock page


def parse_webhook(payload, sig_header):
    stripe.api_key = config.STRIPE_SECRET_KEY
    return stripe.Webhook.construct_event(payload, sig_header, config.STRIPE_WEBHOOK_SECRET)
