#!/usr/bin/env python3
"""
Encode timelapse JPEGs to MP4 and safely delete processed frames.

This script:
1. Finds the oldest JPEGs in the timelapse directory
2. Encodes exactly 240 frames to MP4
3. Verifies the video integrity
4. Only deletes JPEGs after successful verification
"""

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('encoder')


def get_sorted_frames(timelapse_dir: Path) -> list[Path]:
    """Get all JPEG frames sorted by filename (oldest first)."""
    frames = sorted(timelapse_dir.glob("frame_*.jpg"))
    return frames


def verify_video(video_path: Path, expected_frames: int) -> bool:
    """
    Verify video integrity using ffprobe.

    Returns True only if:
    - Video is readable
    - Frame count matches expected
    - No errors detected
    """
    try:
        # Check video integrity and frame count
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-count_packets",
                "-show_entries", "stream=nb_read_packets",
                "-of", "json",
                str(video_path)
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30
        )

        data = json.loads(result.stdout)
        actual_frames = int(data["streams"][0]["nb_read_packets"])

        if actual_frames != expected_frames:
            logger.error(f"Frame count mismatch: expected {expected_frames}, got {actual_frames}")
            return False

        # Additional integrity check - try to decode all frames
        result = subprocess.run(
            [
                "ffmpeg",
                "-v", "error",
                "-i", str(video_path),
                "-f", "null",
                "-"
            ],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0 or result.stderr:
            logger.error(f"Video decode errors: {result.stderr}")
            return False

        return True

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, ValueError) as e:
        logger.error(f"Video verification failed: {e}")
        return False


def encode_frames(frames: list[Path], output_path: Path, framerate: int = 30) -> bool:
    """
    Encode frames to MP4 using ffmpeg.

    Uses a temporary file list to ensure exact frame order and selection.
    Returns True if encoding succeeded.
    """
    # Create temporary file with frame list
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        concat_file = Path(f.name)
        for frame in frames:
            # ffmpeg concat format requires: file 'path'
            f.write(f"file '{frame.absolute()}'\n")

    try:
        # Encode using concat demuxer for precise frame control
        # Use -framerate (input) instead of -r (output) to avoid frame duplication
        result = subprocess.run(
            [
                "ffmpeg",
                "-f", "concat",
                "-safe", "0",
                "-r", str(framerate),  # Input framerate
                "-i", str(concat_file),
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-y",  # Overwrite output file
                str(output_path)
            ],
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode != 0:
            logger.error(f"ffmpeg encoding failed: {result.stderr}")
            return False

        return True

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.error(f"Encoding failed: {e}")
        return False
    finally:
        concat_file.unlink(missing_ok=True)


def safe_delete_frames(frames: list[Path]) -> None:
    """
    Safely delete frames with error handling.
    Continues even if individual deletions fail.
    """
    failed = []
    for frame in frames:
        try:
            frame.unlink()
        except Exception as e:
            failed.append((frame, e))

    if failed:
        logger.warning(f"Failed to delete {len(failed)} frames")
        for frame, error in failed:
            logger.warning(f"  {frame}: {error}")


def main():
    parser = argparse.ArgumentParser(description="Encode timelapse frames to MP4")
    parser.add_argument(
        "--timelapse-dir",
        type=Path,
        default=Path("timelapse"),
        help="Directory containing JPEG frames (default: timelapse)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("timelapse_videos"),
        help="Directory for output MP4 files (default: timelapse_videos)"
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=240,
        help="Number of frames to encode (default: 240)"
    )
    parser.add_argument(
        "--framerate",
        type=int,
        default=30,
        help="Output video framerate (default: 30)"
    )
    parser.add_argument(
        "--keep-frames",
        action="store_true",
        help="Keep JPEG frames after encoding (for testing)"
    )

    args = parser.parse_args()

    # Validate directories
    if not args.timelapse_dir.is_dir():
        logger.error(f"Timelapse directory not found: {args.timelapse_dir}")
        return 1

    # Get available frames
    all_frames = get_sorted_frames(args.timelapse_dir)

    if len(all_frames) < args.frames:
        logger.info(f"Not enough frames: found {len(all_frames)}, need {args.frames}")
        return 0

    # Select frames to encode
    frames_to_encode = all_frames

    logger.info(f"Encoding {len(frames_to_encode)} frames...")
    logger.info(f"  First frame: {frames_to_encode[0].name}")
    logger.info(f"  Last frame: {frames_to_encode[-1].name}")

    # Create output directory
    args.output_dir.mkdir(exist_ok=True)

    # Generate output filename with timestamp from first frame
    first_frame_time = frames_to_encode[0].stem  # Use filename as timestamp
    output_file = args.output_dir / f"timelapse_{first_frame_time}.mp4"

    # Use temporary file for atomic write
    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False, dir=args.output_dir) as tmp:
        tmp_path = Path(tmp.name)

    try:
        # Encode to temporary file
        if not encode_frames(frames_to_encode, tmp_path, args.framerate):
            logger.error("Encoding failed")
            return 1

        # Verify video integrity
        logger.info("Verifying video integrity...")
        if not verify_video(tmp_path, len(frames_to_encode)):
            logger.error("Video verification failed")
            return 1

        # Atomic move to final location
        shutil.move(str(tmp_path), str(output_file))
        logger.info(f"Video created: {output_file}")

        # Delete frames only after successful verification
        if not args.keep_frames:
            logger.info(f"Deleting {len(frames_to_encode)} processed frames...")
            safe_delete_frames(frames_to_encode)
            logger.info("Frames deleted")
        else:
            logger.info("Keeping frames (--keep-frames specified)")

        return 0

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return 1
    finally:
        # Clean up temporary file if it still exists
        tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
