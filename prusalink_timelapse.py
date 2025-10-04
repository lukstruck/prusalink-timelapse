#!/usr/bin/env python3
"""
PrusaLink Camera Timelapse Monitor
Polls PrusaLink camera every 10 seconds and saves images when they change.
"""

import hashlib
import os
import time
from datetime import datetime
from pathlib import Path

import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth
from PIL import Image
from io import BytesIO
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

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
            print(f"Error: Authentication failed (HTTP {response.status_code})")
            return None
        response.raise_for_status()

        cameras = response.json()

        for camera in cameras['camera_list']:
            if camera['config']['name'] == camera_name:
                return camera['camera_id']

        print(f"Error: Camera '{camera_name}' not found")
        print(f"Available cameras: {cameras}")
        return None

    except requests.exceptions.RequestException as e:
        print(f"Error fetching camera list: {e}")
        return None


def get_snapshot(host: str, camera_id: str, api_key: str) -> bytes | None:
    """
    Fetch camera snapshot from PrusaLink API.

    Args:
        host: PrusaLink host IP address
        camera_id: ID of the camera
        api_key: API key for authentication

    Returns:
        Image bytes if successful, None otherwise
    """
    try:
        snapshot_url = f"http://{host}/api/v1/cameras/{camera_id}/snap"
        headers = {"X-Api-Key": api_key}

        response = requests.get(snapshot_url, headers=headers, timeout=10)
        response.raise_for_status()

        return response.content

    except requests.exceptions.RequestException as e:
        print(f"Error fetching snapshot: {e}")
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
        print(f"Error saving image: {e}")
        return None


def main():
    """Main monitoring loop."""
    # Check for required credentials
    if not PRUSALINK_PASSWORD:
        print("Error: PRUSALINK_PASSWORD not set in .env file")
        return 1

    # Create timelapse directory
    Path(TIMELAPSE_DIR).mkdir(exist_ok=True)

    print(f"Starting PrusaLink camera monitor...")
    print(f"Host: {PRUSALINK_HOST}")
    print(f"Camera: {CAMERA_NAME}")
    print(f"Poll interval: {POLL_INTERVAL}s")
    print(f"Output directory: {TIMELAPSE_DIR}")
    print("-" * 50)

    # Get camera ID once at startup
    camera_id = get_camera_id(PRUSALINK_HOST, CAMERA_NAME, PRUSALINK_PASSWORD)
    if not camera_id:
        print("Error: Failed to resolve camera. Exiting.")
        return 1

    print(f"Camera ID resolved: {camera_id}")
    print("-" * 50)

    last_hash = None

    try:
        while True:
            # Fetch camera snapshot
            image_bytes = get_snapshot(PRUSALINK_HOST, camera_id, PRUSALINK_PASSWORD)

            if not image_bytes:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Failed to fetch snapshot")
                time.sleep(POLL_INTERVAL)
                continue

            # Calculate hash to detect changes
            current_hash = calculate_image_hash(image_bytes)

            if current_hash != last_hash:
                # Image changed, save it
                saved_path = save_image(image_bytes, TIMELAPSE_DIR)
                if saved_path:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                          f"Image changed - saved: {saved_path}")
                    last_hash = current_hash
            else:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                      f"No change detected")

            # Wait before next poll
            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\nStopping camera monitor...")
        return 0
    except Exception as e:
        print(f"\nError: {e}")
        return 1


if __name__ == "__main__":
    main()
