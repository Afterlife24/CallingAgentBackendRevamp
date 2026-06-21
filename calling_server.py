"""
Flask HTTP server that exposes the outbound calling agent as a REST API.

Endpoints:
  POST /makeCall          — Initiate an outbound call
  GET  /callStatus/<id>   — Get the status of a call
  POST /updateCallStatus/<id> — Update call status (webhook / agent callback)
  GET  /health            — Health check

Run:
    python calling_server.py

Make sure agent.py is running first:
    python agent.py dev
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import uuid
import atexit
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from livekit import api

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv(dotenv_path=".env.local")

app = Flask(__name__)

CALL_HANDLER_DIR = Path(__file__).parent.resolve()
_venv_python = CALL_HANDLER_DIR.parent / ".venv" / "bin" / "python3"
PYTHON_EXECUTABLE = str(
    _venv_python) if _venv_python.exists() else sys.executable
logger.info("call_handler.py dir: %s", CALL_HANDLER_DIR)
logger.info("Python executable: %s", PYTHON_EXECUTABLE)

# {call_id: {status, phone_number, room_name, dispatch_id, timestamp}}
active_calls: dict = {}

# ---------------------------------------------------------------------------
# Signal / cleanup
# ---------------------------------------------------------------------------


def signal_handler(signum, frame) -> None:
    logger.info("Received signal %s — shutting down.", signum)
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
atexit.register(lambda: logger.info("Server cleanup completed."))

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

CORS(
    app,
    resources={
        r"/*": {
            "origins": "*",
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"],
            "supports_credentials": True,
        }
    },
    supports_credentials=True,
)


@app.after_request
def after_request(response):
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add(
        "Access-Control-Allow-Headers", "Content-Type,Authorization,X-Requested-With"
    )
    response.headers.add(
        "Access-Control-Allow-Methods", "GET,PUT,POST,DELETE,OPTIONS"
    )
    response.headers.add("Access-Control-Allow-Credentials", "true")
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/health")
def health_check():
    return jsonify({"status": "healthy", "service": "calling-agent-backend"}), 200


@app.route("/makeCall", methods=["POST"])
def make_call():
    """Initiate an outbound call by invoking call_handler.py as a subprocess."""
    data = request.get_json() or {}
    phone_number = data.get("phone_number", "").strip()

    if not phone_number:
        return jsonify({"error": "Phone number is required"}), 400

    if not phone_number.startswith("+"):
        return jsonify(
            {"error": "Phone number must be in E.164 format (e.g. +1234567890)"}
        ), 400

    logger.info("Initiating call to %s via call_handler.py", phone_number)
    call_id = str(uuid.uuid4())

    try:
        result = subprocess.run(
            [PYTHON_EXECUTABLE, "call_handler.py", phone_number],
            cwd=str(CALL_HANDLER_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )

        logger.info("call_handler.py stdout: %s", result.stdout)
        if result.stderr:
            logger.warning("call_handler.py stderr: %s", result.stderr)

        if result.returncode == 0:
            room_name = ""
            dispatch_id = ""
            for line in result.stdout.splitlines():
                if "Room:" in line:
                    room_name = line.split("Room:")[-1].strip()
                if "Dispatch ID:" in line:
                    dispatch_id = line.split("Dispatch ID:")[-1].strip()

            active_calls[call_id] = {
                "status": "connecting",
                "phone_number": phone_number,
                "room_name": room_name,
                "dispatch_id": dispatch_id,
                "timestamp": datetime.now().isoformat(),
            }

            return jsonify({
                "success": True,
                "call_id": call_id,
                "room_name": room_name,
                "dispatch_id": dispatch_id,
                "phone_number": phone_number,
                "message": "Call initiated. The agent will dial your number shortly.",
            }), 200
        else:
            error_msg = result.stderr or result.stdout or "call_handler.py failed"
            logger.error("call_handler.py failed: %s", error_msg)
            return jsonify({"success": False, "error": error_msg.strip()}), 500

    except subprocess.TimeoutExpired:
        logger.error("call_handler.py timed out")
        return jsonify({"error": "Call dispatch timed out"}), 504
    except Exception as e:
        logger.error("Error making outbound call: %s", str(e))
        return jsonify({"error": f"Failed to initiate call: {str(e)}"}), 500


@app.route("/callStatus/<call_id>", methods=["GET"])
def get_call_status(call_id: str):
    """Get live call status by querying the LiveKit room API."""
    if call_id not in active_calls:
        return jsonify({"status": "not_found", "message": "Call not found"}), 404

    call_info = active_calls[call_id]
    room_name = call_info["room_name"]

    try:
        async def _check_room() -> int:
            lkapi = api.LiveKitAPI(
                url=os.getenv("LIVEKIT_URL"),
                api_key=os.getenv("LIVEKIT_API_KEY"),
                api_secret=os.getenv("LIVEKIT_API_SECRET"),
            )
            try:
                resp = await lkapi.room.list_participants(
                    api.ListParticipantsRequest(room=room_name)
                )
                return len(resp.participants)
            finally:
                await lkapi.aclose()

        participant_count = asyncio.run(_check_room())
        logger.info("Room %s has %d participants",
                    room_name, participant_count)

        if participant_count >= 2:
            if call_info["status"] in ("connecting", "connected"):
                call_info["status"] = "connected"
        elif participant_count == 1:
            if call_info["status"] == "connected":
                call_info["status"] = "disconnected"
        else:
            call_info["status"] = "disconnected"

        active_calls[call_id] = call_info

    except Exception as e:
        logger.warning("Could not query LiveKit room status: %s", str(e))
        if "not found" in str(e).lower() or "does not exist" in str(e).lower():
            call_info["status"] = "disconnected"
            active_calls[call_id] = call_info

    return jsonify({
        "status": call_info["status"],
        "phone_number": call_info["phone_number"],
        "room_name": call_info["room_name"],
        "timestamp": call_info["timestamp"],
    }), 200


@app.route("/updateCallStatus/<call_id>", methods=["POST"])
def update_call_status(call_id: str):
    """Update call status — used by agent callbacks or webhooks."""
    data = request.get_json() or {}
    new_status = data.get("status", "").strip()

    if not new_status:
        return jsonify({"error": "Status is required"}), 400

    if call_id not in active_calls:
        return jsonify({"success": False, "message": "Call not found"}), 404

    active_calls[call_id]["status"] = new_status
    active_calls[call_id]["last_updated"] = datetime.now().isoformat()
    logger.info("Call %s status updated to: %s", call_id, new_status)

    return jsonify({"success": True, "call_id": call_id, "status": new_status}), 200


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info(
        "Starting Calling Agent Flask server on 0.0.0.0:5002\n"
        "Make sure agent.py is running: python agent.py dev"
    )
    try:
        app.run(host="0.0.0.0", port=5002, debug=True)
    except KeyboardInterrupt:
        logger.info("Server interrupted by user.")
    except Exception as e:
        logger.error("Server error: %s", e)
    finally:
        logger.info("Server shutdown complete.")
