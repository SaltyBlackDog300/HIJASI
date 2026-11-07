from dataclasses import dataclass
from datetime import timezone
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from .models import Application, AuditEvent, Base
from .validation import FIELDS, POLICY, validate_details


@dataclass(frozen=True)
class Principal:
    """Construct ONLY from the host's verified session/token and database memberships."""

    user_id: str
    role: str  # merchant_admin or platform_admin
    merchant_ids: frozenset[str] = frozenset()


class Problem(Exception):
    def __init__(self, status, code, message, fields=None):
        self.status, self.code, self.message, self.fields = status, code, message, fields


def fail(status, code, message, fields=None):
    raise Problem(status, code, message, fields)


def state():
    return current_app.extensions["merchant_verification"]


def db():
    if "verification_db" not in g:
        g.verification_db = Session(state()["engine"])
    return g.verification_db


def principal():
    if "verification_principal" not in g:
        p = state()["resolve_principal"]()
        if (not isinstance(p, Principal) or not p.user_id
                or p.role not in {"merchant_admin", "platform_admin"}):
            fail(401, "authentication_required", "Sign in with an authorized account.")
        g.verification_principal = p
    return g.verification_principal


def admin():
    p = principal()
    if p.role != "platform_admin":
        fail(403, "admin_required", "Platform admin access is required.")
    return p


def merchant_access(merchant_id):
    p = principal()
    if p.role != "merchant_admin" or merchant_id not in p.merchant_ids:
        fail(403, "merchant_forbidden", "You cannot manage this merchant.")
    return p


def body(allowed):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        fail(400, "invalid_json", "Supply a JSON object.")
    extra = data.keys() - allowed
    if extra:
        fail(422, "invalid_fields", "Unknown request fields.", {k: "Unknown field." for k in extra})
    return data


def application_for_merchant(merchant_id):
    return db().scalar(select(Application).where(Application.merchant_id == merchant_id))


def application_by_id(application_id):
    a = db().get(Application, application_id)
    if a is None:
        fail(404, "not_found", "Application not found.")
    return a


def check_version(a, data):
    value = data.get("expected_version")
    if type(value) is not int or value < 1:
        fail(422, "version_required", "Supply the application's expected_version as a positive integer.")
    if value != a.version:
        fail(409, "version_conflict", "Application changed. Reload it before continuing.")


def serialize(a):
    return {
        "id": a.id, "merchant_id": a.merchant_id, "status": a.status,
        "version": a.version, "details": a.details, "review_reason": a.review_reason,
        "created_at": timestamp(a.created_at), "updated_at": timestamp(a.updated_at),
    }


def timestamp(value):
    # SQLite omits timezone information on read; stored timestamps are always UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def record(a, action, previous, evidence=None):
    # Flush obtains the new version. Event and state are committed together.
    try:
        db().flush()
    except (StaleDataError, IntegrityError):
        db().rollback()
        fail(409, "version_conflict", "Application changed. Reload it before continuing.")
    db().add(AuditEvent(
        application_id=a.id, actor_id=principal().user_id, action=action,
        from_status=previous, to_status=a.status, application_version=a.version,
        evidence=evidence or {},
    ))


def commit():
    try:
        db().commit()
    except (StaleDataError, IntegrityError):
        db().rollback()
        fail(409, "version_conflict", "Application changed. Reload it before continuing.")


def require_approved_merchant(merchant_id_getter):
    """Guard new commercial operations. Does not replace wallet ownership/scoping checks."""
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            merchant_id = merchant_id_getter(*args, **kwargs)
            merchant_access(merchant_id)
            a = application_for_merchant(merchant_id)
            if a is None or a.status != "approved":
                fail(403, "merchant_not_approved", "Merchant approval is required for this operation.")
            return view(*args, **kwargs)
        return wrapped
    return decorate


def init_verification(app, *, resolve_principal, mutation_guard, database_url=None,
                      engine=None, create_tables=False):
    """mutation_guard must enforce the host's CSRF/token policy and return True on success."""
    if engine is None:
        if not database_url:
            raise ValueError("Supply an engine or database_url.")
        engine = create_engine(database_url)
    if create_tables:
        Base.metadata.create_all(engine)
    app.extensions["merchant_verification"] = {
        "engine": engine, "resolve_principal": resolve_principal, "mutation_guard": mutation_guard,
    }

    @app.errorhandler(Problem)
    def handle_problem(exc):
        return jsonify(error={"code": exc.code, "message": exc.message, "fields": exc.fields or {}}), exc.status

    @app.teardown_appcontext
    def close_session(exc):
        session = g.pop("verification_db", None)
        if session is not None:
            session.close()

    bp = Blueprint("merchant_verification", __name__, url_prefix="/verification")

    @bp.before_request
    def protect():
        request.max_content_length = 64 * 1024
        principal()
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            if state()["mutation_guard"]() is not True:
                fail(403, "mutation_forbidden", "Request failed the host application's CSRF/token checks.")

    @bp.after_request
    def no_cache(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.get("/form")
    def form():
        return jsonify(fields=FIELDS, field_order=list(FIELDS), policy=POLICY)

    @bp.get("/me")
    def me():
        p = principal()
        return jsonify(user_id=p.user_id, role=p.role, merchant_ids=sorted(p.merchant_ids))

    @bp.get("/merchants/<merchant_id>/application")
    def get_application(merchant_id):
        merchant_access(merchant_id)
        a = application_for_merchant(merchant_id)
        if a is None:
            fail(404, "not_found", "No application exists yet.")
        return jsonify(serialize(a))

    @bp.post("/merchants/<merchant_id>/application")
    def create_application(merchant_id):
        merchant_access(merchant_id)
        if len(merchant_id) > 128:
            fail(422, "invalid_merchant", "Merchant identifier is too long.")
        data = body({"details"})
        details, errors = validate_details(data.get("details", {}))
        if errors:
            fail(422, "invalid_fields", "Correct the application fields.", errors)
        if application_for_merchant(merchant_id):
            fail(409, "already_exists", "This merchant already has an application.")
        a = Application(merchant_id=merchant_id, status="draft", details=details)
        db().add(a)
        record(a, "created", None, {"details": details})
        commit()
        return jsonify(serialize(a)), 201

    @bp.patch("/merchants/<merchant_id>/application")
    def edit_application(merchant_id):
        merchant_access(merchant_id)
        a = application_for_merchant(merchant_id)
        if a is None:
            fail(404, "not_found", "No application exists yet.")
        data = body({"details", "expected_version"})
        check_version(a, data)
        if a.status not in {"draft", "needs_information"}:
            fail(409, "invalid_transition", "Only drafts or applications needing information can be edited.")
        if not isinstance(data.get("details"), dict):
            fail(422, "invalid_fields", "details must be an object.")
        details, errors = validate_details(data["details"])
        if errors:
            fail(422, "invalid_fields", "Correct the application fields.", errors)
        merged = {**a.details, **details}
        # Changing any declarations invalidates the previous policy acceptance.
        if details and merged != a.details:
            merged["declares_accurate_and_lawful"] = details.get("declares_accurate_and_lawful", False)
            merged["accepted_policy_version"] = details.get("accepted_policy_version", "")
        if merged == a.details:
            return jsonify(serialize(a))
        a.details = merged
        record(a, "edited", a.status, {"details": merged})
        commit()
        return jsonify(serialize(a))

    @bp.post("/merchants/<merchant_id>/application/submit")
    def submit_application(merchant_id):
        merchant_access(merchant_id)
        a = application_for_merchant(merchant_id)
        if a is None:
            fail(404, "not_found", "No application exists yet.")
        data = body({"expected_version"})
        check_version(a, data)
        if a.status not in {"draft", "needs_information"}:
            fail(409, "invalid_transition", "This application cannot be submitted in its current state.")
        details, errors = validate_details(a.details, complete=True)
        if errors:
            fail(422, "incomplete_application", "Complete the application before submitting.", errors)
        previous = a.status
        a.details, a.status, a.review_reason = details, "submitted", None
        record(a, "submitted", previous, {"details": details, "policy": POLICY})
        commit()
        return jsonify(serialize(a))

    @bp.get("/admin/applications")
    def review_queue():
        admin()
        status = request.args.get("status", "submitted")
        if status not in {"draft", "submitted", "under_review", "needs_information", "approved", "rejected", "suspended"}:
            fail(422, "invalid_status", "Unknown status.")
        try:
            offset = int(request.args.get("offset", 0))
            limit = int(request.args.get("limit", 25))
            if offset < 0 or not 1 <= limit <= 100:
                raise ValueError
        except ValueError:
            fail(422, "invalid_pagination", "Use offset >= 0 and limit between 1 and 100.")
        rows = db().scalars(select(Application).where(Application.status == status)
                            .order_by(Application.created_at, Application.id).offset(offset).limit(limit))
        return jsonify(items=[serialize(a) for a in rows], offset=offset, limit=limit)

    @bp.get("/admin/applications/<application_id>")
    def review_detail(application_id):
        admin()
        return jsonify(serialize(application_by_id(application_id)))

    @bp.post("/admin/applications/<application_id>/review")
    def review_application(application_id):
        p = admin()
        a = application_by_id(application_id)
        if a.merchant_id in p.merchant_ids:
            fail(403, "self_review_forbidden", "You cannot review your own business.")
        data = body({"action", "expected_version", "reason", "checks", "evidence"})
        check_version(a, data)
        transitions = {
            "start_review": ({"submitted"}, "under_review"),
            "request_information": ({"under_review"}, "needs_information"),
            "approve": ({"under_review"}, "approved"),
            "reject": ({"under_review"}, "rejected"),
            "suspend": ({"approved"}, "suspended"),
            "reopen": ({"approved", "rejected", "suspended"}, "needs_information"),
        }
        action = data.get("action")
        if not isinstance(action, str) or action not in transitions:
            fail(422, "invalid_action", "Unknown review action.")
        sources, target = transitions[action]
        if a.status not in sources:
            fail(409, "invalid_transition", "This action is not allowed in the current state.")
        reason = data.get("reason", "")
        if not isinstance(reason, str) or len(reason) > 2000 or (action != "start_review" and not reason.strip()):
            fail(422, "reason_required", "Supply a merchant-visible reason (1–2000 characters).")
        evidence = data.get("evidence", [])
        if (not isinstance(evidence, list) or len(evidence) > 20
                or any(not isinstance(item, str) or not item.strip() or len(item) > 2000 for item in evidence)):
            fail(422, "invalid_evidence", "Use up to 20 evidence references/notes (1–2000 characters each).")
        checks = data.get("checks", {})
        keys = {"business_identity_checked", "products_match_declaration", "sandbox_policy_checked"}
        if not isinstance(checks, dict) or checks.keys() - keys or any(type(v) is not bool for v in checks.values()):
            fail(422, "invalid_checks", "Supply supported boolean review checks.")
        if action == "approve" and (set(checks) != keys or not all(checks.values()) or not evidence):
            fail(422, "review_incomplete", "Approval requires all three checks and supporting evidence.")
        previous = a.status
        a.status, a.review_reason = target, reason.strip() or None
        record(a, action, previous, {"reason": a.review_reason, "checks": checks, "evidence": evidence})
        commit()
        return jsonify(serialize(a))

    @bp.get("/admin/applications/<application_id>/audit")
    def audit(application_id):
        admin()
        application_by_id(application_id)
        rows = db().scalars(select(AuditEvent).where(AuditEvent.application_id == application_id)
                            .order_by(AuditEvent.application_version, AuditEvent.created_at))
        return jsonify(items=[{
            "id": e.id, "actor_id": e.actor_id, "action": e.action,
            "from_status": e.from_status, "to_status": e.to_status,
            "application_version": e.application_version, "evidence": e.evidence,
            "created_at": timestamp(e.created_at),
        } for e in rows])

    app.register_blueprint(bp)
