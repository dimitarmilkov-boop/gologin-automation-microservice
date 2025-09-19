from datetime import datetime
from sqlalchemy.orm import Session
from loguru import logger

from app.models import ApiKey

def verify_api_key(key: str, db: Session) -> bool:
    try:
        api_key = db.query(ApiKey).filter(
            ApiKey.key == key,
            ApiKey.is_active == True
        ).first()

        if api_key:
            api_key.last_used = datetime.utcnow()
            db.commit()
            return True

        return False

    except Exception as e:
        logger.error(f"API key verification error: {str(e)}")
        return False

def create_api_key(name: str, key: str, db: Session) -> ApiKey:
    api_key = ApiKey(
        key=key,
        name=name,
        is_active=True
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)
    return api_key