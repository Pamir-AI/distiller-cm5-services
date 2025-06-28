#!/bin/bash

# Distiller WiFi Service Installation Script
# Installs the WiFi setup service as a systemd service

set -e

# Configuration
SERVICE_NAME="distiller-wifi"
SERVICE_FILE="distiller-wifi.service"
SERVICE_USER="root"
INSTALL_DIR="/home/distiller/distiller-cm5-services"
SYSTEMD_DIR="/etc/systemd/system"
CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing Distiller WiFi Service..."

# Check if running as root
if [ "$EUID" -ne 0 ]; then
	echo "Error: This script must be run as root"
	echo "Please run: sudo bash install-service.sh"
	exit 1
fi

# Install system dependencies
echo "Installing system dependencies..."
apt-get update -qq
apt-get install -y avahi-daemon avahi-utils libnss-mdns

# Create installation directory
echo "Creating installation directory: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

# Copy service files
echo "Copying service files..."
cp "$CURRENT_DIR/distiller_wifi_service.py" "$INSTALL_DIR/"
cp -r "$CURRENT_DIR/network" "$INSTALL_DIR/"
cp -r "$CURRENT_DIR/templates" "$INSTALL_DIR/"
cp -r "$CURRENT_DIR/static" "$INSTALL_DIR/"
cp "$CURRENT_DIR/requirements.txt" "$INSTALL_DIR/"

# Set permissions
echo "Setting permissions..."
chown -R $SERVICE_USER:$SERVICE_USER "$INSTALL_DIR"
chmod +x "$INSTALL_DIR/distiller_wifi_service.py"

# Install Python dependencies
echo "Installing Python dependencies..."
if command -v uv &>/dev/null; then
	echo "Using uv pip for Python 2 compatibility"
	uv pip install -r "$INSTALL_DIR/requirements.txt"
else
	echo "Error: uv pip is not installed. Trying pip3 instead."
	if ! command -v pip3 &>/dev/null; then
		echo "Error: pip3 is not installed. Please install Python 3 and pip3."
		exit 1
	fi
	pip3 install -r "$INSTALL_DIR/requirements.txt"
fi

# Check if service file exists
if [ ! -f "$SERVICE_FILE" ]; then
	echo "Error: $SERVICE_FILE not found in current directory"
	exit 1
fi

# Copy service file to systemd directory
echo "Installing systemd service..."
cp "$SERVICE_FILE" "$SYSTEMD_DIR/"

# Set proper permissions
chmod 644 "$SYSTEMD_DIR/$SERVICE_FILE"

# Reload systemd and enable service
echo "Configuring systemd service..."
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

# Enable and start Avahi daemon for mDNS
echo "Configuring Avahi daemon for mDNS..."
systemctl enable avahi-daemon
systemctl start avahi-daemon

echo ""
echo "Installation complete!"
echo ""
echo "To start the service:"
echo "  sudo systemctl start $SERVICE_NAME"
echo ""
echo "To check service status:"
echo "  sudo systemctl status $SERVICE_NAME"
echo ""
echo "To view logs:"
echo "  sudo journalctl -u $SERVICE_NAME -f"
echo ""
echo "The service will start automatically on boot."
