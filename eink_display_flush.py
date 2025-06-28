#!/usr/bin/env python3
"""
Simple E-Ink Display Script
Minimal standalone script to display images on e-ink display
"""

import time
import spidev
import platform
import os
import sys
import numpy as np
from PIL import Image
import argparse
import logging
from threading import Thread

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Check platform
_ROCK = "rockchip" in platform.release()
_RPI = (not _ROCK) and (
    os.path.exists("/proc/device-tree/model")
    and "raspberry" in open("/proc/device-tree/model", "r").read().lower()
    or os.path.exists("/sys/firmware/devicetree/base/model")
    and "raspberry" in open("/sys/firmware/devicetree/base/model", "r").read().lower()
)

# Import GPIO libraries based on platform
if _RPI:
    try:
        import lgpio
    except ImportError:
        logger.error("lgpio not available. Install with: pip install lgpio")
        sys.exit(1)
elif _ROCK:
    try:
        from gpiod.line import Direction, Value, Bias
        import gpiod
    except ImportError:
        logger.error("gpiod not available. Install with: pip install gpiod")
        sys.exit(1)


class RockGPIO:
    """Simplified RockGPIO implementation for GPIO handling on Rockchip"""

    def __init__(self):
        self.chip = gpiod.Chip("gpiochip0")
        self.lines = {}

    def _parse_gpio_name(self, gpio_name):
        """Parse GPIO name to offset based on Rockchip naming convention"""
        # This parsing is based on the typical Rockchip GPIO layout
        # You may need to adjust these values for your specific board
        gpio_map = {
            "GPIO0_D3": 27,  # BUSY pin
            "GPIO1_B1": 41,  # RST pin
            "GPIO1_C6": 54,  # DC pin
        }

        if gpio_name in gpio_map:
            return gpio_map[gpio_name]
        else:
            raise ValueError(f"Unknown GPIO: {gpio_name}")

    def setup(self, pin_name, direction, bias=None):
        """Setup GPIO pin"""
        offset = self._parse_gpio_name(pin_name)
        line = self.chip.get_line(offset)

        if direction == Direction.OUTPUT:
            line.request(consumer="eink", type=gpiod.LINE_REQ_DIR_OUT)
        else:
            flags = gpiod.LINE_REQ_DIR_IN
            if bias == Bias.PULL_UP:
                flags |= gpiod.LINE_REQ_FLAG_BIAS_PULL_UP
            line.request(consumer="eink", type=flags)

        self.lines[pin_name] = line

    def output(self, pin_name, value):
        """Set GPIO output value"""
        if pin_name in self.lines:
            gpio_value = 1 if value == Value.ACTIVE else 0
            self.lines[pin_name].set_value(gpio_value)

    def input(self, pin_name):
        """Read GPIO input value"""
        if pin_name in self.lines:
            return Value.ACTIVE if self.lines[pin_name].get_value() else Value.INACTIVE
        return Value.INACTIVE

    def cleanup(self):
        """Clean up GPIO resources"""
        for line in self.lines.values():
            line.release()
        self.lines.clear()
        if self.chip:
            self.chip.close()


class SimpleEinkDriver:
    """Simplified E-ink driver for displaying images"""

    def __init__(self):
        # Display dimensions
        self.EPD_WIDTH = 240
        self.EPD_HEIGHT = 416

        # LUT data for proper initialization
        self.lut_vcom = [
            0x01,
            0x0A,
            0x0A,
            0x0A,
            0x0A,
            0x01,
            0x01,
            0x02,
            0x0F,
            0x01,
            0x0F,
            0x01,
            0x01,
            0x01,
            0x01,
            0x0A,
            0x00,
            0x0A,
            0x00,
            0x01,
            0x01,
            0x01,
            0x00,
            0x00,
            0x00,
            0x00,
            0x01,
            0x01,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
        ]

        self.lut_ww = [
            0x01,
            0x4A,
            0x4A,
            0x0A,
            0x0A,
            0x01,
            0x01,
            0x02,
            0x8F,
            0x01,
            0x4F,
            0x01,
            0x01,
            0x01,
            0x01,
            0x8A,
            0x00,
            0x8A,
            0x00,
            0x01,
            0x01,
            0x01,
            0x80,
            0x00,
            0x80,
            0x00,
            0x01,
            0x01,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
        ]

        self.lut_bw = [
            0x01,
            0x4A,
            0x4A,
            0x0A,
            0x0A,
            0x01,
            0x01,
            0x02,
            0x8F,
            0x01,
            0x4F,
            0x01,
            0x01,
            0x01,
            0x01,
            0x8A,
            0x00,
            0x8A,
            0x00,
            0x01,
            0x01,
            0x01,
            0x80,
            0x00,
            0x80,
            0x00,
            0x01,
            0x01,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
        ]

        self.lut_wb = [
            0x01,
            0x0A,
            0x0A,
            0x8A,
            0x8A,
            0x01,
            0x01,
            0x02,
            0x8F,
            0x01,
            0x4F,
            0x01,
            0x01,
            0x01,
            0x01,
            0x4A,
            0x00,
            0x4A,
            0x00,
            0x01,
            0x01,
            0x01,
            0x40,
            0x00,
            0x40,
            0x00,
            0x01,
            0x01,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
        ]

        self.lut_bb = [
            0x01,
            0x0A,
            0x0A,
            0x8A,
            0x8A,
            0x01,
            0x01,
            0x02,
            0x8F,
            0x01,
            0x4F,
            0x01,
            0x01,
            0x01,
            0x01,
            0x4A,
            0x00,
            0x4A,
            0x00,
            0x01,
            0x01,
            0x01,
            0x40,
            0x00,
            0x40,
            0x00,
            0x01,
            0x01,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
        ]

        # Pin definitions
        if _ROCK:
            self.RK_DC_PIN = "GPIO1_C6"
            self.RK_RST_PIN = "GPIO1_B1"
            self.RK_BUSY_PIN = "GPIO0_D3"
            try:
                self.RockGPIO = RockGPIO()
            except Exception as e:
                logger.error(f"Failed to initialize RockGPIO: {e}")
                self.RockGPIO = None
        else:
            self.DC_PIN = 7
            self.RST_PIN = 13
            self.BUSY_PIN = 9
            self.lgpio_handle = None
            # Always define RockGPIO attribute even on non-Rock platforms
            self.RockGPIO = None

        self.spi = None
        self.initialized = False
        self.oldData = [0xFF] * 12480  # Initialize with white
        self._write_thread = None

    def initialize(self):
        """Initialize the e-ink display"""
        try:
            logger.info("Initializing E-ink display...")

            # Initialize GPIO and SPI
            self._init_gpio_and_spi()

            # Initialize display with proper sequence
            self._init_display()

            self.initialized = True
            logger.info("E-ink display initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize e-ink display: {e}")
            return False

    def _init_gpio_and_spi(self):
        """Initialize GPIO and SPI"""
        if _RPI:
            self.lgpio_handle = lgpio.gpiochip_open(0)
            lgpio.gpio_claim_output(self.lgpio_handle, self.DC_PIN, 0)
            lgpio.gpio_claim_output(self.lgpio_handle, self.RST_PIN, 0)
            lgpio.gpio_claim_input(self.lgpio_handle, self.BUSY_PIN, lgpio.SET_PULL_UP)
        else:
            if self.RockGPIO:
                self.RockGPIO.setup(self.RK_DC_PIN, Direction.OUTPUT)
                self.RockGPIO.setup(self.RK_RST_PIN, Direction.OUTPUT)
                self.RockGPIO.setup(self.RK_BUSY_PIN, Direction.INPUT, bias=Bias.PULL_UP)
            else:
                raise Exception("RockGPIO not available - GPIO hardware may not be accessible")

        # Initialize SPI
        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)  # Bus 0, Device 0
        self.spi.max_speed_hz = 30000000
        self.spi.mode = 0

    def safe_writebytes(self, data, chunk_size=4096):
        """Safe SPI write with chunking"""
        if self._write_thread and self._write_thread.is_alive():
            return
        self._write_thread = Thread(target=self._write_chunks, args=(data, chunk_size))
        self._write_thread.start()

    def _write_chunks(self, data, chunk_size):
        """Write data in chunks to avoid SPI buffer overflow"""
        data_np = np.array(data, dtype=np.uint8)
        for i in range(0, len(data), chunk_size):
            try:
                self.spi.writebytes(data_np[i : i + chunk_size].tolist())
            except Exception as e:
                logger.error(f"SPI write error at offset {i}: {e}")
                raise

    def _write_command(self, command):
        """Write command to display"""
        time.sleep(0.000001)  # Small delay

        # Set DC pin low for command
        if _RPI:
            lgpio.gpio_write(self.lgpio_handle, self.DC_PIN, 0)
        elif _ROCK and self.RockGPIO:
            self.RockGPIO.output(self.RK_DC_PIN, Value.INACTIVE)

        self.spi.xfer2([command])

    def _write_data(self, data):
        """Write data to display"""
        time.sleep(0.000001)  # Small delay

        # Set DC pin high for data
        if _RPI:
            lgpio.gpio_write(self.lgpio_handle, self.DC_PIN, 1)
        elif _ROCK and self.RockGPIO:
            self.RockGPIO.output(self.RK_DC_PIN, Value.ACTIVE)

        if isinstance(data, int):
            self.spi.xfer2([data])
        else:
            self.spi.xfer2(data)

    def _reset_display(self):
        """Reset the display"""
        time.sleep(0.1)  # Initial delay

        if _RPI:
            lgpio.gpio_write(self.lgpio_handle, self.RST_PIN, 0)
            time.sleep(0.02)
            lgpio.gpio_write(self.lgpio_handle, self.RST_PIN, 1)
            time.sleep(0.02)
        elif _ROCK and self.RockGPIO:
            self.RockGPIO.output(self.RK_RST_PIN, Value.INACTIVE)
            time.sleep(0.02)
            self.RockGPIO.output(self.RK_RST_PIN, Value.ACTIVE)
            time.sleep(0.02)

    def _wait_busy(self):
        """Wait for display to become ready"""
        if _RPI:
            while lgpio.gpio_read(self.lgpio_handle, self.BUSY_PIN) == 0:
                time.sleep(0.01)
        elif _ROCK and self.RockGPIO:
            while self.RockGPIO.input(self.RK_BUSY_PIN) == Value.INACTIVE:
                time.sleep(0.01)

    def _write_lut(self):
        """Write Look-Up Table for proper display operation"""
        # Write VCOM LUT
        self._write_command(0x20)
        for value in self.lut_vcom:
            self._write_data(value)

        # Write WW LUT
        self._write_command(0x21)
        for value in self.lut_ww:
            self._write_data(value)

        # Write BW LUT
        self._write_command(0x22)
        for value in self.lut_bw:
            self._write_data(value)

        # Write WB LUT
        self._write_command(0x23)
        for value in self.lut_wb:
            self._write_data(value)

        # Write BB LUT
        self._write_command(0x24)
        for value in self.lut_bb:
            self._write_data(value)

    def _init_display(self):
        """Initialize display settings with proper sequence"""
        # Reset display
        self._reset_display()

        # Power on
        self._write_command(0x04)
        self._wait_busy()

        # Panel setting
        self._write_command(0x00)
        self._write_data(0xF7)

        # Cancel waveform default setting
        self._write_command(0x09)

        # Power setting
        self._write_command(0x01)
        self._write_data(0x03)
        self._write_data(0x10)
        self._write_data(0x3F)
        self._write_data(0x3F)
        self._write_data(0x3F)

        # Booster soft start
        self._write_command(0x06)
        self._write_data(0xD7)
        self._write_data(0xD7)
        self._write_data(0x33)

        # PLL control
        self._write_command(0x30)
        self._write_data(0x09)

        # VCOM and data interval setting
        self._write_command(0x50)
        self._write_data(0xD7)

        # Resolution setting
        self._write_command(0x61)
        self._write_data(0xF0)  # Width: 240
        self._write_data(0x01)  # Height high byte
        self._write_data(0xA0)  # Height low byte: 416

        # Gate/Source start position setting
        self._write_command(0x2A)
        self._write_data(0x80)
        self._write_data(0x00)
        self._write_data(0x00)
        self._write_data(0xFF)
        self._write_data(0x00)

        # VCOM DC setting
        self._write_command(0x82)
        self._write_data(0x0F)

        # Write LUT
        self._write_lut()

    def display_image(self, image_data):
        """Display image data on the e-ink screen"""
        if not self.initialized:
            logger.error("Display not initialized")
            return False

        try:
            # Send old data (0x10)
            self._write_command(0x10)
            if _ROCK:
                self.RockGPIO.output(self.RK_DC_PIN, Value.ACTIVE)
            else:
                lgpio.gpio_write(self.lgpio_handle, self.DC_PIN, 1)
            self.safe_writebytes(self.oldData)

            # Send new data (0x13)
            self._write_command(0x13)
            if _ROCK:
                self.RockGPIO.output(self.RK_DC_PIN, Value.ACTIVE)
            else:
                lgpio.gpio_write(self.lgpio_handle, self.DC_PIN, 1)
            self.safe_writebytes(image_data)
            self.oldData = list(image_data)

            # Refresh display
            self._write_command(0x12)
            time.sleep(0.001)
            self._wait_busy()

            logger.info("Image displayed successfully")
            return True

        except Exception as e:
            logger.error(f"Error displaying image: {e}")
            return False

    def clear_display(self):
        """Clear the display (set to white)"""
        if not self.initialized:
            logger.error("Display not initialized")
            return False

        try:
            bytes_needed = (self.EPD_WIDTH * self.EPD_HEIGHT) // 8

            # Send old data
            self._write_command(0x10)
            if _ROCK:
                self.RockGPIO.output(self.RK_DC_PIN, Value.ACTIVE)
            else:
                lgpio.gpio_write(self.lgpio_handle, self.DC_PIN, 1)
            self.safe_writebytes(self.oldData)

            # Send new data (all white/clear)
            self._write_command(0x13)
            if _ROCK:
                self.RockGPIO.output(self.RK_DC_PIN, Value.ACTIVE)
            else:
                lgpio.gpio_write(self.lgpio_handle, self.DC_PIN, 1)
            clear_data = [0] * bytes_needed
            self.safe_writebytes(clear_data)
            self.oldData = clear_data

            # Refresh display
            self._write_command(0x12)
            time.sleep(0.001)
            self._wait_busy()

            logger.info("Display cleared")
            return True

        except Exception as e:
            logger.error(f"Error clearing display: {e}")
            return False

    def cleanup(self):
        """Clean up resources"""
        try:
            if self.spi:
                self.spi.close()

            if _RPI and self.lgpio_handle:
                lgpio.gpiochip_close(self.lgpio_handle)
            elif _ROCK and hasattr(self, "RockGPIO"):
                self.RockGPIO.cleanup()

            logger.info("Cleanup completed")

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")


def load_and_convert_image(
    image_path, width=240, height=416, threshold=128, dither=True
):
    """
    Load and convert an image for e-ink display

    Args:
        image_path: Path to image file
        width: Display width
        height: Display height
        threshold: Black/white threshold (0-255)
        dither: Apply dithering for better quality

    Returns:
        bytearray ready for display
    """
    try:
        # Load image
        img = Image.open(image_path)
        logger.info(f"Loaded image: {img.size}, mode: {img.mode}")

        # Resize to display size
        img = img.resize((width, height), Image.Resampling.LANCZOS)

        # Convert to grayscale
        if img.mode != "L":
            img = img.convert("L")

        # Convert to numpy array
        pixels = np.array(img, dtype=np.uint8)

        # Horizontal flip (e-ink displays are often mirrored)
        pixels = np.fliplr(pixels)

        # Apply dithering if requested
        if dither:
            pixels = _apply_floyd_steinberg_dithering(pixels, threshold)

        # Convert to 1-bit
        binary = (pixels > threshold).astype(np.uint8)

        # Pack into bytes (8 pixels per byte)
        bytes_per_row = (width + 7) // 8
        image_data = []

        for y in range(height):
            for x_byte in range(bytes_per_row):
                byte_val = 0
                for bit in range(8):
                    x = x_byte * 8 + bit
                    if x < width and binary[y, x]:
                        byte_val |= 1 << (7 - bit)  # MSB first
                image_data.append(byte_val)

        logger.info(f"Converted image to {len(image_data)} bytes")
        return bytearray(image_data)

    except Exception as e:
        logger.error(f"Error processing image: {e}")
        return None


def _apply_floyd_steinberg_dithering(pixels, threshold=128):
    """Apply Floyd-Steinberg dithering to improve image quality"""
    height, width = pixels.shape
    pixels = pixels.copy().astype(np.float32)

    for y in range(height - 1):
        for x in range(1, width - 1):
            old_pixel = pixels[y, x]
            new_pixel = 0 if old_pixel < threshold else 255
            pixels[y, x] = new_pixel

            error = old_pixel - new_pixel
            pixels[y, x + 1] += error * 7 / 16
            pixels[y + 1, x - 1] += error * 3 / 16
            pixels[y + 1, x] += error * 5 / 16
            pixels[y + 1, x + 1] += error * 1 / 16

    # Handle last row
    y = height - 1
    for x in range(1, width - 1):
        old_pixel = pixels[y, x]
        new_pixel = 0 if old_pixel < threshold else 255
        pixels[y, x] = new_pixel
        error = old_pixel - new_pixel
        pixels[y, x + 1] += error * 7 / 16

    return np.clip(pixels, 0, 255).astype(np.uint8)


def main():
    parser = argparse.ArgumentParser(description="Display image on e-ink screen")
    parser.add_argument("image", nargs="?", help="Path to image file")
    parser.add_argument("--clear", action="store_true", help="Clear the display")
    parser.add_argument(
        "--threshold", type=int, default=128, help="B/W threshold (0-255)"
    )
    parser.add_argument("--no-dither", action="store_true", help="Disable dithering")

    args = parser.parse_args()

    if not args.image and not args.clear:
        parser.print_help()
        return

    # Initialize display
    display = SimpleEinkDriver()

    try:
        if not display.initialize():
            logger.error("Failed to initialize display")
            return 1

        if args.clear:
            logger.info("Clearing display...")
            display.clear_display()

        if args.image:
            if not os.path.exists(args.image):
                logger.error(f"Image file not found: {args.image}")
                return 1

            logger.info(f"Loading image: {args.image}")
            image_data = load_and_convert_image(
                args.image, threshold=args.threshold, dither=not args.no_dither
            )

            if image_data is None:
                logger.error("Failed to load/convert image")
                return 1

            logger.info("Displaying image...")
            display.display_image(image_data)

        logger.info("Done!")
        return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return 1
    finally:
        display.cleanup()


if __name__ == "__main__":
    sys.exit(main())
