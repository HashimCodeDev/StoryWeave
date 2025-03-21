# authenticate.py
from fastapi import HTTPException
from pydantic import BaseModel


# Dummy user database (Replace this with an actual database)
users_db = {"testuser": "password123"}

# User Model for Registration
class User(BaseModel):
    username: str
    password: str

def authenticate_user(username: str, password: str):
    """Verify if the provided username and password match a user in the database."""
    user = users_db.get(username)
    if user and user["password"] == password:
        return True
    return False

def register_user(user: User):
    """Register a new user."""
    if user.username in users_db:
        raise HTTPException(status_code=400, detail="Username already exists")
    users_db[user.username] = {"username": user.username, "password": user.password}
    return {"message": "User registered successfully"}