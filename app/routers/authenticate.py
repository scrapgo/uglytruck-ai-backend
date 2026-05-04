from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.database import get_connection_for_authentication
from app.database.user import User
from app.database.auth import UserLogin, UserCreate
from app.database.security import create_access_token

router = APIRouter()

# ---------------------------
# Register
# ---------------------------
@router.post("/register")
def register(user_in: UserCreate, db: Session = Depends(get_connection_for_authentication)):

    existing = db.query(User).filter(User.email == user_in.email).first()
    if existing:
        return {"success": False, "message": "Email already in use"}

    new_user = User(
        email=user_in.email,
        full_name=user_in.full_name,
        hashed_password=User.hash_password(user_in.password)
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {"success": True, "message": "User registered successfully"}

# ---------------------------
# Login
# ---------------------------
from fastapi import HTTPException

@router.post("/login")
def login(user_in: UserLogin, db: Session = Depends(get_connection_for_authentication)):

    user = db.query(User).filter(User.email == user_in.email).first()

    if not user or not user.verify_password(user_in.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token({"sub": user.email})

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "is_active": user.is_active
        }
    }
