"""Merchant onboarding module; authentication is supplied by the host application."""

from .api import Principal, init_verification, require_approved_merchant

__all__ = ["Principal", "init_verification", "require_approved_merchant"]
