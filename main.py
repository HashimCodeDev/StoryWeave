from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Dict, List
import json
from transformers import pipeline
from pydantic import BaseModel
from authentication import authenticate_user, register_user, User, users_db
import os
from supabase import Client, create_client

# Initialize FastAPI app
app = FastAPI()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Ensure you have set these environment variables
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")

# Create the Supabase client
supabase: Client = create_client(url, key)

# Define the allowed origins and methods
origins = [
    "http://localhost",  # Frontend origin (adjust this to your actual frontend URL)
    "http://localhost:3000",
    "https://storyweave-frontend.vercel.app"  # If your frontend is running on port 3000 (example)
    "*",  # This allows all origins, but it's recommended to be more specific in production
]

# Add the CORSMiddleware to handle CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # List of allowed origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods (GET, POST, OPTIONS, etc.)
    allow_headers=["*"],  # Allow all headers
)

# Response model
class ResponseModel(BaseModel):
    message: str

class UserLogin(BaseModel):
    username: str
    password: str

# Load AI model for plot twists (GPT-2)
generator = pipeline("text-generation", model="gpt2")

# In-memory storage (replace with a database later)
story_data: Dict[str, str] = {}  # room_id -> story_text
addition_count: Dict[str, int] = {}  # room_id -> addition_count
current_twist: Dict[str, str] = {}  # room_id -> current_twist_id

@app.post("/register")
async def register_user(user: User):
    try:
        # Insert user into the 'users' table
        result = supabase.from_('users').insert({
            "username": user.username,
            "password": user.password
        }).execute()
        
        return {"message": "User registered successfully", "data": result.data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Login endpoint to check user credentials
@app.post("/login")
async def login(user: User):
    try:
        # Sign in the user using username and password
        result = supabase.from_('users').select('*').eq('username', user.username).eq('password', user.password).execute()

        if result.data:
            return {"message": "Login successful", "user": result.data[0]}
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/rooms")
async def get_rooms():
    try:
        # Fetch all rooms from the 'rooms' table
        result = supabase.from_('rooms').select('*').execute()
        print(result)
        return {"message": "Rooms found", "data": result.data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Manage WebSocket connections
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[tuple[WebSocket, str]]] = {}

    async def connect(self, websocket: WebSocket, room_id: str, username: str):
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = []
        self.active_connections[room_id].append((websocket, username))

    def disconnect(self, websocket: WebSocket, room_id: str):
        for conn in self.active_connections[room_id]:
            if conn[0] == websocket:
                self.active_connections[room_id].remove(conn)
                break
        if not self.active_connections[room_id]:
            del self.active_connections[room_id]

    async def broadcast(self, message: str, room_id: str):
        for connection, _ in self.active_connections.get(room_id, []):
            await connection.send_text(message)

manager = ConnectionManager()

# Helper functions
def get_story(room_id: str) -> str:
    return story_data.get(room_id, "")

def update_story(room_id: str, new_text: str):
    if room_id in story_data:
        story_data[room_id] += " " + new_text
    else:
        story_data[room_id] = new_text

def increment_addition_count(room_id: str) -> int:
    if room_id in addition_count:
        addition_count[room_id] += 1
    else:
        addition_count[room_id] = 1
    return addition_count[room_id]

def get_last_n_words(text: str, n: int) -> str:
    words = text.split()
    return " ".join(words[-n:]) if len(words) > n else text

def generate_plot_twist(story: str) -> str:
    prompt = get_last_n_words(story, 200) + " But then, something unexpected happened: "
    result = generator(prompt, max_length=100, num_return_sequences=1, do_sample=True)
    generated_text = result[0]["generated_text"]
    twist = generated_text[len(prompt):].strip().split(".")[0] + "."
    return twist

# WebSocket endpoint
@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, username: str):
    await manager.connect(websocket, room_id, username)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            if message["type"] == "add":
                new_text = message["text"]
                update_story(room_id, new_text)
                current_story = get_story(room_id)
                await manager.broadcast(
                    json.dumps({"type": "story_update", "story": current_story, "added_by": username}),
                    room_id
                )
                if increment_addition_count(room_id) % 5 == 0:
                    twist = generate_plot_twist(current_story)
                    update_story(room_id, twist)  # Automatically add the plot twist
                    current_story = get_story(room_id)
                    await manager.broadcast(
                        json.dumps({"type": "twist_accepted", "twist": twist}),
                        room_id
                    )
            elif message["type"] == "get_story":
                current_story = get_story(room_id)
                await websocket.send_text(json.dumps({"type": "story_update", "story": current_story}))
    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id)
        await manager.broadcast(
            json.dumps({"type": "user_left", "username": username}),
            room_id
        )
