#!/usr/bin/env python3
"""
PrusaLink Camera Timelapse Monitor
Polls PrusaLink camera every 10 seconds and saves images when they change.
"""

import hashlib
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image
from io import BytesIO
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('timelapse')

# Configuration
PRUSALINK_HOST = os.getenv("PRUSALINK_HOST", "192.168.178.56")
PRUSALINK_USERNAME = os.getenv("PRUSALINK_USERNAME", "maker")
PRUSALINK_PASSWORD = os.getenv("PRUSALINK_PASSWORD")
CAMERA_NAME = os.getenv("CAMERA_NAME", "RaspberryPi Camera: ov5647")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))  # seconds
TIMELAPSE_DIR = os.getenv("TIMELAPSE_DIR", "timelapse")


def get_camera_id(host: str, camera_name: str, api_key: str) -> str | None:
    """
    Get camera ID from PrusaLink API.

    Args:
        host: PrusaLink host IP address
        camera_name: Name of the camera to find
        api_key: API key for authentication

    Returns:
        Camera ID if found, None otherwise
    """
    try:
        url = f"http://{host}/api/v1/cameras"
        headers = {"X-Api-Key": api_key}

        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 401 or response.status_code == 403:
            logger.error(f"Authentication failed (HTTP {response.status_code})")
            return None
        response.raise_for_status()

        cameras = response.json()

        for camera in cameras['camera_list']:
            if camera['config']['name'] == camera_name:
                return camera['camera_id']

        logger.error(f"Camera '{camera_name}' not found")
        logger.error(f"Available cameras: {cameras}")
        return None

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching camera list: {e}")
        return None


def get_snapshot(host: str, camera_id: str, api_key: str, silent_on_connection_error: bool = False) -> bytes | None:
    """
    Fetch camera snapshot from PrusaLink API.

    Args:
        host: PrusaLink host IP address
        camera_id: ID of the camera
        api_key: API key for authentication
        silent_on_connection_error: If True, suppress error messages for connection errors

    Returns:
        Image bytes if successful, None otherwise
    """
    try:
        snapshot_url = f"http://{host}/api/v1/cameras/{camera_id}/snap"
        headers = {"X-Api-Key": api_key}

        response = requests.get(snapshot_url, headers=headers, timeout=10)
        response.raise_for_status()

        return response.content

    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        # Printer is offline/unreachable - don't spam logs
        if not silent_on_connection_error:
            logger.error(f"Cannot reach printer at {host}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching snapshot: {e}")
        return None


def calculate_image_hash(image_bytes: bytes) -> str:
    """Calculate MD5 hash of image bytes."""
    return hashlib.md5(image_bytes).hexdigest()


def save_image(image_bytes: bytes, output_dir: str) -> str | None:
    """
    Save image to timelapse directory with timestamp filename.

    Args:
        image_bytes: Image data
        output_dir: Directory to save images

    Returns:
        Path to saved image
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"frame_{timestamp}.jpg"
    filepath = Path(output_dir) / filename

    # Verify it's a valid image and save
    try:
        image = Image.open(BytesIO(image_bytes))
        image.save(filepath, "JPEG")
        return str(filepath)
    except Exception as e:
        logger.error(f"Error saving image: {e}")
        return None


def trigger_encoding(timelapse_dir: str) -> None:
    """
    Trigger encoding process in background.

    Runs encode_timelapse.py as a separate process to avoid blocking.
    """
    script_dir = Path(__file__).parent
    encode_script = script_dir / "encode_timelapse.py"

    if not encode_script.exists():
        logger.warning(f"Encoding script not found: {encode_script}")
        return

    try:
        # Run in background, detached from parent process
        subprocess.Popen(
            [sys.executable, str(encode_script), "--timelapse-dir", timelapse_dir],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        logger.info("Triggered video encoding")
    except Exception as e:
        logger.warning(f"Failed to trigger encoding: {e}")


def setup() -> tuple[str | None, bool]:
    """
    Perform initial setup and validation.

    Returns:
        Tuple of (camera_id, success). camera_id is None if setup failed.
    """
    # Check for required credentials
    if not PRUSALINK_PASSWORD:
        logger.error("PRUSALINK_PASSWORD not set in .env file")
        return None, False

    # Create timelapse directory
    try:
        Path(TIMELAPSE_DIR).mkdir(exist_ok=True)
    except Exception as e:
        logger.error(f"Cannot create directory {TIMELAPSE_DIR}: {e}")
        return None, False

    logger.info("Starting PrusaLink camera monitor")
    logger.info(f"Host: {PRUSALINK_HOST}")
    logger.info(f"Camera: {CAMERA_NAME}")
    logger.info(f"Poll interval: {POLL_INTERVAL}s")
    logger.info(f"Output directory: {TIMELAPSE_DIR}")

    # Get camera ID once at startup
    camera_id = get_camera_id(PRUSALINK_HOST, CAMERA_NAME, PRUSALINK_PASSWORD)
    if not camera_id:
        logger.error("Failed to resolve camera ID")
        return None, False

    logger.info(f"Camera ID resolved: {camera_id}")

    # Test snapshot fetch to ensure everything works
    logger.info("Testing snapshot fetch...")
    test_snapshot = get_snapshot(PRUSALINK_HOST, camera_id, PRUSALINK_PASSWORD)
    if not test_snapshot:
        logger.error("Failed to fetch test snapshot")
        return None, False

    logger.info(f"Test snapshot successful ({len(test_snapshot)} bytes)")

    return camera_id, True


def run_monitoring_loop(camera_id: str) -> int:
    """
    Main monitoring loop - continues running even if transient errors occur.

    Args:
        camera_id: The resolved camera ID

    Returns:
        Exit code
    """
    last_hash = None
    printer_was_offline = False
    frame_count = 0

    # Trigger encoding on startup (process old frames if any)
    trigger_encoding(TIMELAPSE_DIR)

    try:
        while True:
            # Check that API key is available (should always be at this point)
            if not PRUSALINK_PASSWORD:
                logger.error("API key became unavailable")
                return 1

            # Fetch camera snapshot (silent on connection errors to avoid spam)
            image_bytes = get_snapshot(
                PRUSALINK_HOST,
                camera_id,
                PRUSALINK_PASSWORD,
                silent_on_connection_error=True
            )

            if not image_bytes:
                # Printer likely offline - just wait and retry
                if not printer_was_offline:
                    logger.warning("Printer appears offline, will retry silently...")
                    printer_was_offline = True
                time.sleep(POLL_INTERVAL)
                continue

            # Printer came back online
            if printer_was_offline:
                logger.info("Printer back online")
                printer_was_offline = False

            # Calculate hash to detect changes
            try:
                current_hash = calculate_image_hash(image_bytes)
            except Exception as e:
                logger.error(f"Error calculating hash: {e}")
                time.sleep(POLL_INTERVAL)
                continue

            if current_hash != last_hash:
                # Image changed, save it
                saved_path = save_image(image_bytes, TIMELAPSE_DIR)
                if saved_path:
                    logger.info(f"Image changed - saved: {saved_path}")
                    last_hash = current_hash
                    frame_count += 1

                    # Trigger encoding every 240 frames
                    if frame_count % 240 == 0:
                        trigger_encoding(TIMELAPSE_DIR)
                # If save failed, log already printed by save_image, just continue
            else:
                logger.debug("No change detected")

            # Wait before next poll
            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Stopping camera monitor...")
        return 0


def main() -> int:
    """Main entry point."""
    camera_id, success = setup()
    if not success or camera_id is None:
        return 1

    return run_monitoring_loop(camera_id)


if __name__ == "__main__":
    sys.exit(main())
