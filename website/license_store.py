from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.astimezone(timezone.utc).isoformat()


def _month_key(dt: datetime | None) -> str:
    value = dt or _utc_now()
    return value.strftime("%Y-%m")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _sha256(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _estimate_words_from_chars(chars: int) -> int:
    charge = max(0, _safe_int(chars, 0))
    if charge <= 0:
        return 0
    # Light fallback for live streams that report chars without raw transcript text.
    return max(1, int(round(charge / 5.0)))


@dataclass(frozen=True)
class PlanSpec:
    code: str
    quota_chars: int
    seat_limit: int
    cycle_type: str  # "monthly" | "lifetime"
    commercial: bool


DEFAULT_PLAN_SPECS: dict[str, PlanSpec] = {
    "starter": PlanSpec("starter", quota_chars=50_000, seat_limit=1, cycle_type="monthly", commercial=False),
    "pro": PlanSpec("pro", quota_chars=500_000, seat_limit=1, cycle_type="monthly", commercial=True),
    "team": PlanSpec("team", quota_chars=2_000_000, seat_limit=5, cycle_type="monthly", commercial=True),
    "lifetime": PlanSpec("lifetime", quota_chars=3_000_000, seat_limit=1, cycle_type="lifetime", commercial=True),
}


def _load_plan_specs() -> dict[str, PlanSpec]:
    raw = _normalize_text(os.getenv("SONUS_PLAN_CATALOG_JSON", ""))
    if not raw:
        return dict(DEFAULT_PLAN_SPECS)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return dict(DEFAULT_PLAN_SPECS)
    if not isinstance(parsed, dict):
        return dict(DEFAULT_PLAN_SPECS)

    merged = dict(DEFAULT_PLAN_SPECS)
    for code, payload in parsed.items():
        if not isinstance(payload, dict):
            continue
        plan_code = _normalize_text(code).lower()
        if not plan_code:
            continue
        merged[plan_code] = PlanSpec(
            code=plan_code,
            quota_chars=max(0, _safe_int(payload.get("quotaChars"), merged.get(plan_code, DEFAULT_PLAN_SPECS["starter"]).quota_chars)),
            seat_limit=max(1, _safe_int(payload.get("seatLimit"), merged.get(plan_code, DEFAULT_PLAN_SPECS["starter"]).seat_limit)),
            cycle_type="lifetime" if _normalize_text(payload.get("cycleType")).lower() == "lifetime" else "monthly",
            commercial=bool(payload.get("commercial", merged.get(plan_code, DEFAULT_PLAN_SPECS["starter"]).commercial)),
        )
    return merged


def _load_product_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    raw_json = _normalize_text(os.getenv("GUMROAD_PRODUCT_PLAN_MAP_JSON", ""))
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                for product_id, plan_code in parsed.items():
                    pid = _normalize_text(product_id)
                    code = _normalize_text(plan_code).lower()
                    if pid and code:
                        mapping[pid] = code
        except json.JSONDecodeError:
            pass

    env_slots = {
        "starter": os.getenv("GUMROAD_PRODUCT_ID_STARTER", ""),
        "pro": os.getenv("GUMROAD_PRODUCT_ID_PRO", ""),
        "team": os.getenv("GUMROAD_PRODUCT_ID_TEAM", ""),
        "lifetime": os.getenv("GUMROAD_PRODUCT_ID_LIFETIME", ""),
    }
    for code, product_id in env_slots.items():
        pid = _normalize_text(product_id)
        if pid:
            mapping[pid] = code
    return mapping


class MongoLicenseStore:
    def __init__(self) -> None:
        mongo_uri = _normalize_text(os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017"))
        db_name = _normalize_text(os.getenv("MONGODB_DB_NAME", "sonus"))
        self._client = MongoClient(mongo_uri, serverSelectionTimeoutMS=2500)
        self._db = self._client[db_name]
        self.licenses: Collection = self._db["licenses"]
        self.activations: Collection = self._db["license_activations"]
        self.usage: Collection = self._db["license_usage"]
        self.webhooks: Collection = self._db["gumroad_events"]
        self.plan_specs = _load_plan_specs()
        self.product_map = _load_product_map()
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        self.licenses.create_index([("licenseHash", ASCENDING)], unique=True)
        self.licenses.create_index([("saleId", ASCENDING)])
        self.licenses.create_index([("purchaseEmail", ASCENDING)])
        self.licenses.create_index([("status", ASCENDING)])
        self.licenses.create_index([("updatedAt", ASCENDING)])
        self.activations.create_index([("licenseId", ASCENDING), ("deviceHash", ASCENDING)], unique=True)
        self.activations.create_index([("licenseId", ASCENDING), ("revokedAt", ASCENDING)])
        self.usage.create_index([("licenseId", ASCENDING), ("idempotencyKey", ASCENDING)], unique=True)
        self.usage.create_index([("createdAt", ASCENDING)])
        self.webhooks.create_index([("eventKey", ASCENDING)], unique=True)

    def ping(self) -> bool:
        self._client.admin.command("ping")
        return True

    def resolve_plan(self, product_id: str, fallback: str = "starter") -> PlanSpec:
        code = self.product_map.get(_normalize_text(product_id), _normalize_text(fallback).lower() or "starter")
        return self.plan_specs.get(code, self.plan_specs["starter"])

    def hash_device(self, device_id: str) -> str:
        return _sha256(device_id)

    def hash_license(self, license_key: str) -> str:
        return _sha256(license_key)

    def _monthly_reset_if_needed(self, doc: dict[str, Any]) -> dict[str, Any]:
        if _normalize_text(doc.get("cycleType")) != "monthly":
            return doc
        now = _utc_now()
        active_cycle = _normalize_text(doc.get("cycleKey"))
        current_cycle = _month_key(now)
        if active_cycle == current_cycle:
            return doc
        updated = self.licenses.find_one_and_update(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "cycleKey": current_cycle,
                    "usedChars": 0,
                    "usedWords": 0,
                    "cycleStartedAt": now,
                    "updatedAt": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return updated or doc

    def build_entitlement(self, doc: dict[str, Any]) -> dict[str, Any]:
        doc = self._monthly_reset_if_needed(doc)
        used_chars = max(0, _safe_int(doc.get("usedChars"), 0))
        used_words = max(0, _safe_int(doc.get("usedWords"), 0))
        quota_chars = max(0, _safe_int(doc.get("quotaChars"), 0))
        bonus_chars = max(0, _safe_int(doc.get("bonusChars"), 0))
        total_chars = quota_chars + bonus_chars
        remaining_chars = max(0, total_chars - used_chars)
        seats = self.activations.count_documents({"licenseId": doc["_id"], "revokedAt": None})
        seat_limit = max(1, _safe_int(doc.get("seatLimit"), 1))
        status = _normalize_text(doc.get("status")).lower() or "active"
        seat_limit_reached = seats > seat_limit
        # Existing activated seats can continue; limit applies to additional activations.
        can_transcribe = status == "active" and remaining_chars > 0
        return {
            "licenseId": str(doc["_id"]),
            "status": status,
            "plan": _normalize_text(doc.get("planCode")).lower() or "starter",
            "isSubscription": bool(doc.get("isSubscription", False)),
            "quotaChars": quota_chars,
            "bonusChars": bonus_chars,
            "usedChars": used_chars,
            "usedWords": used_words,
            "remainingChars": remaining_chars,
            "seatLimit": seat_limit,
            "activeSeats": int(seats),
            "seatLimitReached": bool(seat_limit_reached),
            "cycleType": _normalize_text(doc.get("cycleType")) or "monthly",
            "cycleKey": _normalize_text(doc.get("cycleKey")) or _month_key(_utc_now()),
            "commercial": bool(doc.get("commercial", False)),
            "lastVerifiedAt": _iso(doc.get("lastVerifiedAt")),
            "updatedAt": _iso(doc.get("updatedAt")),
            "canTranscribe": bool(can_transcribe),
            "purchaseEmail": _normalize_text(doc.get("purchaseEmail")),
            "saleId": _normalize_text(doc.get("saleId")),
        }

    def activate_from_purchase(
        self,
        license_key: str,
        product_id: str,
        device_id: str,
        purchase: dict[str, Any],
        device_name: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        now = _utc_now()
        license_hash = self.hash_license(license_key)
        device_hash = self.hash_device(device_id)
        plan = self.resolve_plan(product_id)

        subscription_stopped = bool(
            purchase.get("subscription_cancelled_at") or purchase.get("subscription_ended_at")
        )
        status = "inactive" if subscription_stopped else "active"

        upsert_fields = {
            "licenseHash": license_hash,
            "licenseHint": f"***{_normalize_text(license_key)[-4:]}",
            "purchaseEmail": _normalize_text(purchase.get("email")),
            "saleId": _normalize_text(purchase.get("sale_id")),
            "productId": _normalize_text(product_id),
            "planCode": plan.code,
            "quotaChars": int(plan.quota_chars),
            "seatLimit": int(plan.seat_limit),
            "cycleType": plan.cycle_type,
            "commercial": bool(plan.commercial),
            "status": status,
            "isSubscription": bool(purchase.get("subscription_id")),
            "subscriptionEndedAt": _normalize_text(purchase.get("subscription_ended_at")),
            "subscriptionCancelledAt": _normalize_text(purchase.get("subscription_cancelled_at")),
            "lastVerifiedAt": now,
            "updatedAt": now,
        }
        create_fields = {
            "createdAt": now,
            "usedChars": 0,
            "usedWords": 0,
            "bonusChars": 0,
            "cycleKey": _month_key(now),
            "cycleStartedAt": now,
            "revokedAt": None,
            "revokedReason": "",
        }
        self.licenses.update_one(
            {"licenseHash": license_hash},
            {"$set": upsert_fields, "$setOnInsert": create_fields},
            upsert=True,
        )
        doc = self.licenses.find_one({"licenseHash": license_hash})
        if not doc:
            return None, "license_persist_failed"

        if _normalize_text(doc.get("revokedAt")):
            return None, "license_revoked"
        if _normalize_text(doc.get("status")).lower() != "active":
            return None, "license_inactive"

        active_count = self.activations.count_documents({"licenseId": doc["_id"], "revokedAt": None})
        existing_activation = self.activations.find_one({"licenseId": doc["_id"], "deviceHash": device_hash})
        if not existing_activation and active_count >= int(doc.get("seatLimit") or 1):
            return None, "seat_limit_reached"

        self.activations.update_one(
            {"licenseId": doc["_id"], "deviceHash": device_hash},
            {
                "$set": {
                    "deviceLabel": _normalize_text(device_name)[:120],
                    "lastSeenAt": now,
                    "revokedAt": None,
                    "updatedAt": now,
                },
                "$setOnInsert": {
                    "activatedAt": now,
                },
            },
            upsert=True,
        )
        doc = self._monthly_reset_if_needed(doc)
        return self.build_entitlement(doc), ""

    def get_license_by_id(self, license_id: str) -> dict[str, Any] | None:
        from bson import ObjectId

        try:
            object_id = ObjectId(license_id)
        except Exception:
            return None
        doc = self.licenses.find_one({"_id": object_id})
        if not doc:
            return None
        return self._monthly_reset_if_needed(doc)

    def touch_activation(self, license_id: str, device_id: str, device_name: str = "") -> bool:
        doc = self.get_license_by_id(license_id)
        if not doc:
            return False
        now = _utc_now()
        device_hash = self.hash_device(device_id)
        self.activations.update_one(
            {"licenseId": doc["_id"], "deviceHash": device_hash},
            {
                "$set": {
                    "lastSeenAt": now,
                    "updatedAt": now,
                    "deviceLabel": _normalize_text(device_name)[:120],
                    "revokedAt": None,
                },
                "$setOnInsert": {"activatedAt": now},
            },
            upsert=True,
        )
        self.licenses.update_one({"_id": doc["_id"]}, {"$set": {"updatedAt": now}})
        return True

    def consume_chars(
        self,
        license_id: str,
        chars_used: int,
        words_used: int,
        mode: str,
        session_id: str,
        idempotency_key: str,
        detail: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        charge = max(0, _safe_int(chars_used, 0))
        words_charge = max(0, _safe_int(words_used, 0))
        if charge > 0 and words_charge <= 0:
            words_charge = _estimate_words_from_chars(charge)
        if charge <= 0:
            doc = self.get_license_by_id(license_id)
            return (self.build_entitlement(doc), "") if doc else (None, "license_not_found")

        doc = self.get_license_by_id(license_id)
        if not doc:
            return None, "license_not_found"

        entitlement = self.build_entitlement(doc)
        if entitlement["status"] != "active":
            return None, "license_inactive"
        if charge > int(entitlement["remainingChars"]):
            return None, "quota_exceeded"

        now = _utc_now()
        usage_key = _normalize_text(idempotency_key)[:160]
        usage_doc = {
            "licenseId": doc["_id"],
            "charsUsed": charge,
            "wordsUsed": words_charge,
            "mode": _normalize_text(mode).lower()[:32],
            "sessionId": _normalize_text(session_id)[:96],
            "idempotencyKey": usage_key,
            "detail": _normalize_text(detail)[:240],
            "createdAt": now,
        }
        try:
            inserted = self.usage.insert_one(usage_doc)
        except DuplicateKeyError:
            refreshed = self.get_license_by_id(license_id)
            return (self.build_entitlement(refreshed), "") if refreshed else (None, "license_not_found")

        total_chars = int(entitlement["quotaChars"]) + int(entitlement["bonusChars"])
        max_used_before = max(0, total_chars - charge)
        update_filter: dict[str, Any] = {
            "_id": doc["_id"],
            "status": "active",
            "revokedAt": None,
            "usedChars": {"$lte": max_used_before},
        }
        if _normalize_text(doc.get("cycleType")) == "monthly":
            update_filter["cycleKey"] = entitlement["cycleKey"]
        updated = self.licenses.find_one_and_update(
            update_filter,
            {"$inc": {"usedChars": charge, "usedWords": words_charge}, "$set": {"updatedAt": now}},
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            self.usage.delete_one({"_id": inserted.inserted_id})
            refreshed = self.get_license_by_id(license_id)
            if not refreshed:
                return None, "license_not_found"
            refreshed_entitlement = self.build_entitlement(refreshed)
            if refreshed_entitlement["status"] != "active":
                return None, "license_inactive"
            if charge > int(refreshed_entitlement["remainingChars"]):
                return None, "quota_exceeded"
            return None, "usage_conflict"
        return self.build_entitlement(updated), ""

    def list_licenses(self, limit: int = 100, query: str = "") -> list[dict[str, Any]]:
        clean_query = _normalize_text(query).lower()
        mongo_query: dict[str, Any] = {}
        if clean_query:
            mongo_query = {
                "$or": [
                    {"purchaseEmail": {"$regex": clean_query, "$options": "i"}},
                    {"saleId": {"$regex": clean_query, "$options": "i"}},
                    {"licenseHint": {"$regex": clean_query, "$options": "i"}},
                    {"planCode": {"$regex": clean_query, "$options": "i"}},
                ]
            }
        docs = list(
            self.licenses.find(mongo_query).sort("updatedAt", -1).limit(max(10, min(500, _safe_int(limit, 100))))
        )
        rows: list[dict[str, Any]] = []
        for doc in docs:
            ent = self.build_entitlement(doc)
            rows.append(
                {
                    "id": str(doc["_id"]),
                    "licenseHint": _normalize_text(doc.get("licenseHint")),
                    "purchaseEmail": _normalize_text(doc.get("purchaseEmail")),
                    "saleId": _normalize_text(doc.get("saleId")),
                    "plan": ent["plan"],
                    "status": ent["status"],
                    "usedChars": ent["usedChars"],
                    "usedWords": ent["usedWords"],
                    "quotaChars": ent["quotaChars"],
                    "bonusChars": ent["bonusChars"],
                    "remainingChars": ent["remainingChars"],
                    "activeSeats": ent["activeSeats"],
                    "seatLimit": ent["seatLimit"],
                    "updatedAt": ent["updatedAt"],
                    "lastVerifiedAt": ent["lastVerifiedAt"],
                }
            )
        return rows

    def dashboard_summary(self) -> dict[str, Any]:
        total_licenses = self.licenses.count_documents({})
        active_licenses = self.licenses.count_documents({"status": "active", "revokedAt": None})
        revoked_licenses = self.licenses.count_documents({"revokedAt": {"$ne": None}})
        active_devices = self.activations.count_documents({"revokedAt": None})

        usage_pipeline = [
            {
                "$group": {
                    "_id": None,
                    "totalChars": {"$sum": "$charsUsed"},
                    "totalWords": {"$sum": "$wordsUsed"},
                }
            },
        ]
        usage_total_chars = 0
        usage_total_words = 0
        usage_result = list(self.usage.aggregate(usage_pipeline))
        if usage_result:
            usage_total_chars = _safe_int(usage_result[0].get("totalChars"), 0)
            usage_total_words = _safe_int(usage_result[0].get("totalWords"), 0)

        return {
            "totalLicenses": int(total_licenses),
            "activeLicenses": int(active_licenses),
            "revokedLicenses": int(revoked_licenses),
            "activeDevices": int(active_devices),
            "totalCharsUsed": int(usage_total_chars),
            "totalWordsUsed": int(usage_total_words),
        }

    def set_revoke_state(self, license_id: str, revoked: bool, reason: str = "") -> bool:
        doc = self.get_license_by_id(license_id)
        if not doc:
            return False
        now = _utc_now()
        fields: dict[str, Any] = {"updatedAt": now}
        if revoked:
            fields["revokedAt"] = now.isoformat()
            fields["revokedReason"] = _normalize_text(reason)[:240] or "admin_revoke"
            fields["status"] = "revoked"
        else:
            fields["revokedAt"] = None
            fields["revokedReason"] = ""
            if _normalize_text(doc.get("subscriptionCancelledAt")) or _normalize_text(doc.get("subscriptionEndedAt")):
                fields["status"] = "inactive"
            else:
                fields["status"] = "active"
        self.licenses.update_one({"_id": doc["_id"]}, {"$set": fields})
        return True

    def top_up_chars(self, license_id: str, amount: int) -> bool:
        doc = self.get_license_by_id(license_id)
        if not doc:
            return False
        delta = max(0, _safe_int(amount, 0))
        if delta <= 0:
            return False
        self.licenses.update_one(
            {"_id": doc["_id"]},
            {"$inc": {"bonusChars": delta}, "$set": {"updatedAt": _utc_now()}},
        )
        return True

    def record_webhook_event(self, event_key: str, event_type: str, payload: dict[str, Any]) -> bool:
        now = _utc_now()
        key = _normalize_text(event_key) or _sha256(json.dumps(payload, sort_keys=True))
        try:
            self.webhooks.insert_one(
                {
                    "eventKey": key,
                    "eventType": _normalize_text(event_type).lower()[:80],
                    "receivedAt": now,
                    "payload": payload,
                }
            )
            return True
        except DuplicateKeyError:
            return False

    def apply_webhook_purchase_update(
        self,
        payload: dict[str, Any],
        event_type: str = "",
        fallback_status: str = "active",
    ) -> int:
        if not isinstance(payload, dict):
            return 0

        now = _utc_now()
        clean_event = _normalize_text(event_type).lower()
        clean_status = _normalize_text(payload.get("status") or fallback_status).lower() or "active"
        if clean_status in {"cancelled", "canceled", "ended", "refunded", "refund", "chargeback", "disputed"}:
            clean_status = "inactive"
        if clean_status not in {"active", "inactive", "revoked"}:
            clean_status = "active"
        if bool(payload.get("refunded")) or bool(payload.get("chargebacked")):
            clean_status = "inactive"
        if any(item in clean_event for item in ("refund", "chargeback", "cancel", "ended", "dispute")):
            clean_status = "inactive"
        if "revoke" in clean_event:
            clean_status = "revoked"

        purchase_payload = payload.get("purchase") if isinstance(payload.get("purchase"), dict) else {}
        sale_id = _normalize_text(
            payload.get("sale_id") or payload.get("saleId") or purchase_payload.get("sale_id")
        )
        license_key = _normalize_text(
            payload.get("license_key") or payload.get("licenseKey") or purchase_payload.get("license_key")
        )
        product_id = _normalize_text(
            payload.get("product_id") or payload.get("productId") or purchase_payload.get("product_id")
        )
        purchase_email = _normalize_text(
            payload.get("email")
            or payload.get("purchase_email")
            or payload.get("purchaseEmail")
            or purchase_payload.get("email")
        )
        subscription_id = _normalize_text(
            payload.get("subscription_id") or payload.get("subscriptionId") or purchase_payload.get("subscription_id")
        )
        subscription_ended_at = _normalize_text(
            payload.get("subscription_ended_at")
            or payload.get("subscriptionEndedAt")
            or purchase_payload.get("subscription_ended_at")
        )
        subscription_cancelled_at = _normalize_text(
            payload.get("subscription_cancelled_at")
            or payload.get("subscriptionCancelledAt")
            or purchase_payload.get("subscription_cancelled_at")
        )
        if subscription_cancelled_at or subscription_ended_at:
            clean_status = "inactive"

        touched = 0
        license_hash = self.hash_license(license_key) if license_key else ""
        plan: PlanSpec | None = None
        if product_id:
            plan = self.resolve_plan(product_id)

        has_subscription_id = any(
            key in payload or key in purchase_payload for key in ("subscription_id", "subscriptionId")
        )
        has_subscription_ended = any(
            key in payload or key in purchase_payload for key in ("subscription_ended_at", "subscriptionEndedAt")
        )
        has_subscription_cancelled = any(
            key in payload or key in purchase_payload
            for key in ("subscription_cancelled_at", "subscriptionCancelledAt")
        )

        shared_updates: dict[str, Any] = {
            "status": clean_status,
            "updatedAt": now,
            "lastVerifiedAt": now,
        }
        if has_subscription_id:
            shared_updates["isSubscription"] = bool(subscription_id)
        if has_subscription_ended:
            shared_updates["subscriptionEndedAt"] = subscription_ended_at
        if has_subscription_cancelled:
            shared_updates["subscriptionCancelledAt"] = subscription_cancelled_at
        if sale_id:
            shared_updates["saleId"] = sale_id
        if purchase_email:
            shared_updates["purchaseEmail"] = purchase_email
        if product_id:
            shared_updates["productId"] = product_id
        if plan:
            shared_updates["planCode"] = plan.code
            shared_updates["quotaChars"] = int(plan.quota_chars)
            shared_updates["seatLimit"] = int(plan.seat_limit)
            shared_updates["cycleType"] = plan.cycle_type
            shared_updates["commercial"] = bool(plan.commercial)

        if license_hash:
            create_fields = {
                "createdAt": now,
                "usedChars": 0,
                "usedWords": 0,
                "bonusChars": 0,
                "cycleKey": _month_key(now),
                "cycleStartedAt": now,
                "revokedAt": None,
                "revokedReason": "",
                "licenseHint": f"***{license_key[-4:]}",
                "licenseHash": license_hash,
            }
            one_result = self.licenses.update_one(
                {"licenseHash": license_hash},
                {"$set": shared_updates, "$setOnInsert": create_fields},
                upsert=True,
            )
            touched += int(one_result.matched_count) + (1 if one_result.upserted_id else 0)

        if sale_id:
            extra_filter: dict[str, Any] = {"saleId": sale_id}
            if license_hash:
                extra_filter["licenseHash"] = {"$ne": license_hash}
            many_result = self.licenses.update_many(extra_filter, {"$set": shared_updates})
            touched += int(many_result.matched_count)

        return touched

    def apply_webhook_status_update(self, sale_id: str, status: str) -> int:
        return self.apply_webhook_purchase_update(
            payload={"sale_id": sale_id, "status": status},
            event_type="status_sync",
            fallback_status=status,
        )
