import secrets
import uuid

from ratis_core.models.referral import ReferralCode, ReferralUse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def get_by_code(db: Session, code: str) -> ReferralCode | None:
    return db.query(ReferralCode).filter(ReferralCode.code == code).first()


def create_for_user(db: Session, user_id: uuid.UUID) -> ReferralCode:
    for _ in range(10):
        code = secrets.token_hex(4).upper()
        try:
            nested = db.begin_nested()
            rc = ReferralCode(user_id=user_id, code=code, type="user")
            db.add(rc)
            db.flush()
            return rc
        except IntegrityError:
            nested.rollback()
    raise RuntimeError("referral_code_generation_failed")


def create_use(db: Session, referral_id: uuid.UUID, referred_user_id: uuid.UUID) -> ReferralUse:
    ru = ReferralUse(referral_id=referral_id, referred_user_id=referred_user_id)
    db.add(ru)
    db.flush()
    return ru
