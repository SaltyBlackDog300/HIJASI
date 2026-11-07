import re
from urllib.parse import urlsplit


POLICY_VERSION = "hackathon-v1"
CATEGORIES = ["food_and_drink", "physical_goods", "digital_services", "other"]
FIELDS = {
    "legal_name": {"type": "text", "max_length": 200, "required": True},
    "registration_number": {"type": "text", "max_length": 80, "required": True},
    "registration_country": {"type": "country_code", "required": True},
    "contact_name": {"type": "text", "max_length": 160, "required": True},
    "contact_email": {"type": "email", "max_length": 254, "required": True},
    "website": {"type": "url", "max_length": 2048, "required": False},
    "product_category": {"type": "choice", "options": CATEGORIES, "required": True},
    "product_description": {"type": "text", "max_length": 4000, "required": True},
    "sample_products": {"type": "text_list", "max_items": 10, "required": True},
    "selling_countries": {"type": "country_list", "max_items": 50, "required": True},
    "regulated_products": {"type": "boolean", "required": True},
    "licence_details": {"type": "text", "max_length": 2000, "required": False},
    "declares_accurate_and_lawful": {"type": "boolean", "required": True},
    "accepted_policy_version": {"type": "text", "max_length": 80, "required": True},
}
POLICY = {
    "version": POLICY_VERSION,
    "text": (
        "Hackathon sandbox policy: simulated funds only. Describe all products accurately; "
        "do not offer illegal, counterfeit, deceptive or unlicensed regulated products. "
        "Product or business changes require another review. Platform approval is an internal "
        "sandbox decision, not legal certification or payment-provider approval. "
        "Live use requires a separately agreed provider policy and approved funds flow."
    ),
}


def valid_url(value):
    try:
        parsed = urlsplit(value)
        return (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                and not parsed.username and not parsed.password
                and not any(c.isspace() for c in value))
    except ValueError:
        return False


def validate_details(data, complete=False):
    """Return normalized fields and field errors; never accept approval/identity fields."""
    errors, clean = {}, {}
    if not isinstance(data, dict):
        return {}, {"details": "Must be an object."}
    for key in data.keys() - FIELDS.keys():
        errors[key] = "Unknown field."
    for key, spec in FIELDS.items():
        if key not in data:
            if complete and spec["required"]:
                errors[key] = "Required."
            continue
        value = data[key]
        kind = spec["type"]
        if kind == "boolean":
            if type(value) is not bool:
                errors[key] = "Must be a boolean."
                continue
        elif kind in {"text_list", "country_list"}:
            if (not isinstance(value, list) or not 1 <= len(value) <= spec["max_items"]
                    or any(not isinstance(v, str) or not v.strip() or len(v) > 300 for v in value)):
                errors[key] = f"Supply 1 to {spec['max_items']} nonempty strings (maximum 300 characters each)."
                continue
            value = [v.strip() for v in value]
            if kind == "country_list":
                value = [v.upper() for v in value]
                if any(not re.fullmatch(r"[A-Z]{2}", v) for v in value):
                    errors[key] = "Use two-letter country codes."
                    continue
        else:
            if not isinstance(value, str):
                errors[key] = "Must be text."
                continue
            value = value.strip()
            if len(value) > spec.get("max_length", 200):
                errors[key] = "Too long."
                continue
            if complete and spec["required"] and not value:
                errors[key] = "Required."
                continue
            if kind == "country_code":
                value = value.upper()
                if not re.fullmatch(r"[A-Z]{2}", value):
                    errors[key] = "Use a two-letter country code."
            if kind == "choice" and value not in spec["options"]:
                errors[key] = "Choose a supported category."
            if kind == "email" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
                errors[key] = "Supply a valid email address."
            if kind == "url" and value and not valid_url(value):
                errors[key] = "Supply an http(s) URL without credentials."
        clean[key] = value
    if complete:
        if clean.get("regulated_products") and not clean.get("licence_details"):
            errors["licence_details"] = "Describe the applicable licence and how it can be verified."
        if clean.get("declares_accurate_and_lawful") is not True:
            errors["declares_accurate_and_lawful"] = "Declaration must be accepted."
        if clean.get("accepted_policy_version") != POLICY_VERSION:
            errors["accepted_policy_version"] = "Accept the current policy version."
    return clean, errors
