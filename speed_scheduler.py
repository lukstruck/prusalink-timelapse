#!/usr/bin/env python3
"""
PrusaLink Speed Scheduler
Sets printer speed to 100% immediately and 200% at 3 AM.
"""

import logging
import os
import sys
import time
from datetime import datetime, time as dt_time
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('speed-scheduler')

# Configuration
PRUSALINK_HOST = os.getenv("PRUSALINK_HOST")
PRUSALINK_PASSWORD = os.getenv("PRUSALINK_PASSWORD")
TARGET_HOUR = int(os.getenv("SPEED_CHANGE_HOUR", "3"))
DAYTIME_SPEED = int(os.getenv("DAYTIME_SPEED", "200"))
NIGHTTIME_SPEED = int(os.getenv("NIGHTTIME_SPEED", "100"))


def set_print_speed(host: str, api_key: str, speed_percent: int) -> bool:
    """
    Set the print speed percentage via PrusaLink legacy API.

    Uses the legacy OctoPrint-compatible API endpoint.

    Args:
        host: PrusaLink host IP address
        api_key: API key for authentication
        speed_percent: Speed percentage (100 = 100%, 200 = 200%)

    Returns:
        True if successful, False otherwise
    """
    try:
        url = f"http://{host}/api/printer/printhead"
        headers = {
            'X-Api-Key': api_key,
            'Content-Type': 'application/json'
        }

        data = {
            'command': 'speed',
            'factor': speed_percent
        }

        response = requests.post(url, headers=headers, json=data, timeout=10)

        if response.status_code in (200, 204):
            logger.info(f"Successfully set speed to {speed_percent}%")
            return True
        else:
            logger.error(f"Failed to set speed. Status: {response.status_code}, Response: {response.text}")
            return False

    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        logger.error(f"Cannot reach printer at {host}")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Error setting speed: {e}")
        return False


def setup() -> bool:
    """
    Perform initial setup and validation.

    Returns:
        True if setup successful, False otherwise
    """
    
    if not PRUSALINK_HOST:
        logger.error("PRUSALINK_HOST not set in .env file")
        return False
    
    
    # Check for required credentials
    if not PRUSALINK_PASSWORD:
        logger.error("PRUSALINK_PASSWORD not set in .env file")
        return False

    logger.info("Starting PrusaLink speed scheduler")
    logger.info(f"Host: {PRUSALINK_HOST}")
    logger.info(f"Nighttime speed: {NIGHTTIME_SPEED}%")
    logger.info(f"Daytime speed: {DAYTIME_SPEED}% (at {TARGET_HOUR}:00 AM)")

    return True


def run_scheduler() -> int:
    """
    Main scheduler loop.

    Returns:
        Exit code
    """
    # Set to nighttime speed immediately
    logger.info(f"Setting speed to {NIGHTTIME_SPEED}% (quiet mode)...")
    if not set_print_speed(PRUSALINK_HOST, PRUSALINK_PASSWORD, NIGHTTIME_SPEED):
        logger.warning("Initial speed change failed, but continuing...")

    logger.info(f"Waiting until {TARGET_HOUR}:00 AM to set speed to {DAYTIME_SPEED}%...")
    logger.info("Press Ctrl+C to stop the scheduler\n")

    speed_changed = False

    try:
        while True:
            now = datetime.now()
            current_hour = now.hour
            current_minute = now.minute

            # Check if it's time to change to daytime speed
            if current_hour == TARGET_HOUR and current_minute == 0 and not speed_changed:
                logger.info(f"It's {TARGET_HOUR}:00 AM! Setting speed to {DAYTIME_SPEED}%...")
                if set_print_speed(PRUSALINK_HOST, PRUSALINK_PASSWORD, DAYTIME_SPEED):
                    speed_changed = True

            # Reset flag at TARGET_HOUR:01 so it can trigger again tomorrow
            if current_hour == TARGET_HOUR and current_minute == 1 and speed_changed:
                speed_changed = False
                logger.debug("Reset speed change flag for next day")

            # Sleep for 30 seconds before checking again
            time.sleep(30)

    except KeyboardInterrupt:
        logger.info("Stopping speed scheduler...")
        return 0


def main() -> int:
    """Main entry point."""
    if not setup():
        return 1

    return run_scheduler()


if __name__ == "__main__":
    sys.exit(main())
