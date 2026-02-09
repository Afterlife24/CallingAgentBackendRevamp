import os
import logging
import signal
import sys
import atexit
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from dispatcher import OutboundCallDispatcher
from datetime import datetime
import uuid
from livekit import api

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

# Initialize dispatcher
dispatcher = OutboundCallDispatcher()

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
async def make_call():
    """Initiate an outbound call to a phone number using dispatcher"""
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

        logger.info(f"Initiating call to {phone_number} using dispatcher")

        # Generate unique call ID
        call_id = str(uuid.uuid4())

        # Use dispatcher to make the call
        result = await dispatcher.make_call(phone_number)

        if result["success"]:
            # Track the call
            active_calls[call_id] = {
                "status": "connecting",
                "phone_number": result["phone_number"],
                "room_name": result["room_name"],
                "dispatch_id": result["dispatch_id"],
                "timestamp": datetime.now().isoformat()
            }

            logger.info(f"Call dispatch successful: {result}")
            return jsonify({
                "success": True,
                "call_id": call_id,
                "room_name": result["room_name"],
                "dispatch_id": result["dispatch_id"],
                "phone_number": result["phone_number"],
                "message": "Call initiated successfully. The agent will dial your number shortly."
            }), 200
        else:
            logger.error(f"Call dispatch failed: {result.get('error')}")
            return jsonify({
                "success": False,
                "error": result.get("error", "Failed to initiate call")
            }), 500

    except Exception as e:
        logger.error(f"Error making outbound call: {str(e)}")
        return jsonify({"error": f"Failed to initiate call: {str(e)}"}), 500


@app.route("/callStatus/<call_id>", methods=["GET"])
async def get_call_status(call_id):
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
            lk_api = api.LiveKitAPI(
                url=os.getenv("LIVEKIT_URL"),
                api_key=os.getenv("LIVEKIT_API_KEY"),
                api_secret=os.getenv("LIVEKIT_API_SECRET")
            )

            # List participants in the room
            participants = await lk_api.room.list_participants(
                api.ListParticipantsRequest(room=room_name)
            )

            await lk_api.aclose()

            # Check participant count to determine call status
            participant_count = len(participants.participants)
            logger.info(
                f"Room {room_name} has {participant_count} participants")

            if participant_count >= 2:
                # Both agent and caller are in the room - call is connected
                if call_info["status"] in ["connecting", "connected"]:
                    call_info["status"] = "connected"
                    active_calls[call_id] = call_info
                    logger.info(f"Call {call_id} is connected")
            elif participant_count == 1:
                # Only agent is in room
                if call_info["status"] == "connected":
                    # Was connected, now only 1 participant - caller hung up
                    call_info["status"] = "disconnected"
                    active_calls[call_id] = call_info
                    logger.info(f"Call {call_id} disconnected (caller left)")
                elif call_info["status"] == "connecting":
                    # Still waiting for caller to pick up
                    logger.info(
                        f"Call {call_id} still connecting (only agent in room)")
            elif participant_count == 0:
                # No participants - call ended or failed
                call_info["status"] = "disconnected"
                active_calls[call_id] = call_info
                logger.info(f"Call {call_id} disconnected (no participants)")

        except Exception as e:
            logger.warning(f"Could not query LiveKit room status: {str(e)}")
            # If room doesn't exist, call has ended
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
async def update_call_status(call_id):
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
