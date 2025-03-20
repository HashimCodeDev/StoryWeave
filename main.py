from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import torch
from typing import Dict, List
import json
import uuid
import asyncio
from transformers import GPT2LMHeadModel, GPT2Tokenizer

# Initialize FastAPI app
app = FastAPI()

# Serve static files (for frontend)
app.mount("/static", StaticFiles(directory="static", html=True), name="static")

# Load model and tokenizer
model_name = "gpt2"
model = GPT2LMHeadModel.from_pretrained(model_name)
tokenizer = GPT2Tokenizer.from_pretrained(model_name)

# In-memory storage (replace with a database later)
story_data: Dict[str, str] = {}  # room_id -> story_text
addition_count: Dict[str, int] = {}  # room_id -> addition_count
current_twist: Dict[str, str] = {}  # room_id -> current_twist_id
twist_votes: Dict[str, Dict[str, set]] = {}  # room_id -> twist_id -> set of usernames who voted yes

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
    prompt = get_last_n_words(story, 50) + " But then, something unexpected happened:"
    result = generator(prompt, max_length=100, num_return_sequences=1, do_sample=True)
    generated_text = result[0]["generated_text"]
    twist = generated_text[len(prompt):].strip().split(".")[0] + "."
    return twist

# Handle voting for plot twists
async def handle_voting(room_id: str, twist_id: str, twist: str):
    await asyncio.sleep(30)  # Wait 30 seconds for votes
    yes_votes = len(twist_votes[room_id].get(twist_id, set()))
    total_users = len(manager.active_connections.get(room_id, []))
    if yes_votes > total_users / 2:
        update_story(room_id, twist)
        current_story = get_story(room_id)
        await manager.broadcast(
            json.dumps({"type": "story_update", "story": current_story, "twist_accepted": True}),
            room_id
        )
    else:
        await manager.broadcast(json.dumps({"type": "twist_rejected"}), room_id)
    # Clean up
    del current_twist[room_id]
    del twist_votes[room_id][twist_id]

# WebSocket endpoint
@app.websocket("/ws/{room_id}/{username}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, username: str):
    await manager.connect(websocket, room_id, username)  # Connect the user only once
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            if message["type"] == "add" and "text" in message:
                new_text = message["text"]
                update_story(room_id, new_text)
                current_story = get_story(room_id)
                await manager.broadcast(
                    json.dumps({"type": "story_update", "story": current_story, "added_by": username}),
                    room_id
                )
                if increment_addition_count(room_id) % 5 == 0:
                    twist = generate_plot_twist(current_story)
                    twist_id = str(uuid.uuid4())
                    current_twist[room_id] = twist_id
                    twist_votes[room_id] = {twist_id: set()}
                    await manager.broadcast(
                        json.dumps({"type": "twist_suggestion", "twist": twist, "twist_id": twist_id}),
                        room_id
                    )
                    asyncio.create_task(handle_voting(room_id, twist_id, twist))
            elif message["type"] == "vote" and "twist_id" in message:
                twist_id = message["twist_id"]
                if twist_id == current_twist.get(room_id) and username not in twist_votes[room_id][twist_id]:
                    if message["vote"] == "yes":
                        twist_votes[room_id][twist_id].add(username)
    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id)
        await manager.broadcast(
            json.dumps({"type": "user_left", "username": username}),
            room_id
        )