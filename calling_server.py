import os
import logging
import signal
import sys
import subprocess
import atexit
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from datetime import datetime
import uuid
from livekit import api
from pathlib import Path

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv(dotenv_path=".env.local")

app = Flask(__name__)

# Global variable to track server state
server_running = True

# Initialize paths
CALL_HANDLER_DIR = Path(__file__).parent.resolve()
# Use the venv python explicitly to avoid Flask reloader issues
_venv_python = CALL_HANDLER_DIR.parent / ".venv" / "bin" / "python3"
PYTHON_EXECUTABLE = str(
    _venv_python) if _venv_python.exists() else sys.executable
logger.info(f"call_handler.py dir: {CALL_HANDLER_DIR}")
logger.info(f"Python executable: {PYTHON_EXECUTABLE}")

# Global call tracking
# {call_id: {status, phone_number, room_name, dispatch_id, timestamp}}
active_calls = {}


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    global server_running
    logger.info(
        f"Received signal {signum}. Initiating graceful server shutdown...")
    server_running = False
    sys.exit(0)


def cleanup():
    """Cleanup function called on exit"""
    logger.info("Server cleanup completed")


# Register signal handlers and cleanup
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
atexit.register(cleanup)

# Enhanced CORS configuration
CORS(app,
     resources={
         r"/*": {
             "origins": "*",
             "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
             "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"],
             "supports_credentials": True
         }
     },
     supports_credentials=True)


@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers',
                         'Content-Type,Authorization,X-Requested-With')
    response.headers.add('Access-Control-Allow-Methods',
                         'GET,PUT,POST,DELETE,OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response


@app.route("/health")
def health_check():
    """Health check endpoint"""
    logger.info("Health check endpoint called")
    return jsonify({"status": "healthy", "service": "calling-agent-backend"}), 200


@app.route("/makeCall", methods=["POST"])
def make_call():
    """Initiate an outbound call by invoking call_handler.py as a subprocess"""
    logger.info("makeCall endpoint called")
    try:
        data = request.get_json()
        phone_number = data.get("phone_number")

        if not phone_number:
            logger.error("No phone number provided")
            return jsonify({"error": "Phone number is required"}), 400

        # Validate phone number format (basic check)
        if not phone_number.startswith("+"):
            logger.error(f"Invalid phone number format: {phone_number}")
            return jsonify({"error": "Phone number must be in E.164 format (e.g., +1234567890)"}), 400

        logger.info(f"Initiating call to {phone_number} via call_handler.py")

        # Generate unique call ID
        call_id = str(uuid.uuid4())

        # Run call_handler.py as a subprocess — same way it works from CLI
        result = subprocess.run(
            [PYTHON_EXECUTABLE, "call_handler.py", phone_number],
            cwd=str(CALL_HANDLER_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )

        logger.info(f"call_handler.py stdout: {result.stdout}")
        if result.stderr:
            logger.warning(f"call_handler.py stderr: {result.stderr}")

        if result.returncode == 0:
            # Parse room name and dispatch ID from stdout
            room_name = ""
            dispatch_id = ""
            for line in result.stdout.splitlines():
                if "Room:" in line:
                    room_name = line.split("Room:")[-1].strip()
                if "Dispatch ID:" in line:
                    dispatch_id = line.split("Dispatch ID:")[-1].strip()

            # Track the call
            active_calls[call_id] = {
                "status": "connecting",
                "phone_number": phone_number,
                "room_name": room_name,
                "dispatch_id": dispatch_id,
                "timestamp": datetime.now().isoformat()
            }

            logger.info(f"Call dispatch successful via call_handler.py")
            return jsonify({
                "success": True,
                "call_id": call_id,
                "room_name": room_name,
                "dispatch_id": dispatch_id,
                "phone_number": phone_number,
                "message": "Call initiated successfully. The agent will dial your number shortly."
            }), 200
        else:
            error_msg = result.stderr or result.stdout or "call_handler.py failed"
            logger.error(f"call_handler.py failed: {error_msg}")
            return jsonify({
                "success": False,
                "error": error_msg.strip()
            }), 500

    except subprocess.TimeoutExpired:
        logger.error("call_handler.py timed out")
        return jsonify({"error": "Call dispatch timed out"}), 504
    except Exception as e:
        logger.error(f"Error making outbound call: {str(e)}")
        return jsonify({"error": f"Failed to initiate call: {str(e)}"}), 500


@app.route("/callStatus/<call_id>", methods=["GET"])
def get_call_status(call_id):
    """Get the status of a call by call_id"""
    logger.info(f"callStatus endpoint called for call_id: {call_id}")

    try:
        if call_id not in active_calls:
            return jsonify({
                "status": "not_found",
                "message": "Call not found"
            }), 404

        call_info = active_calls[call_id]
        room_name = call_info["room_name"]

        # Query LiveKit API to get actual room status
        try:
            import asyncio

            async def _check_room():
                lk_api = api.LiveKitAPI(
                    url=os.getenv("LIVEKIT_URL"),
                    api_key=os.getenv("LIVEKIT_API_KEY"),
                    api_secret=os.getenv("LIVEKIT_API_SECRET")
                )
                try:
                    participants = await lk_api.room.list_participants(
                        api.ListParticipantsRequest(room=room_name)
                    )
                    return len(participants.participants)
                finally:
                    await lk_api.aclose()

            participant_count = asyncio.run(_check_room())
            logger.info(
                f"Room {room_name} has {participant_count} participants")

            if participant_count >= 2:
                if call_info["status"] in ["connecting", "connected"]:
                    call_info["status"] = "connected"
                    active_calls[call_id] = call_info
                    logger.info(f"Call {call_id} is connected")
            elif participant_count == 1:
                if call_info["status"] == "connected":
                    call_info["status"] = "disconnected"
                    active_calls[call_id] = call_info
                    logger.info(f"Call {call_id} disconnected (caller left)")
                elif call_info["status"] == "connecting":
                    logger.info(
                        f"Call {call_id} still connecting (only agent in room)")
            elif participant_count == 0:
                call_info["status"] = "disconnected"
                active_calls[call_id] = call_info
                logger.info(f"Call {call_id} disconnected (no participants)")

        except Exception as e:
            logger.warning(f"Could not query LiveKit room status: {str(e)}")
            if "not found" in str(e).lower() or "does not exist" in str(e).lower():
                call_info["status"] = "disconnected"
                active_calls[call_id] = call_info
                logger.info(f"Call {call_id} disconnected (room not found)")

        return jsonify({
            "status": call_info["status"],
            "phone_number": call_info["phone_number"],
            "room_name": call_info["room_name"],
            "timestamp": call_info["timestamp"]
        }), 200

    except Exception as e:
        logger.error(f"Error getting call status: {str(e)}")
        return jsonify({"error": f"Failed to get call status: {str(e)}"}), 500


@app.route("/updateCallStatus/<call_id>", methods=["POST"])
def update_call_status(call_id):
    """Update the status of a call (used by agent or webhook)"""
    logger.info(f"updateCallStatus endpoint called for call_id: {call_id}")

    try:
        data = request.get_json()
        new_status = data.get("status")

        if not new_status:
            return jsonify({"error": "Status is required"}), 400

        if call_id not in active_calls:
            return jsonify({
                "success": False,
                "message": "Call not found"
            }), 404

        # Update call status
        active_calls[call_id]["status"] = new_status
        active_calls[call_id]["last_updated"] = datetime.now().isoformat()

        logger.info(f"Call {call_id} status updated to: {new_status}")

        return jsonify({
            "success": True,
            "call_id": call_id,
            "status": new_status
        }), 200

    except Exception as e:
        logger.error(f"Error updating call status: {str(e)}")
        return jsonify({"error": f"Failed to update call status: {str(e)}"}), 500


if __name__ == "__main__":
    try:
        logger.info(
            "Starting Calling Agent Flask server on host 0.0.0.0, port 5002")
        logger.info("Make sure agent.py is running to handle dispatches!")
        app.run(host="0.0.0.0", port=5002, debug=True)
    except KeyboardInterrupt:
        logger.info("Server interrupted by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
    finally:
        logger.info("Server shutdown completed")
