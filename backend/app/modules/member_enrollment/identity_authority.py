from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from uuid import UUID

from sqlalchemy import text

from app.modules.auth.identity_submission_crypto import (
    EncryptedIdentityValue,
    IdentitySubmissionCrypto,
    IdentitySubmissionCryptoUnavailable,
)


_PRC_ID_RE = re.compile(r"^[0-9]{17}[0-9X]$", re.ASCII)
_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_CHECK_CODES = "10X98765432"


def parse_prc_resident_identity_birth_date(value: str) -> date:
    if type(value) is not str or _PRC_ID_RE.fullmatch(value) is None:
        raise ValueError("IDENTITY_DOCUMENT_INVALID")
    expected = _CHECK_CODES[
        sum(int(digit) * weight for digit, weight in zip(value[:17], _WEIGHTS)) % 11
    ]
    if value[-1] != expected:
        raise ValueError("IDENTITY_DOCUMENT_INVALID")
    try:
        return date(int(value[6:10]), int(value[10:12]), int(value[12:14]))
    except ValueError:
        raise ValueError("IDENTITY_DOCUMENT_INVALID") from None


def verified_adult_on(birth_date: date, as_of: date) -> bool:
    if type(birth_date) is not date or type(as_of) is not date:
        raise ValueError("IDENTITY_DOCUMENT_INVALID")
    eighteenth = date(
        birth_date.year + 18,
        birth_date.month,
        min(
            birth_date.day,
            28 if birth_date.month == 2 and birth_date.day == 29 else birth_date.day,
        ),
    )
    return as_of >= eighteenth


@dataclass(frozen=True, slots=True)
class VerifiedAdultEvidence:
    eligible: bool
    as_of_date: date
    submission_id: UUID
    decision_ref: UUID
    facts_version: int
    evidence_digest: str


async def verified_adult_eligibility_for_update(
    session,
    *,
    user_id: int,
    member_id: UUID,
    as_of_date: date,
) -> VerifiedAdultEvidence:
    """Read the current P1 authority under shared locks and expose no PII."""
    result = await session.execute(
        text(
            "SELECT s.submission_id,s.user_ref,s.version,s.id_card_ciphertext,"
            "s.id_card_nonce,s.encryption_key_id,e.decision_ref,e.facts_version,"
            "e.facts_digest FROM identity.user_member_self_link l "
            "JOIN identity.member m ON m.member_id=l.member_id "
            "JOIN public.identity_verification_submission s ON s.user_ref=l.user_ref "
            "JOIN LATERAL (SELECT d.decision_ref,d.facts_version,d.facts_digest "
            "FROM public.registration_eligibility_decision d "
            "JOIN public.identity_verification_decision v "
            "ON v.decision_ref=d.verification_decision_ref "
            "WHERE d.user_ref=l.user_ref AND d.decision='eligible' "
            "AND v.user_ref=l.user_ref AND v.outcome='verified' "
            "ORDER BY d.facts_version DESC,d.decided_at DESC LIMIT 1) e ON true "
            "WHERE l.user_ref=:user_id AND l.member_id=:member_id "
            "AND m.status='created' AND s.status='verified' "
            "ORDER BY s.version DESC LIMIT 1 FOR SHARE OF l,m,s"
        ),
        {"user_id": user_id, "member_id": member_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ValueError("PROXY_ADULT_IDENTITY_REQUIRED")
    try:
        crypto = IdentitySubmissionCrypto.from_environment()
        if row["encryption_key_id"] != crypto.key_id:
            raise IdentitySubmissionCryptoUnavailable("authority key unavailable")
        plaintext = crypto.decrypt(
            EncryptedIdentityValue(
                ciphertext=bytes(row["id_card_ciphertext"]),
                nonce=bytes(row["id_card_nonce"]),
            ),
            aad=crypto.aad(
                submission_id=str(row["submission_id"]),
                user_ref=row["user_ref"],
                version=row["version"],
                field="id_card",
                key_id=row["encryption_key_id"],
            ),
        )
        birth_date = parse_prc_resident_identity_birth_date(plaintext)
    except Exception:
        raise ValueError("PROXY_ADULT_IDENTITY_REQUIRED") from None
    decision_ref = UUID(str(row["decision_ref"]))
    submission_id = UUID(str(row["submission_id"]))
    evidence = sha256(
        f"slice3-adult:v1:{submission_id}:{decision_ref}:{row['facts_version']}".encode()
    ).hexdigest()
    return VerifiedAdultEvidence(
        eligible=verified_adult_on(birth_date, as_of_date),
        as_of_date=as_of_date,
        submission_id=submission_id,
        decision_ref=decision_ref,
        facts_version=row["facts_version"],
        evidence_digest=evidence,
    )
