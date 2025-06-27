#!/usr/bin/env python3
"""
WiFi Information Display for E-Ink
Generates and displays WiFi network information on e-ink display
"""

import sys
import os
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import argparse
import logging

# QR code generation
try:
    import qrcode
except ImportError:
    qrcode = None
    logging.warning(
        "qrcode package not available. Install with: pip install qrcode[pil]"
    )

# Add the distiller project path to import NetworkUtils
from network.network_utils import NetworkUtils

# Import our simple e-ink display functions
try:
    from eink_display_flush import SimpleEinkDriver, load_and_convert_image

    EINK_AVAILABLE = True
except ImportError as e:
    logging.warning(f"E-ink display not available: {e}")
    SimpleEinkDriver = None
    load_and_convert_image = None
    EINK_AVAILABLE = False

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def create_wifi_info_image(
    width=250, height=128, filename="wifi_info.png", auto_display=False
):
    """
    Create an image with WiFi information for e-ink display
    Optimized for horizontal layout on monochrome e-ink displays

    Args:
        width: Image width in pixels
        height: Image height in pixels
        filename: Output filename
        auto_display: If True, automatically display on e-ink after creating

    Returns:
        Filename of created image
    """

    # Get network information
    logger.info("Gathering network information...")
    network_utils = NetworkUtils()

    # Collect all network data
    wifi_name = network_utils.get_wifi_name()
    ip_address = network_utils.get_wifi_ip_address()
    mac_address = network_utils.get_wifi_mac_address()
    signal_strength = network_utils.get_wifi_signal_strength()
    network_details = network_utils.get_network_details()

    logger.info(f"WiFi: {wifi_name}, IP: {ip_address}")

    # Create image
    img = Image.new("L", (width, height), 255)  # White background
    draw = ImageDraw.Draw(img)

    # Try to load fonts - optimized sizes for horizontal layout
    try:
        # Use MartianMono font from local directory
        martian_font_path = os.path.join(
            os.getcwd(), "fonts", "MartianMonoNerdFont-CondensedBold.ttf"
        )
        font_header = ImageFont.truetype(martian_font_path, 18)   # Main header
        font_large = ImageFont.truetype(martian_font_path, 16)   # Important info
        font_medium = ImageFont.truetype(martian_font_path, 14)  # Secondary info
        font_small = ImageFont.truetype(martian_font_path, 12)   # Details
        font_tiny = ImageFont.truetype(martian_font_path, 10)    # Labels
        logger.info("Using MartianMono font for better readability")
    except Exception as e:
        logger.warning(f"Could not load MartianMono font: {e}")
        try:
            # Fallback to Liberation fonts
            font_header = ImageFont.truetype(
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 18
            )
            font_large = ImageFont.truetype(
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 16
            )
            font_medium = ImageFont.truetype(
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 14
            )
            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 12
            )
            font_tiny = ImageFont.truetype(
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 10
            )
        except:
            # Use default font scaled appropriately
            font_header = ImageFont.load_default()
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()
            font_small = ImageFont.load_default()
            font_tiny = ImageFont.load_default()

    # === HORIZONTAL LAYOUT DESIGN ===
    # Top section: WiFi name and status
    # Middle section: IP address and signal strength
    # Bottom section: Connection details and timestamp
    
    margin = 8
    center_x = width // 2
    
    # === TOP SECTION - WiFi Network Name ===
    y = margin
    
    # WiFi status icon (left side)
    is_connected = wifi_name != "Not Connected"
    status_icon = "●" if is_connected else "○"
    draw.text((margin, y), status_icon, fill=0, font=font_header)
    
    # "WiFi" label
    label_x = margin + 25
    draw.text((label_x, y + 2), "WiFi", fill=0, font=font_tiny)
    
    # Network name (centered, large)
    ssid_display = wifi_name if len(wifi_name) <= 20 else wifi_name[:17] + "..."
    ssid_bbox = draw.textbbox((0, 0), ssid_display, font=font_large)
    ssid_width = ssid_bbox[2] - ssid_bbox[0]
    ssid_x = center_x - (ssid_width // 2)
    draw.text((ssid_x, y), ssid_display, fill=0, font=font_large)
    
    # Connection time/status (right side)
    timestamp = datetime.now().strftime("%H:%M")
    time_bbox = draw.textbbox((0, 0), timestamp, font=font_small)
    time_width = time_bbox[2] - time_bbox[0]
    draw.text((width - margin - time_width, y + 2), timestamp, fill=0, font=font_small)
    
    y += 22
    
    # Separator line
    draw.line([margin, y, width - margin, y], fill=0, width=1)
    y += 8
    
    # === MIDDLE SECTION - IP Address and Signal ===
    # IP Address (left side)
    ip_label = "IP"
    draw.text((margin, y), ip_label, fill=0, font=font_tiny)
    draw.text((margin, y + 12), ip_address, fill=0, font=font_medium)
    
    # Signal strength (right side)
    signal_percent = 50
    if "%" in signal_strength:
        try:
            signal_percent = int(signal_strength.split("%")[0])
        except:
            pass
    
    # Signal bars (modern horizontal bars)
    signal_label_x = width - 80
    draw.text((signal_label_x, y), "Signal", fill=0, font=font_tiny)
    
    # Enhanced signal bars
    bar_y = y + 12
    bar_width = 8
    bar_spacing = 2
    bar_heights = [6, 10, 14, 18]  # Progressive heights
    
    for i in range(4):
        bar_x = signal_label_x + i * (bar_width + bar_spacing)
        bar_height = bar_heights[i]
        
        if signal_percent > (i * 25):
            # Filled bar
            draw.rectangle([bar_x, bar_y + (18 - bar_height), 
                          bar_x + bar_width, bar_y + 18], fill=0)
        else:
            # Empty bar outline
            draw.rectangle([bar_x, bar_y + (18 - bar_height), 
                          bar_x + bar_width, bar_y + 18], outline=0, width=1)
    
    # Signal percentage
    signal_text = f"{signal_percent}%"
    signal_text_x = signal_label_x + 40
    draw.text((signal_text_x, y + 12), signal_text, fill=0, font=font_small)
    
    y += 35
    
    # === BOTTOM SECTION - Connection Details ===
    # Separator line
    draw.line([margin, y, width - margin, y], fill=0, width=1)
    y += 6
    
    # SSH connection info (horizontal layout)
    if is_connected:
        ssh_info = f"SSH: distiller@{ip_address}"
        draw.text((margin, y), ssh_info, fill=0, font=font_tiny)
        
        # Password on same line, right-aligned
        pwd_text = "Password: one"
        pwd_bbox = draw.textbbox((0, 0), pwd_text, font=font_tiny)
        pwd_width = pwd_bbox[2] - pwd_bbox[0]
        draw.text((width - margin - pwd_width, y), pwd_text, fill=0, font=font_tiny)
        
        y += 12
        
        # Web interface info
        web_info = f"Web: http://{ip_address}:8080"
        draw.text((margin, y), web_info, fill=0, font=font_tiny)
        
        # Device status
        device_status = "● READY"
        status_bbox = draw.textbbox((0, 0), device_status, font=font_tiny)
        status_width = status_bbox[2] - status_bbox[0]
        draw.text((width - margin - status_width, y), device_status, fill=0, font=font_tiny)
    else:
        # Disconnected state
        disconnect_msg = "Connect to 'DistillerSetup' hotspot to configure"
        msg_bbox = draw.textbbox((0, 0), disconnect_msg, font=font_tiny)
        msg_width = msg_bbox[2] - msg_bbox[0]
        msg_x = center_x - (msg_width // 2)
        draw.text((msg_x, y), disconnect_msg, fill=0, font=font_tiny)
    
    # Clean border
    draw.rectangle([0, 0, width - 1, height - 1], outline=0, width=2)

    # Save the image
    img.save(filename)
    logger.info(f"WiFi info image saved as: {filename}")

    # Auto-display if requested
    if auto_display:
        display_on_eink(filename)

    return filename


def display_on_eink(image_path):
    """Display the image on the e-ink screen"""
    if not EINK_AVAILABLE:
        logger.warning("E-ink display not available, skipping display")
        return False

    logger.info("Displaying image on e-ink screen...")

    try:
        # Initialize e-ink display
        if SimpleEinkDriver is None or load_and_convert_image is None:
            logger.error("E-ink display functions not available")
            return False

        display = SimpleEinkDriver()

        if not display.initialize():
            logger.error("Failed to initialize e-ink display")
            return False

        # Convert and display image
        image_data = load_and_convert_image(image_path, threshold=128, dither=True)

        if image_data is None:
            logger.error("Failed to convert image")
            return False

        success = display.display_image(image_data)
        display.cleanup()

        if success:
            logger.info("WiFi info displayed successfully on e-ink")
        else:
            logger.error("Failed to display image on e-ink")

        return success

    except Exception as e:
        logger.error(f"Error displaying on e-ink: {e}")
        return False


def create_wifi_setup_image(
    ssid,
    password,
    ip_address,
    port=8080,
    width=250,
    height=128,
    filename="wifi_setup.png",
    auto_display=False,
):
    """
    Create an image with WiFi setup instructions for e-ink display
    Optimized for horizontal layout on monochrome e-ink displays

    Args:
        ssid: WiFi hotspot name
        password: WiFi hotspot password
        ip_address: Setup interface IP address
        port: Setup interface port
        width: Image width in pixels
        height: Image height in pixels
        filename: Output filename
        auto_display: If True, automatically display on e-ink after creating

    Returns:
        Filename of created image
    """

    # Create image
    img = Image.new("L", (width, height), 255)  # White background
    draw = ImageDraw.Draw(img)

    # Try to load fonts - optimized for horizontal setup layout
    try:
        martian_font_path = os.path.join(
            os.getcwd(), "fonts", "MartianMonoNerdFont-CondensedBold.ttf"
        )
        font_header = ImageFont.truetype(martian_font_path, 18)
        font_large = ImageFont.truetype(martian_font_path, 16)
        font_medium = ImageFont.truetype(martian_font_path, 14)
        font_small = ImageFont.truetype(martian_font_path, 12)
        font_tiny = ImageFont.truetype(martian_font_path, 10)
        logger.info("Using MartianMono font for setup instructions")
    except Exception as e:
        logger.warning(f"Could not load MartianMono font: {e}")
        try:
            font_header = ImageFont.truetype(
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 18
            )
            font_large = ImageFont.truetype(
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 16
            )
            font_medium = ImageFont.truetype(
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 14
            )
            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 12
            )
            font_tiny = ImageFont.truetype(
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 10
            )
        except:
            font_header = ImageFont.load_default()
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()
            font_small = ImageFont.load_default()
            font_tiny = ImageFont.load_default()

    # === HORIZONTAL SETUP LAYOUT ===
    margin = 8
    center_x = width // 2
    
    # === TOP SECTION - WiFi Setup Header ===
    y = margin
    
    # Setup icon
    setup_icon = "⚙"
    draw.text((margin, y), setup_icon, fill=0, font=font_header)
    
    # Title centered
    title = "WiFi Setup Required"
    title_bbox = draw.textbbox((0, 0), title, font=font_large)
    title_width = title_bbox[2] - title_bbox[0]
    title_x = center_x - (title_width // 2)
    draw.text((title_x, y + 2), title, fill=0, font=font_large)
    
    # Status (right side)
    draw.text((width - 30, y + 4), "1/3", fill=0, font=font_small)
    
    y += 22
    
    # Separator line
    draw.line([margin, y, width - margin, y], fill=0, width=1)
    y += 8
    
    # === MIDDLE SECTION - Connection Instructions ===
    # Left side: Connection details
    # Step 1: Connect to hotspot
    step1_label = "1. Connect to WiFi:"
    draw.text((margin, y), step1_label, fill=0, font=font_tiny)
    y += 12
    
    # SSID display (larger, prominent)
    ssid_display = ssid if len(ssid) <= 18 else ssid[:15] + "..."
    draw.text((margin + 8, y), ssid_display, fill=0, font=font_medium)
    y += 16
    
    # Password (if not empty)
    if password:
        pwd_label = "Password:"
        draw.text((margin + 8, y), pwd_label, fill=0, font=font_tiny)
        pwd_display = password if len(password) <= 15 else password[:12] + "..."
        pwd_bbox = draw.textbbox((0, 0), pwd_display, font=font_small)
        pwd_width = pwd_bbox[2] - pwd_bbox[0]
        draw.text((margin + 60, y - 2), pwd_display, fill=0, font=font_small)
        y += 14
    
    # === RIGHT SIDE - QR Code (if available) ===
    qr_size = 65
    qr_x = width - qr_size - margin
    qr_y = margin + 25
    
    setup_url = f"http://{ip_address}:{port}"
    
    if qrcode:
        try:
            # Generate QR code
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=2,
                border=1,
            )
            qr.add_data(setup_url)
            qr.make(fit=True)

            # Create QR code image
            qr_img = qr.make_image(fill_color="black", back_color="white")
            qr_img = qr_img.convert("L")
            qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.NEAREST)

            # Paste QR code
            img.paste(qr_img, (qr_x, qr_y))

            # QR label
            qr_label = "Scan"
            qr_label_bbox = draw.textbbox((0, 0), qr_label, font=font_tiny)
            qr_label_width = qr_label_bbox[2] - qr_label_bbox[0]
            qr_label_x = qr_x + (qr_size // 2) - (qr_label_width // 2)
            draw.text((qr_label_x, qr_y + qr_size + 2), qr_label, fill=0, font=font_tiny)

        except Exception as e:
            logger.error(f"Failed to generate QR code: {e}")
            # Fallback box with pattern
            draw.rectangle([qr_x, qr_y, qr_x + qr_size, qr_y + qr_size], outline=0, width=2)
            # Simple pattern
            for i in range(5, qr_size-5, 8):
                for j in range(5, qr_size-5, 8):
                    if (i + j) % 16 == 0:
                        draw.rectangle([qr_x + i, qr_y + j, qr_x + i + 4, qr_y + j + 4], fill=0)
            draw.text((qr_x + 20, qr_y + qr_size + 2), "QR", fill=0, font=font_tiny)
    else:
        # No QR code - simple box
        draw.rectangle([qr_x, qr_y, qr_x + qr_size, qr_y + qr_size], outline=0, width=1)
        draw.text((qr_x + 15, qr_y + 28), "Scan to", fill=0, font=font_tiny)
        draw.text((qr_x + 15, qr_y + 40), "Connect", fill=0, font=font_tiny)
    
    # === BOTTOM SECTION - Browser Instructions ===
    y = height - 35
    
    # Separator line
    draw.line([margin, y, width - margin - qr_size - 5, y], fill=0, width=1)
    y += 5
    
    # Step 2: Open browser
    step2_label = "2. Open browser and visit:"
    draw.text((margin, y), step2_label, fill=0, font=font_tiny)
    y += 12
    
    # URL (prominent)
    url_display = setup_url
    if len(url_display) > 22:
        url_display = url_display[:19] + "..."
    draw.text((margin + 8, y), url_display, fill=0, font=font_small)
    
    # Clean border
    draw.rectangle([0, 0, width - 1, height - 1], outline=0, width=2)

    # Save the image
    img.save(filename)
    logger.info(f"WiFi setup image saved as: {filename}")

    # Auto-display if requested
    if auto_display:
        display_on_eink(filename)

    return filename


def create_wifi_success_image(
    ssid,
    ip_address,
    width=250,
    height=128,
    filename="wifi_success.png",
    auto_display=False,
):
    """
    Create an image showing successful WiFi connection
    Optimized for horizontal layout on monochrome e-ink displays

    Args:
        ssid: Connected WiFi network name
        ip_address: Assigned IP address
        width: Image width in pixels
        height: Image height in pixels
        filename: Output filename
        auto_display: If True, automatically display on e-ink after creating

    Returns:
        Filename of created image
    """

    # Create image
    img = Image.new("L", (width, height), 255)  # White background
    draw = ImageDraw.Draw(img)

    # Try to load fonts - optimized for horizontal success layout
    try:
        martian_font_path = os.path.join(
            os.getcwd(), "fonts", "MartianMonoNerdFont-CondensedBold.ttf"
        )
        font_header = ImageFont.truetype(martian_font_path, 18)
        font_large = ImageFont.truetype(martian_font_path, 16)
        font_medium = ImageFont.truetype(martian_font_path, 14)
        font_small = ImageFont.truetype(martian_font_path, 12)
        font_tiny = ImageFont.truetype(martian_font_path, 10)
        logger.info("Using MartianMono font for success screen")
    except Exception as e:
        logger.warning(f"Could not load MartianMono font: {e}")
        try:
            font_header = ImageFont.truetype(
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 18
            )
            font_large = ImageFont.truetype(
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 16
            )
            font_medium = ImageFont.truetype(
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 14
            )
            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 12
            )
            font_tiny = ImageFont.truetype(
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 10
            )
        except:
            font_header = ImageFont.load_default()
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()
            font_small = ImageFont.load_default()
            font_tiny = ImageFont.load_default()

    # === HORIZONTAL SUCCESS LAYOUT ===
    margin = 8
    center_x = width // 2
    
    # === TOP SECTION - Success Header ===
    y = margin
    
    # Success checkmark icon (left)
    check_size = 16
    check_x = margin + 8
    check_y = y + 2
    
    # Large checkmark circle
    draw.ellipse([check_x - check_size//2, check_y - check_size//2, 
                  check_x + check_size//2, check_y + check_size//2], outline=0, width=2)
    
    # Checkmark inside
    draw.line([check_x - 4, check_y, check_x - 1, check_y + 3], fill=0, width=2)
    draw.line([check_x - 1, check_y + 3, check_x + 5, check_y - 3], fill=0, width=2)
    
    # "SUCCESS" title (centered)
    title = "WiFi Connected!"
    title_bbox = draw.textbbox((0, 0), title, font=font_large)
    title_width = title_bbox[2] - title_bbox[0]
    title_x = center_x - (title_width // 2)
    draw.text((title_x, y), title, fill=0, font=font_large)
    
    # Status indicator (right)
    timestamp = datetime.now().strftime("%H:%M")
    time_bbox = draw.textbbox((0, 0), timestamp, font=font_small)
    time_width = time_bbox[2] - time_bbox[0]
    draw.text((width - margin - time_width, y + 2), timestamp, fill=0, font=font_small)
    
    y += 22
    
    # Separator line
    draw.line([margin, y, width - margin, y], fill=0, width=1)
    y += 8
    
    # === MIDDLE SECTION - Connection Details ===
    # Network name (prominent, centered)
    network_label = "Connected to:"
    label_bbox = draw.textbbox((0, 0), network_label, font=font_tiny)
    label_width = label_bbox[2] - label_bbox[0]
    label_x = center_x - (label_width // 2)
    draw.text((label_x, y), network_label, fill=0, font=font_tiny)
    y += 12
    
    # SSID (large, centered)
    ssid_display = ssid if len(ssid) <= 20 else ssid[:17] + "..."
    ssid_bbox = draw.textbbox((0, 0), ssid_display, font=font_medium)
    ssid_width = ssid_bbox[2] - ssid_bbox[0]
    ssid_x = center_x - (ssid_width // 2)
    draw.text((ssid_x, y), ssid_display, fill=0, font=font_medium)
    y += 18
    
    # IP Address (centered)
    ip_label = f"IP: {ip_address}"
    ip_bbox = draw.textbbox((0, 0), ip_label, font=font_small)
    ip_width = ip_bbox[2] - ip_bbox[0]
    ip_x = center_x - (ip_width // 2)
    draw.text((ip_x, y), ip_label, fill=0, font=font_small)
    y += 18
    
    # === BOTTOM SECTION - Status and Actions ===
    # Separator line
    draw.line([margin, y, width - margin, y], fill=0, width=1)
    y += 6
    
    # Status indicators (horizontal layout)
    # Left side: Ready status
    ready_icon = "●"
    draw.text((margin, y), ready_icon, fill=0, font=font_small)
    draw.text((margin + 15, y), "Device Ready", fill=0, font=font_tiny)
    
    # Right side: Setup complete
    complete_text = "Setup Complete"
    complete_bbox = draw.textbbox((0, 0), complete_text, font=font_tiny)
    complete_width = complete_bbox[2] - complete_bbox[0]
    draw.text((width - margin - complete_width, y), complete_text, fill=0, font=font_tiny)
    
    y += 12
    
    # Web access info (centered)
    web_url = f"http://{ip_address}:8080"
    if len(web_url) > 25:
        web_display = f"{ip_address}:8080"
    else:
        web_display = web_url
    
    web_bbox = draw.textbbox((0, 0), web_display, font=font_tiny)
    web_width = web_bbox[2] - web_bbox[0]
    web_x = center_x - (web_width // 2)
    draw.text((web_x, y), web_display, fill=0, font=font_tiny)
    
    # Clean border
    draw.rectangle([0, 0, width - 1, height - 1], outline=0, width=2)

    # Save the image
    img.save(filename)
    logger.info(f"WiFi success image saved as: {filename}")

    # Auto-display if requested
    if auto_display:
        display_on_eink(filename)

    return filename


def main():
    parser = argparse.ArgumentParser(
        description="Display WiFi information on e-ink screen"
    )
    parser.add_argument(
        "--output", type=str, default="wifi_info.png", help="Output image filename"
    )
    parser.add_argument(
        "--display",
        action="store_true",
        help="Automatically display on e-ink after creating",
    )
    parser.add_argument(
        "--no-image",
        action="store_true",
        help="Only display on e-ink, do not save image file",
    )
    parser.add_argument("--width", type=int, default=250, help="Image width")
    parser.add_argument("--height", type=int, default=128, help="Image height")
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Create setup screen instead of info screen",
    )
    parser.add_argument("--ssid", type=str, help="WiFi SSID for setup screen")
    parser.add_argument("--password", type=str, help="WiFi password for setup screen")
    parser.add_argument("--ip", type=str, help="IP address for setup screen")
    parser.add_argument("--success", action="store_true", help="Create success screen")
    parser.add_argument(
        "--connected-ip", type=str, help="Connected IP address for success screen"
    )

    args = parser.parse_args()

    try:
        if args.setup:
            if not args.ssid or not args.password or not args.ip:
                print("Setup mode requires --ssid, --password, and --ip arguments")
                return 1

            if args.no_image:
                temp_filename = "/tmp/wifi_setup_temp.png"
                create_wifi_setup_image(
                    args.ssid,
                    args.password,
                    args.ip,
                    filename=temp_filename,
                    auto_display=True,
                )
                try:
                    os.remove(temp_filename)
                except:
                    pass
            else:
                filename = create_wifi_setup_image(
                    args.ssid,
                    args.password,
                    args.ip,
                    filename=args.output,
                    auto_display=args.display,
                )
                print(f"WiFi setup image created: {filename}")

        elif args.success:
            if not args.ssid or not args.connected_ip:
                print("Success mode requires --ssid and --connected-ip arguments")
                return 1

            if args.no_image:
                temp_filename = "/tmp/wifi_success_temp.png"
                create_wifi_success_image(
                    args.ssid,
                    args.connected_ip,
                    filename=temp_filename,
                    auto_display=True,
                )
                try:
                    os.remove(temp_filename)
                except:
                    pass
            else:
                filename = create_wifi_success_image(
                    args.ssid,
                    args.connected_ip,
                    filename=args.output,
                    auto_display=args.display,
                )
                print(f"WiFi success image created: {filename}")

        elif args.no_image:
            # Create temporary image and display directly
            temp_filename = "/tmp/wifi_info_temp.png"
            create_wifi_info_image(
                args.width, args.height, temp_filename, auto_display=True
            )
            # Clean up temp file
            try:
                os.remove(temp_filename)
            except:
                pass
        else:
            # Create image file
            filename = create_wifi_info_image(
                args.width, args.height, args.output, auto_display=args.display
            )

            print(f"WiFi information image created: {filename}")

            if not args.display:
                print(f"To display on e-ink: python eink_display_simple.py {filename}")

        return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
