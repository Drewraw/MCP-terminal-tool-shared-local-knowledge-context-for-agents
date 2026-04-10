"""
User Manager — Freemium Tier with Firebase
============================================
Tracks users by Gmail account and enforces daily query limits.

Auth:    Firebase Auth (Google Sign-In)
Storage: Firestore (users collection)

Free tier: 50 prune queries per day per Gmail account.
Pro tier:  Unlimited (validated via license key).

Firestore structure:
  users/{email}
    ├── name: str
    ├── picture: str
    ├── tier: "free" | "pro"
    ├── license_key: str | null
    ├── queries_today: int
    ├── last_query_date: "YYYY-MM-DD"
    ├── total_queries: int
    ├── total_tokens_saved: int
    ├── created_at: timestamp
    └── last_seen: timestamp

Setup:
  1. Create Firebase project at console.firebase.google.com
  2. Enable Authentication → Google Sign-In
  3. Create Firestore database
  4. Download service account key → set GOOGLE_APPLICATION_CREDENTIALS env var
  5. Set GOOGLE_CLIENT_ID env var (from Firebase Console → Auth → Web client ID)
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional

# Firebase Admin SDK
try:
    import firebase_admin
    from firebase_admin import credentials, firestore, auth as firebase_auth
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False

# Daily query limit for free tier
FREE_TIER_DAILY_LIMIT = 50

# Firestore collection name
USERS_COLLECTION = "users"


class UserManager:
    """
    Manages user accounts via Firebase Auth + Firestore.

    Auth flow:
      1. VS Code extension → Google Sign-In → gets Firebase ID token
      2. Extension sends token to gateway /auth/verify
      3. Gateway verifies token with Firebase Admin SDK
      4. Returns user record + quota info

    Free tier: 50 queries/day per Gmail account.
    Pro tier:  Unlimited queries (license key validated).
    """

    def __init__(self, service_account_path: Optional[str] = None):
        """
        Initialize Firebase connection.

        Args:
            service_account_path: Path to Firebase service account JSON.
                                 If None, uses GOOGLE_APPLICATION_CREDENTIALS env var.
        """
        self.db = None

        if not FIREBASE_AVAILABLE:
            print("[user_manager] firebase-admin not installed. Auth disabled.")
            print("[user_manager] Install with: pip install firebase-admin")
            return

        try:
            # Initialize Firebase app (only once)
            if not firebase_admin._apps:
                if service_account_path and os.path.exists(service_account_path):
                    cred = credentials.Certificate(service_account_path)
                    firebase_admin.initialize_app(cred)
                elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
                    firebase_admin.initialize_app()
                else:
                    print("[user_manager] No Firebase credentials found. Auth disabled.")
                    print("[user_manager] Set GOOGLE_APPLICATION_CREDENTIALS or pass service_account_path")
                    return

            self.db = firestore.client()
            print("[user_manager] Firebase connected. Auth enabled.")
        except Exception as e:
            print(f"[user_manager] Firebase init failed: {e}")
            self.db = None

    @property
    def is_enabled(self) -> bool:
        return self.db is not None

    def verify_token(self, id_token: str) -> Optional[dict]:
        """
        Verify a Firebase ID token from the frontend.

        Args:
            id_token: The Firebase ID token (JWT) from Google Sign-In.

        Returns:
            Decoded token with uid, email, name, picture — or None if invalid.
        """
        if not FIREBASE_AVAILABLE or not self.db:
            return None

        try:
            decoded = firebase_auth.verify_id_token(id_token)
            return {
                "uid": decoded["uid"],
                "email": decoded.get("email", ""),
                "name": decoded.get("name", ""),
                "picture": decoded.get("picture", ""),
            }
        except Exception as e:
            print(f"[user_manager] Token verification failed: {e}")
            return None

    def login_or_register(self, email: str, name: str = "", picture: str = "") -> dict:
        """
        Register a new user or return existing one.
        Called after Firebase token verification.
        """
        if not self.db:
            return {"email": email, "tier": "free", "error": "Firebase not connected"}

        now = datetime.now(timezone.utc)
        doc_ref = self.db.collection(USERS_COLLECTION).document(email)
        doc = doc_ref.get()

        if doc.exists:
            # Existing user — update last_seen
            doc_ref.update({
                "last_seen": now,
                **({"name": name} if name else {}),
                **({"picture": picture} if picture else {}),
            })
            return doc.to_dict()

        # New user
        user_data = {
            "email": email,
            "name": name,
            "picture": picture,
            "tier": "free",
            "license_key": None,
            "queries_today": 0,
            "last_query_date": "",
            "total_queries": 0,
            "total_tokens_saved": 0,
            "created_at": now,
            "last_seen": now,
        }
        doc_ref.set(user_data)
        return user_data

    def check_quota(self, email: str) -> dict:
        """
        Check if user can make a query.

        Returns:
            {
                "allowed": True/False,
                "remaining": int (-1 for unlimited),
                "limit": int (-1 for unlimited),
                "tier": "free"/"pro",
                "reason": str (only if denied)
            }
        """
        if not self.db:
            # Firebase not connected — allow all (local/dev mode)
            return {"allowed": True, "remaining": -1, "limit": -1, "tier": "local"}

        doc_ref = self.db.collection(USERS_COLLECTION).document(email)
        doc = doc_ref.get()

        if not doc.exists:
            return {
                "allowed": False,
                "remaining": 0,
                "limit": 0,
                "tier": "unknown",
                "reason": "User not found. Please sign in.",
            }

        user = doc.to_dict()

        # Pro users — unlimited
        if user.get("tier") == "pro":
            return {"allowed": True, "remaining": -1, "limit": -1, "tier": "pro"}

        # Free tier — check daily limit
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        queries_today = user.get("queries_today", 0)

        # Reset counter if it's a new day
        if user.get("last_query_date") != today:
            queries_today = 0
            doc_ref.update({"queries_today": 0, "last_query_date": today})

        remaining = FREE_TIER_DAILY_LIMIT - queries_today
        allowed = remaining > 0

        result = {
            "allowed": allowed,
            "remaining": max(remaining, 0),
            "limit": FREE_TIER_DAILY_LIMIT,
            "tier": "free",
        }

        if not allowed:
            result["reason"] = (
                f"Daily limit reached ({FREE_TIER_DAILY_LIMIT} queries). "
                f"Upgrade to Pro for unlimited queries, or wait until midnight UTC."
            )

        return result

    def record_query(self, email: str, tokens_saved: int = 0):
        """Record that a user made a query. Call AFTER successful prune."""
        if not self.db:
            return

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        doc_ref = self.db.collection(USERS_COLLECTION).document(email)
        doc = doc_ref.get()

        if not doc.exists:
            return

        user = doc.to_dict()

        # Reset if new day
        if user.get("last_query_date") != today:
            queries_today = 1
        else:
            queries_today = user.get("queries_today", 0) + 1

        doc_ref.update({
            "queries_today": queries_today,
            "last_query_date": today,
            "total_queries": (user.get("total_queries", 0) + 1),
            "total_tokens_saved": (user.get("total_tokens_saved", 0) + tokens_saved),
            "last_seen": datetime.now(timezone.utc),
        })

    def activate_pro(self, email: str, license_key: str) -> bool:
        """
        Activate Pro tier for a user with a license key.

        In production, validate the key against Lemon Squeezy API:
          POST https://api.lemonsqueezy.com/v1/licenses/validate
          { "license_key": "PRUNE-XXXX-XXXX-XXXX" }
        """
        if not self.db:
            return False

        if not license_key or not license_key.strip():
            return False

        doc_ref = self.db.collection(USERS_COLLECTION).document(email)
        doc = doc_ref.get()

        if not doc.exists:
            return False

        # TODO: Validate against Lemon Squeezy API
        # import requests
        # resp = requests.post(
        #     "https://api.lemonsqueezy.com/v1/licenses/validate",
        #     json={"license_key": license_key}
        # )
        # if not resp.json().get("valid"):
        #     return False

        doc_ref.update({
            "tier": "pro",
            "license_key": license_key.strip(),
        })
        return True

    def get_user_stats(self, email: str) -> Optional[dict]:
        """Get usage stats for a user."""
        if not self.db:
            return None

        doc = self.db.collection(USERS_COLLECTION).document(email).get()
        if not doc.exists:
            return None

        user = doc.to_dict()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        queries_today = user.get("queries_today", 0) if user.get("last_query_date") == today else 0
        is_free = user.get("tier", "free") == "free"

        return {
            "email": user.get("email"),
            "name": user.get("name", ""),
            "picture": user.get("picture", ""),
            "tier": user.get("tier", "free"),
            "queries_today": queries_today,
            "daily_limit": FREE_TIER_DAILY_LIMIT if is_free else -1,
            "remaining_today": max(FREE_TIER_DAILY_LIMIT - queries_today, 0) if is_free else -1,
            "total_queries": user.get("total_queries", 0),
            "total_tokens_saved": user.get("total_tokens_saved", 0),
            "member_since": str(user.get("created_at", "")),
        }
