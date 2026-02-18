import asyncio
from typing import List, Dict, Set
from fastapi import WebSocket, WebSocketDisconnect

class ConnectionManager:
    """
    Manages WebSocket connections for real-time updates.
    """
    def __init__(self):
        # active_connections: List[WebSocket] = []
        # We might want to group connections by type (e.g. task list watchers vs specific task log watchers)
        # For now, simple broadcast for task list, and specific room for logs.
        
        # Room: "task_list" -> Set[WebSocket]
        # Room: "task_logs:{task_id}" -> Set[WebSocket]
        self.rooms: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room: str):
        await websocket.accept()
        if room not in self.rooms:
            self.rooms[room] = set()
        self.rooms[room].add(websocket)
        print(f"Client connected to room: {room}. Total in room: {len(self.rooms[room])}")

    def disconnect(self, websocket: WebSocket, room: str):
        if room in self.rooms:
            if websocket in self.rooms[room]:
                self.rooms[room].remove(websocket)
            if not self.rooms[room]:
                del self.rooms[room]
        print(f"Client disconnected from room: {room}")

    async def broadcast(self, room: str, message: dict):
        if room in self.rooms:
            # Create a list copy to avoid "Set changed size during iteration"
            for connection in list(self.rooms[room]):
                try:
                    await connection.send_json(message)
                except Exception as e:
                    print(f"Error broadcasting to {connection}: {e}")
                    # Optionally remove dead connection here, but disconnect() should handle it via exception in endpoint
    
    async def broadcast_log(self, task_id: str, log_line: str):
        """Helper to broadcast a new log line to watchers"""
        room = f"task_logs:{task_id}"
        await self.broadcast(room, {"type": "log", "data": log_line})

manager = ConnectionManager()
