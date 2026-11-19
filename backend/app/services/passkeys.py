"""WebAuthn ceremonies (py_webauthn), with challenges held in Redis.

- Discoverable credentials only (``residentKey: required``) so login is usernameless.
- ``userVerification: required`` on every ceremony, and verified server side.
- Attestation ``none``: we trust the user's choice of authenticator, not a vendor list.
- Challenges are 64 random bytes, single use (GETDEL), 5-minute TTL, and bound to their purpose
  (and, for registration and step-up, to the session that asked for them).
- Sign counts must increase when the authenticator keeps one (py_webauthn enforces it).
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from redis.asyncio import Redis
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    CredentialDeviceType,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.config import Settings
from app.logging import get_logger

CHALLENGE_TTL_SECONDS = 300
RP_NAME = "Holdfast"
Purpose = Literal["register", "login", "reauth"]

log = get_logger(__name__)


class VerificationFailed(Exception):
    """Any WebAuthn failure. Deliberately carries no detail for the client."""


@dataclass(frozen=True)
class NewCredential:
    credential_id: bytes
    public_key: bytes
    sign_count: int
    transports: list[str]
    aaguid: uuid.UUID | None
    backup_eligible: bool
    backup_state: bool


@dataclass(frozen=True)
class Assertion:
    credential_id: bytes
    new_sign_count: int
    backup_state: bool


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _key(purpose: Purpose, challenge_b64: str) -> str:
    return f"hf:wa:{purpose}:{challenge_b64}"


def credential_id_of(credential: dict[str, Any]) -> bytes:
    raw = credential.get("rawId") or credential.get("id")
    if not isinstance(raw, str) or not raw or len(raw) > 1024:
        raise VerificationFailed
    try:
        return _b64url_decode(raw)
    except (binascii.Error, ValueError):
        raise VerificationFailed from None


def user_handle_of(credential: dict[str, Any]) -> bytes | None:
    response = credential.get("response")
    if not isinstance(response, dict):
        raise VerificationFailed
    handle = response.get("userHandle")
    if handle is None or handle == "":
        return None
    if not isinstance(handle, str):
        raise VerificationFailed
    try:
        return _b64url_decode(handle)
    except (binascii.Error, ValueError):
        raise VerificationFailed from None


def _challenge_of(credential: dict[str, Any]) -> str:
    """The challenge the client signed, read from clientDataJSON (verified again later)."""
    try:
        response = credential["response"]
        client_data = json.loads(_b64url_decode(response["clientDataJSON"]))
        challenge = client_data["challenge"]
    except (KeyError, TypeError, ValueError, binascii.Error):
        raise VerificationFailed from None
    if not isinstance(challenge, str) or not challenge or len(challenge) > 256:
        raise VerificationFailed
    return challenge


class WebAuthnService:
    def __init__(self, settings: Settings, redis: Redis) -> None:
        self.rp_id = settings.app_host
        self.origin = settings.expected_origin
        self.redis = redis

    async def _store(self, purpose: Purpose, challenge: bytes, bound_to: str) -> None:
        await self.redis.set(
            _key(purpose, bytes_to_base64url(challenge)), bound_to, ex=CHALLENGE_TTL_SECONDS
        )

    async def _take(self, purpose: Purpose, credential: dict[str, Any], bound_to: str) -> bytes:
        challenge = _challenge_of(credential)
        stored = await self.redis.getdel(_key(purpose, challenge))
        if stored is None:
            raise VerificationFailed
        value = stored.decode() if isinstance(stored, bytes) else str(stored)
        if value != bound_to:
            raise VerificationFailed
        return _b64url_decode(challenge)

    # ------------------------------------------------------------------ registration

    async def registration_options(
        self,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        user_name: str,
        display_name: str,
        existing: list[bytes],
    ) -> dict[str, Any]:
        options = generate_registration_options(
            rp_id=self.rp_id,
            rp_name=RP_NAME,
            user_id=user_id.bytes,
            user_name=user_name,
            user_display_name=display_name,
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                require_resident_key=True,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=c) for c in existing],
        )
        await self._store("register", options.challenge, str(session_id))
        result: dict[str, Any] = json.loads(options_to_json(options))
        return result

    async def verify_registration(
        self, *, session_id: uuid.UUID, credential: dict[str, Any]
    ) -> NewCredential:
        challenge = await self._take("register", credential, str(session_id))
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                require_user_verification=True,
            )
        except Exception as exc:
            log.info("webauthn.registration_rejected", error_type=type(exc).__name__)
            raise VerificationFailed from None
        response = credential.get("response")
        transports_raw = response.get("transports") if isinstance(response, dict) else None
        transports = (
            [t for t in transports_raw if isinstance(t, str)][:8]
            if isinstance(transports_raw, list)
            else []
        )
        try:
            aaguid: uuid.UUID | None = uuid.UUID(verified.aaguid)
        except ValueError:
            aaguid = None
        return NewCredential(
            credential_id=verified.credential_id,
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
            transports=transports,
            aaguid=aaguid,
            backup_eligible=verified.credential_device_type == CredentialDeviceType.MULTI_DEVICE,
            backup_state=verified.credential_backed_up,
        )

    # ------------------------------------------------------------------ authentication

    async def authentication_options(
        self, purpose: Literal["login", "reauth"], *, bound_to: str, allow: list[bytes]
    ) -> dict[str, Any]:
        options = generate_authentication_options(
            rp_id=self.rp_id,
            user_verification=UserVerificationRequirement.REQUIRED,
            allow_credentials=[PublicKeyCredentialDescriptor(id=c) for c in allow],
        )
        await self._store(purpose, options.challenge, bound_to)
        result: dict[str, Any] = json.loads(options_to_json(options))
        return result

    async def take_challenge(
        self, purpose: Literal["login", "reauth"], credential: dict[str, Any], bound_to: str
    ) -> bytes:
        return await self._take(purpose, credential, bound_to)

    def verify_assertion(
        self,
        *,
        credential: dict[str, Any],
        challenge: bytes,
        public_key: bytes,
        sign_count: int,
    ) -> Assertion:
        try:
            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                credential_public_key=public_key,
                credential_current_sign_count=sign_count,
                require_user_verification=True,
            )
        except Exception as exc:
            log.info("webauthn.assertion_rejected", error_type=type(exc).__name__)
            raise VerificationFailed from None
        return Assertion(
            credential_id=verified.credential_id,
            new_sign_count=verified.new_sign_count,
            backup_state=verified.credential_backed_up,
        )
