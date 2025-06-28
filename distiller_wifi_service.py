#!/usr/bin/env python3
"""
Distiller WiFi Service

Handles single-radio WiFi hardware limitation with proper state management,
web server coordination, and seamless user experience during transitions.
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
import threading
from pathlib import Path
from typing import Optional
from enum import Enum

from network.wifi_manager import WiFiManager
from network.device_config import get_device_config

try:
    from flask import Flask, render_template, request, jsonify, redirect, url_for

    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

# E-ink display imports (optional)
try:
    from wifi_info_display import (
        create_wifi_setup_image,
        create_wifi_success_image,
        create_wifi_info_image,
    )

    EINK_AVAILABLE = True
except ImportError:
    EINK_AVAILABLE = False


class ServiceState(Enum):
    """Service state definitions"""

    INITIALIZING = "initializing"
    HOTSPOT_MODE = "hotspot_mode"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class DistillerWiFiServiceFixed:
    """Fixed WiFi Setup Service with proper state transitions"""

    def __init__(
        self,
        hotspot_ssid: str = None,
        hotspot_password: str = None,
        device_name: str = None,
        web_port: int = None,
        enable_eink: bool = True,
    ):
        # Initialize device configuration
        self.device_config = get_device_config()
        
        # Use device configuration with fallbacks to parameters
        self.hotspot_ssid = hotspot_ssid or self.device_config.get_hotspot_ssid()
        self.hotspot_password = hotspot_password or self.device_config.get_hotspot_password()
        self.device_name = device_name or self.device_config.get_friendly_name()
        self.web_port = web_port or self.device_config.get_web_port()
        self.enable_eink = enable_eink and EINK_AVAILABLE

        # Service state
        self.current_state = ServiceState.INITIALIZING
        self.running = False
        self.target_ssid: Optional[str] = None
        self.target_password: Optional[str] = None
        self.connection_start_time: Optional[float] = None
        self._connection_in_progress = False  # Flag to prevent race conditions
        self.hotspot_ip: Optional[str] = None  # Store actual hotspot IP
        self._successful_connection_ip: Optional[str] = None  # Track successful connection IP
        self._successful_connection_ssid: Optional[str] = None  # Track successful connection SSID

        # Setup logging
        self.setup_logging()
        self.logger = logging.getLogger(__name__)

        # Initialize WiFi manager
        self.wifi_manager = WiFiManager()

        # Flask app for web interface
        self.app = self._create_flask_app() if FLASK_AVAILABLE else None
        self.web_server_thread: Optional[threading.Thread] = None

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        self.logger.info("Fixed WiFi Service initialized")
        self.logger.info(f"Device ID: {self.device_config.get_device_id()}")
        self.logger.info(f"Hostname: {self.device_config.get_hostname()}")
        self.logger.info(f"Hotspot SSID: {self.hotspot_ssid}")
        self.logger.info(f"mDNS URL: {self.device_config.get_device_mdns_url()}")

    def setup_logging(self):
        """Configure logging"""
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

        # Try to write to system log first, fallback to local
        log_paths = ["/var/log/distiller-wifi.log", "./distiller-wifi.log"]
        log_file = None

        for path in log_paths:
            try:
                Path(path).touch(exist_ok=True)
                log_file = path
                break
            except (PermissionError, OSError):
                continue

        handlers = [logging.StreamHandler(sys.stdout)]
        if log_file:
            handlers.append(logging.FileHandler(log_file))

        # Production logging level - only INFO and above
        logging.basicConfig(
            level=logging.INFO, format=log_format, handlers=handlers, force=True
        )

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def _create_flask_app(self) -> Flask:
        """Create Flask web application"""
        app = Flask(__name__, template_folder="templates", static_folder="static")

        # Disable caching
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

        @app.after_request
        def add_no_cache_headers(response):
            response.cache_control.max_age = 0
            response.cache_control.no_cache = True
            response.cache_control.must_revalidate = True
            
            # Add security headers to handle HTTPS-Only mode and CSP
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['X-XSS-Protection'] = '1; mode=block'
            
            # Allow HTTP requests for local IoT device operation (no HTTPS upgrade)
            response.headers['Content-Security-Policy'] = (
                "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: http: https:; "
                "connect-src 'self' http: https: ws: wss:; "
                "img-src 'self' data: blob: http: https:; "
                "font-src 'self' data: http: https:; "
                "frame-src 'none'; "
                "object-src 'none'"
            )
            
            # For local development and IoT devices, allow mixed content
            response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
            
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
            
            return response

        # Routes
        @app.route("/")
        def index():
            return self._handle_index()

        @app.route("/confirm")
        def confirm():
            return self._handle_confirm()

        @app.route("/connect", methods=["POST"])
        def connect():
            return self._handle_connect()

        @app.route("/status")
        def status():
            return self._handle_status()

        @app.route("/api/status")
        def api_status():
            return self._handle_api_status()

        @app.route("/api/networks")
        def api_networks():
            return self._handle_api_networks()

        @app.route("/api/connect", methods=["POST"])
        def api_connect():
            return self._handle_connect()

        @app.route("/api/scan", methods=["GET"])
        def api_scan():
            """Manually trigger network scan"""
            try:
                networks = asyncio.run(self._scan_networks_properly())
                return jsonify(
                    {
                        "success": True,
                        "networks": [
                            {
                                "ssid": net.ssid,
                                "signal_strength": net.signal,
                                "security": net.security,
                                "frequency": net.frequency,
                            }
                            for net in networks
                        ],
                    }
                )
            except Exception as e:
                self.logger.error(f"Error in API scan: {e}")
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route("/refresh-display")
        def refresh_display():
            """Refresh e-ink display with current WiFi info"""
            try:
                if self.enable_eink and self.current_state == ServiceState.CONNECTED:
                    self._update_eink_info()
                    return jsonify({"success": True, "message": "Display refreshed"})
                else:
                    return jsonify(
                        {
                            "success": False,
                            "message": "Display not available or not connected",
                        }
                    )
            except Exception as e:
                self.logger.error(f"Error refreshing display: {e}")
                return jsonify({"success": False, "error": str(e)}), 500

        # Catch-all for captive portal
        @app.route("/<path:path>")
        def catch_all(path):
            self.logger.info(f"Redirecting path: {path}")
            return redirect(url_for("index"))

        return app

    def _handle_index(self):
        """Handle main index page"""
        try:
            if self.current_state == ServiceState.HOTSPOT_MODE:
                # Don't scan networks on page load - let JavaScript handle it
                # This prevents automatic hotspot restarts
                return render_template(
                    "index.html",
                    networks=[],  # Empty initially, will be loaded by JavaScript
                    device_name=self.device_name,
                    current_state=self.current_state.value,
                    web_port=self.web_port,
                )
            elif self.current_state == ServiceState.CONNECTED:
                # Show connected status
                return redirect(url_for("status"))
            elif self.current_state == ServiceState.INITIALIZING:
                # Service is transitioning (e.g., changing networks)
                # Redirect to status page to show progress
                return redirect(url_for("status"))
            else:
                # Show loading or error state
                return render_template(
                    "index.html",
                    networks=[],
                    device_name=self.device_name,
                    current_state=self.current_state.value,
                    message="Service initializing...",
                    web_port=self.web_port,
                )
        except Exception as e:
            self.logger.error(f"Error in index handler: {e}")
            return render_template(
                "index.html",
                networks=[],
                device_name=self.device_name,
                error="Failed to load networks",
                web_port=self.web_port,
            )

    def _handle_confirm(self):
        """Handle network confirmation page"""
        try:
            ssid = request.args.get("ssid", "")
            encrypted = request.args.get("encrypted", "unencrypted")

            if not ssid:
                return redirect(url_for("index"))

            return render_template(
                "confirm.html",
                ssid=ssid,
                encrypted=encrypted,
                device_name=self.device_name,
                web_port=self.web_port,
            )
        except Exception as e:
            self.logger.error(f"Error in confirm handler: {e}")
            return redirect(url_for("index"))

    def _handle_connect(self):
        """Handle connection request"""
        try:
            self.logger.debug(f"Raw request data: {request.data}")
            self.logger.debug(f"Request content type: {request.content_type}")
            self.logger.debug(f"Request is_json: {request.is_json}")
            self.logger.debug(f"Request form: {request.form}")
            self.logger.debug(f"Request args: {request.args}")

            # Handle both form data and JSON data
            if request.is_json:
                data = request.get_json()
                self.logger.debug(f"JSON data: {data}")
                ssid = data.get("ssid", "") if data else ""
                password = data.get("password", "") if data else ""
            else:
                ssid = request.form.get("ssid", "")
                password = request.form.get("password", "")

            self.logger.info(
                f"Connection request received: SSID='{ssid}', Password={'***' if password else 'None'}"
            )

            if not ssid:
                self.logger.warning("No SSID provided in connection request")
                if request.is_json:
                    return jsonify({"success": False, "error": "No SSID provided"}), 400
                return redirect(url_for("index"))

            # Store connection target
            self.target_ssid = ssid
            self.target_password = password
            self.connection_start_time = time.time()

            self.logger.info(f"Starting connection process to '{ssid}' in background")

            # Start connection in background
            self._start_connection_background()

            if request.is_json:
                return jsonify({"success": True, "message": "Connection started"})
            
            # Instead of immediately redirecting to status, show a connecting page
            # that will handle the hotspot disconnection gracefully
            return render_template(
                "connecting.html",
                ssid=ssid,
                device_name=self.device_name,
                web_port=self.web_port,
                hotspot_ip=self.hotspot_ip or "192.168.4.1",
            )

        except Exception as e:
            self.logger.error(f"Error in connect handler: {e}")
            if request.is_json:
                return jsonify({"success": False, "error": str(e)}), 500
            return redirect(url_for("index"))

    def _handle_status(self):
        """Handle status page"""
        try:
            # Get current status
            status_info = self._get_current_status()

            return render_template(
                "status.html",
                status=status_info,
                device_name=self.device_name,
                web_port=self.web_port,
            )
        except Exception as e:
            self.logger.error(f"Error in status handler: {e}")
            return render_template(
                "status.html",
                status={
                    "connected": False,
                    "connecting": False,
                    "error": "Status unavailable"
                },
                device_name=self.device_name,
                web_port=self.web_port,
            )

    def _handle_api_status(self):
        """Handle status API endpoint"""
        try:
            status_info = self._get_current_status()
            
            # DEBUG: Log what we're actually returning
            self.logger.info(f"API Status response: connected_to_target={status_info.get('connected_to_target')}, "
                            f"current_state={status_info.get('current_state')}, "
                            f"ssid={status_info.get('ssid')}, "
                            f"ip={status_info.get('ip_address')}, "
                            f"redirect_url={status_info.get('redirect_url')}")
            
            return jsonify({"success": True, **status_info})
        except Exception as e:
            self.logger.error(f"Error in API status: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    def _handle_api_networks(self):
        """Handle API request for available networks"""
        try:
            # Don't scan networks if we're in the middle of a connection
            if self.current_state == ServiceState.CONNECTING:
                self.logger.info(
                    "Connection in progress, returning cached/empty network list"
                )
                return jsonify(
                    {
                        "success": True,
                        "networks": [],
                        "message": "Connection in progress",
                    }
                )

            # Use cached or simplified scan for hotspot mode
            if self.current_state == ServiceState.HOTSPOT_MODE:
                # Don't stop hotspot for network scan - use a simpler approach
                try:
                    # Get a quick scan without stopping hotspot
                    networks = asyncio.run(self._get_networks_without_hotspot_restart())
                    return jsonify(
                        {
                            "success": True,
                            "networks": [
                                {
                                    "ssid": net.ssid,
                                    "signal_strength": net.signal,
                                    "security": net.security,
                                    "frequency": net.frequency,
                                }
                                for net in networks
                            ],
                        }
                    )
                except Exception as e:
                    self.logger.error(f"Error getting networks: {e}")
                    # Return some common networks as fallback
                    return jsonify(
                        {
                            "success": True,
                            "networks": [],
                            "message": "Scan temporarily unavailable",
                        }
                    )
            else:
                # Normal scan when not in hotspot mode
                networks = asyncio.run(self.wifi_manager.get_available_networks())
                return jsonify(
                    {
                        "success": True,
                        "networks": [
                            {
                                "ssid": net.ssid,
                                "signal_strength": net.signal,
                                "security": net.security,
                                "frequency": net.frequency,
                            }
                            for net in networks
                        ],
                    }
                )
        except Exception as e:
            self.logger.error(f"Error in API networks: {e}")
            return jsonify({"success": False, "error": str(e)}), 500



    def _get_current_status(self) -> dict:
        """Get current service status"""
        try:
            # Get WiFi connection status with timeout and error handling
            try:
                wifi_status = asyncio.run(self.wifi_manager.get_connection_status())
            except Exception as wifi_error:
                self.logger.warning(f"WiFi status check failed: {wifi_error}")
                # During network transition, if we're in CONNECTED state, trust that state
                if self.current_state == ServiceState.CONNECTED:
                    self.logger.info("WiFi status check failed but service state is CONNECTED - assuming successful connection")
                    # Use stored connection info if available
                    connection_ip = self._successful_connection_ip or self.device_config.get_device_mdns_id()
                    connection_ssid = self._successful_connection_ssid or self.target_ssid
                    return {
                        "connected": True,
                        "connected_to_target": True,  # Trust the CONNECTED state
                        "connected_to_hotspot": False,
                        "connecting": False,
                        "ssid": connection_ssid,
                        "ip_address": connection_ip,
                        "interface": None,
                        "current_state": self.current_state.value,
                        "target_ssid": self.target_ssid,
                        "elapsed": 0,
                        "redirect_url": f"http://{connection_ip}:{self.web_port}",
                        "timestamp": int(time.time()),
                        "device_id": self.device_config.get_device_id(),
                        "mdns_url": self.device_config.get_device_mdns_url(),
                        "hostname": self.device_config.get_hostname(),
                        "message": "Network transition in progress"
                    }
                # Return status based on service state when WiFi check fails
                return self._get_fallback_status()

            # Determine service state
            connecting = (
                self.current_state == ServiceState.CONNECTING
                and self.connection_start_time
                and time.time() - self.connection_start_time < 120
            )  # 2 min timeout

            # Check if we're connected to a target network (not hotspot)
            connected_to_target = (
                wifi_status.connected 
                and self.current_state == ServiceState.CONNECTED
                and wifi_status.ssid 
                and not wifi_status.ssid.startswith(self.hotspot_ssid)  # Not connected to our hotspot
                and wifi_status.ip_address
                and wifi_status.ip_address != self.hotspot_ip  # Not using hotspot IP
            )

            # CRITICAL FIX: If we're in CONNECTED state but WiFi status doesn't show connection,
            # trust the service state (this handles network transition timing issues)
            if self.current_state == ServiceState.CONNECTED and not connected_to_target:
                self.logger.info("Service state is CONNECTED but WiFi status check inconsistent - trusting service state")
                connected_to_target = True

            # Check if we're connected to hotspot
            connected_to_hotspot = (
                wifi_status.connected 
                and wifi_status.ssid 
                and (wifi_status.ssid.startswith(self.hotspot_ssid) or wifi_status.ip_address == self.hotspot_ip)
            )

            connected = connected_to_target or connected_to_hotspot

            # Handle redirection after successful connection to TARGET network (not hotspot)
            redirect_url = None
            if connected_to_target:
                if wifi_status.ip_address:
                    redirect_url = f"http://{wifi_status.ip_address}:{self.web_port}"
                elif self._successful_connection_ip:
                    # Use stored connection IP
                    redirect_url = f"http://{self._successful_connection_ip}:{self.web_port}"
                else:
                    # Fallback: Use mDNS if available
                    redirect_url = self.device_config.get_device_mdns_url()

            return {
                "connected": connected,
                "connected_to_target": connected_to_target,  # New field to distinguish target vs hotspot
                "connected_to_hotspot": connected_to_hotspot,  # New field 
                "connecting": connecting,
                "ssid": wifi_status.ssid,
                "ip_address": wifi_status.ip_address,
                "interface": wifi_status.interface,
                "current_state": self.current_state.value,
                "target_ssid": self.target_ssid,
                "elapsed": (
                    time.time() - self.connection_start_time
                    if self.connection_start_time
                    else 0
                ),
                "redirect_url": redirect_url,
                "timestamp": int(time.time()),
                "device_id": self.device_config.get_device_id(),
                "mdns_url": self.device_config.get_device_mdns_url(),
                "hostname": self.device_config.get_hostname(),
            }

        except Exception as e:
            self.logger.error(f"Error getting status: {e}")
            return self._get_fallback_status()

    def _get_fallback_status(self) -> dict:
        """Get fallback status when WiFi status check fails"""
        # Return status based on current service state
        if self.current_state == ServiceState.HOTSPOT_MODE:
            return {
                "connected": True,  # Connected to hotspot
                "connected_to_target": False,  # Not connected to target network
                "connected_to_hotspot": True,  # Connected to hotspot
                "connecting": False,
                "ssid": self.hotspot_ssid,  # Use hotspot SSID
                "ip_address": self.hotspot_ip or "192.168.4.1",  # Use actual hotspot IP
                "interface": None,
                "current_state": self.current_state.value,
                "target_ssid": self.target_ssid,
                "elapsed": 0,
                "redirect_url": None,
                "timestamp": int(time.time()),
                "message": "Hotspot mode active",
                "device_id": self.device_config.get_device_id(),
                "mdns_url": self.device_config.get_device_mdns_url(),
                "hostname": self.device_config.get_hostname(),
            }
        elif self.current_state == ServiceState.CONNECTING:
            return {
                "connected": False,
                "connected_to_target": False,
                "connected_to_hotspot": False,
                "connecting": True,
                "ssid": None,
                "ip_address": None,
                "interface": None,
                "current_state": self.current_state.value,
                "target_ssid": self.target_ssid,
                "elapsed": (
                    time.time() - self.connection_start_time
                    if self.connection_start_time
                    else 0
                ),
                "redirect_url": None,
                "timestamp": int(time.time()),
                "device_id": self.device_config.get_device_id(),
                "mdns_url": self.device_config.get_device_mdns_url(),
                "hostname": self.device_config.get_hostname(),
            }
        elif self.current_state == ServiceState.INITIALIZING:
            return {
                "connected": False,
                "connected_to_target": False,
                "connected_to_hotspot": False,
                "connecting": False,
                "ssid": None,
                "ip_address": None,
                "interface": None,
                "current_state": self.current_state.value,
                "target_ssid": None,
                "elapsed": 0,
                "redirect_url": None,
                "timestamp": int(time.time()),
                "message": "Changing network configuration...",
            }
        else:
            return {
                "connected": False,
                "connected_to_target": False,
                "connected_to_hotspot": False,
                "connecting": False,
                "error": "Status check failed - service transitioning",
                "current_state": self.current_state.value,
                "timestamp": int(time.time()),
            }

    def _start_connection_background(self):
        """Start connection process in background thread"""

        def connection_worker():
            try:
                self._connection_in_progress = True
                self.logger.info("Background connection thread started")
                asyncio.run(self._perform_connection())
            except Exception as e:
                self.logger.error(f"Connection background thread error: {e}")
            finally:
                self._connection_in_progress = False
                self.logger.info("Connection background thread finished")

        thread = threading.Thread(target=connection_worker, daemon=True)
        thread.start()
        self.logger.info("Connection background thread launched")

    async def _perform_connection(self):
        """Perform WiFi connection with proper state management"""
        try:
            if not self.target_ssid:
                return

            self.logger.info(f"Starting connection to {self.target_ssid}")
            self.current_state = ServiceState.CONNECTING

            # Update e-ink display if available
            if self.enable_eink:
                self._update_eink_connecting(self.target_ssid)

            # CRITICAL: Stop hotspot first before attempting connection
            if self.wifi_manager.is_hotspot_active():
                self.logger.info("Stopping hotspot before connecting to target network")
                await self.wifi_manager.stop_hotspot()
                # Wait for interface to be ready
                await asyncio.sleep(3)

            # Perform the connection (hotspot is now stopped)
            # Handle None password properly
            password = self.target_password or ""
            success = await self.wifi_manager.connect_to_network(
                self.target_ssid, password
            )

            if success:
                self.logger.info(f"Initial connection successful to {self.target_ssid}")
                
                # Wait for connection to stabilize before marking as fully connected
                self.logger.info("Waiting for connection to stabilize...")
                await asyncio.sleep(5)  # Wait for DHCP and network setup
                
                # Verify connection is still active and stable
                verification_attempts = 3
                for attempt in range(verification_attempts):
                    status = await self.wifi_manager.get_connection_status()
                    if status.connected and status.ip_address:
                        self.logger.info(f"Connection verified (attempt {attempt + 1}): {status.ip_address}")
                        break
                    else:
                        self.logger.warning(f"Connection verification failed (attempt {attempt + 1})")
                        if attempt < verification_attempts - 1:
                            await asyncio.sleep(3)
                else:
                    # All verification attempts failed
                    self.logger.error("Connection verification failed, treating as failed connection")
                    await self._start_hotspot_mode()
                    return
                
                # Connection is stable, mark as connected
                self.current_state = ServiceState.CONNECTED
                self.logger.info(f"Connection to {self.target_ssid} fully established and stable")
                
                # CRITICAL: Store connection info for status API during transition
                self._successful_connection_ip = status.ip_address
                self._successful_connection_ssid = self.target_ssid

                # Update e-ink display
                if self.enable_eink:
                    # Handle None IP address properly
                    ip_address = status.ip_address or "unknown"
                    self._update_eink_success(self.target_ssid, ip_address)

                # Handle network transition
                await self._handle_network_transition()

            else:
                self.logger.error(f"Failed to connect to {self.target_ssid}")

                # Restore hotspot mode after connection failure
                self.logger.info("Restoring hotspot mode after connection failure")
                await self._start_hotspot_mode()

        except Exception as e:
            self.logger.error(f"Connection error: {e}")
            # Restore hotspot mode on error
            await self._start_hotspot_mode()
        finally:
            self.target_ssid = None
            self.target_password = None
            self.connection_start_time = None

    async def _handle_network_transition(self):
        """Handle transition from hotspot to client network"""
        try:
            # Get new network status
            status = await self.wifi_manager.get_connection_status()

            if status.connected and status.ip_address:
                self.logger.info(f"Network transition: now at {status.ip_address}")

                # Use transition method to properly handle mDNS service move to new network
                mdns_success = self.device_config.transition_mdns_to_network(status.ip_address)
                if mdns_success:
                    mdns_url = self.device_config.get_device_mdns_url()
                    self.logger.info(f"mDNS service updated for new network: {mdns_url}")
                else:
                    self.logger.warning("Failed to update mDNS service for new network")

                # Web server will continue running on all interfaces
                # The status page will handle redirection to new IP

                # Log success
                self.logger.info(f"WiFi setup completed successfully")
                self.logger.info(
                    f"Device accessible at: http://{status.ip_address}:{self.web_port}"
                )
                
                # Also log mDNS URL for convenience
                mdns_url = self.device_config.get_device_mdns_url()
                self.logger.info(f"Device also accessible via mDNS: {mdns_url}")

        except Exception as e:
            self.logger.error(f"Error handling network transition: {e}")

    async def check_initial_state(self) -> ServiceState:
        """Check initial state and determine startup mode"""
        try:
            self.logger.info("Checking initial WiFi state...")

            status = await self.wifi_manager.get_connection_status()

            if status.connected:
                if status.ssid and status.ssid.startswith(self.hotspot_ssid):
                    # Connected to our hotspot - stay in hotspot mode
                    self.logger.info(f"Connected to setup hotspot: {status.ssid}")
                    return ServiceState.HOTSPOT_MODE
                else:
                    # Connected to real network - connected mode
                    self.logger.info(f"Already connected to: {status.ssid}")
                    return ServiceState.CONNECTED
            else:
                # No connection - start hotspot mode
                self.logger.info("No WiFi connection detected")
                return ServiceState.HOTSPOT_MODE

        except Exception as e:
            self.logger.error(f"Error checking initial state: {e}")
            return ServiceState.HOTSPOT_MODE

    async def _start_hotspot_mode(self):
        """Start hotspot mode"""
        try:
            self.logger.info("Starting hotspot mode")
            self.current_state = ServiceState.HOTSPOT_MODE

            success, hotspot_ip = await self.wifi_manager.start_hotspot(
                self.hotspot_ssid, self.hotspot_password
            )

            if success:
                self.hotspot_ip = hotspot_ip
                self.logger.info(f"Hotspot started: {self.hotspot_ssid}")
                self.logger.info(
                    f"Web interface: http://{hotspot_ip}:{self.web_port}"
                )
                
                # Start mDNS service for device discovery
                mdns_success = self.device_config.start_mdns_service(hotspot_ip, self.web_port)
                if mdns_success:
                    mdns_url = self.device_config.get_device_mdns_url()
                    self.logger.info(f"mDNS service started: {mdns_url}")
                else:
                    self.logger.warning("Failed to start mDNS service")

                # Update e-ink display
                if self.enable_eink:
                    self._update_eink_setup()

            else:
                self.logger.error("Failed to start hotspot")
                self.current_state = ServiceState.ERROR

        except Exception as e:
            self.logger.error(f"Error starting hotspot: {e}")
            self.current_state = ServiceState.ERROR

    async def _transition_to_hotspot(self):
        """Transition from connected state back to hotspot mode"""
        try:
            self.logger.info("Transitioning to hotspot mode for network change")

            # Get current connection status
            current_status = await self.wifi_manager.get_connection_status()
            if current_status.connected:
                self.logger.info(
                    f"Disconnecting from current network: {current_status.ssid}"
                )

                # Stop current connection - this will automatically disconnect
                # We don't need to explicitly disconnect since starting hotspot will handle it

            # Start hotspot mode
            await self._start_hotspot_mode()

            self.logger.info("Successfully transitioned to hotspot mode")

        except Exception as e:
            self.logger.error(f"Error transitioning to hotspot: {e}")
            # Try to ensure we end up in some usable state
            try:
                await self._start_hotspot_mode()
            except Exception as fallback_error:
                self.logger.error(f"Fallback hotspot start failed: {fallback_error}")
                self.current_state = ServiceState.ERROR

    def _start_web_server(self):
        """Start web server in background thread"""
        if not FLASK_AVAILABLE or not self.app:
            self.logger.error("Flask not available, cannot start web server")
            return

        def run_server():
            # Show the mDNS URL for user convenience
            mdns_url = self.device_config.get_device_mdns_url()
            accessible_ip = self.hotspot_ip or "0.0.0.0"
            self.logger.info(f"Starting web server on {accessible_ip}:{self.web_port}")
            self.logger.info(f"Device accessible via mDNS: {mdns_url}")
            if self.app:  # Additional None check for type safety
                self.app.run(
                    host="0.0.0.0",
                    port=self.web_port,
                    debug=False,
                    use_reloader=False,
                    threaded=True,
                )

        self.web_server_thread = threading.Thread(target=run_server, daemon=True)
        self.web_server_thread.start()

    def _update_eink_setup(self):
        """Update e-ink display for setup mode"""
        if not self.enable_eink:
            return
        try:
            self.logger.info("Updating e-ink display for setup mode")
            # Create and display setup image with hotspot information
            create_wifi_setup_image(
                ssid=self.hotspot_ssid,
                password=self.hotspot_password,
                ip_address=self.hotspot_ip or "localhost",  # Use actual hotspot IP
                port=self.web_port,
                filename="wifi_setup_display.png",
                auto_display=True,  # Automatically display on e-ink
            )
            self.logger.info("E-ink display updated for setup mode")
        except Exception as e:
            self.logger.error(f"E-ink setup update error: {e}")

    def _update_eink_connecting(self, ssid: str):
        """Update e-ink display for connecting state"""
        if not self.enable_eink:
            return
        try:
            self.logger.info(f"Updating e-ink display for connecting to {ssid}")
            # For connecting state, we'll create a simple image showing connection progress
            # Since there's no specific connecting image function, we'll use the setup function
            # with modified text to indicate connection in progress
            from PIL import Image, ImageDraw, ImageFont

            # Create a simple connecting image
            width, height = 240, 416
            img = Image.new("L", (width, height), 255)  # White background
            draw = ImageDraw.Draw(img)

            # Try to load font
            try:
                font_large = ImageFont.truetype(
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 18
                )
                font_medium = ImageFont.truetype(
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                    14,
                )
            except:
                font_large = ImageFont.load_default()
                font_medium = ImageFont.load_default()

            # Border
            draw.rectangle([0, 0, width - 1, height - 1], outline=0, width=2)

            # Title
            y_pos = 30
            title = "CONNECTING..."
            bbox = draw.textbbox((0, 0), title, font=font_large)
            title_width = bbox[2] - bbox[0]
            draw.text(
                ((width - title_width) // 2, y_pos), title, fill=0, font=font_large
            )

            y_pos += 50

            # Network name
            network_text = f"Network: {ssid}"
            bbox = draw.textbbox((0, 0), network_text, font=font_medium)
            text_width = bbox[2] - bbox[0]
            draw.text(
                ((width - text_width) // 2, y_pos),
                network_text,
                fill=0,
                font=font_medium,
            )

            y_pos += 40

            # Status message
            status_text = "Please wait..."
            bbox = draw.textbbox((0, 0), status_text, font=font_medium)
            text_width = bbox[2] - bbox[0]
            draw.text(
                ((width - text_width) // 2, y_pos),
                status_text,
                fill=0,
                font=font_medium,
            )

            # Save and display the image
            filename = "wifi_connecting_display.png"
            img.save(filename)

            # Display on e-ink
            from eink_display_flush import SimpleEinkDriver, load_and_convert_image

            driver = SimpleEinkDriver()
            driver.initialize()
            image_data = load_and_convert_image(filename)
            driver.display_image(image_data)
            driver.cleanup()

            self.logger.info("E-ink display updated for connecting state")
        except Exception as e:
            self.logger.error(f"E-ink connecting update error: {e}")

    def _update_eink_success(self, ssid: str, ip_address: str):
        """Update e-ink display for success state"""
        if not self.enable_eink:
            return
        try:
            self.logger.info(
                f"Updating e-ink display for successful connection to {ssid}"
            )
            # Create and display success image with connection information
            create_wifi_success_image(
                ssid=ssid,
                ip_address=ip_address,
                filename="wifi_success_display.png",
                auto_display=True,  # Automatically display on e-ink
            )
            self.logger.info("E-ink display updated for success state")
        except Exception as e:
            self.logger.error(f"E-ink success update error: {e}")

    def _update_eink_info(self):
        """Update e-ink display with current WiFi information"""
        if not self.enable_eink:
            return
        try:
            self.logger.info("Updating e-ink display with current WiFi information")
            # Create and display WiFi info image showing current connection details
            create_wifi_info_image(
                filename="wifi_info_display.png",
                auto_display=True,  # Automatically display on e-ink
            )
            self.logger.info("E-ink display updated with WiFi info")
        except Exception as e:
            self.logger.error(f"E-ink WiFi info update error: {e}")

    async def run(self):
        """Run the WiFi service"""
        self.logger.info("Starting Fixed Distiller WiFi Service")
        self.running = True

        try:
            # Check initial state
            initial_state = await self.check_initial_state()
            self.current_state = initial_state

            # Start appropriate mode
            if initial_state == ServiceState.HOTSPOT_MODE:
                await self._start_hotspot_mode()
            elif initial_state == ServiceState.CONNECTED:
                self.logger.info("Already connected, service ready")
                # Display current WiFi info on e-ink
                if self.enable_eink:
                    self._update_eink_info()

            # Start web server
            self._start_web_server()

            # Main service loop
            while self.running:
                try:
                    # Periodic health checks
                    await asyncio.sleep(10)

                    # Handle state-specific tasks
                    if self.current_state == ServiceState.HOTSPOT_MODE:
                        # Check if hotspot is still active (but be careful about restarts)
                        if not self.wifi_manager.is_hotspot_active():
                            self.logger.warning("Hotspot lost, restarting...")
                            # Only restart if we're not in the middle of a connection attempt
                            if not self._connection_in_progress:
                                await asyncio.sleep(
                                    3
                                )  # Wait longer to prevent race conditions
                                # Double-check state and connection flag haven't changed
                                if (
                                    self.current_state == ServiceState.HOTSPOT_MODE
                                    and not self._connection_in_progress
                                ):
                                    await self._start_hotspot_mode()

                    elif self.current_state == ServiceState.CONNECTED:
                        # Monitor connection health
                        status = await self.wifi_manager.get_connection_status()
                        if not status.connected:
                            self.logger.warning(
                                "WiFi connection lost, starting hotspot"
                            )
                            self.current_state = ServiceState.HOTSPOT_MODE
                            await self._start_hotspot_mode()
                        else:
                            # Periodically update WiFi info display (every 5 minutes)
                            if self.enable_eink and hasattr(self, "_last_info_update"):
                                if (
                                    time.time() - self._last_info_update > 300
                                ):  # 5 minutes
                                    self._update_eink_info()
                                    self._last_info_update = time.time()
                            elif self.enable_eink:
                                # First time - set the timestamp
                                self._last_info_update = time.time()

                    elif self.current_state == ServiceState.CONNECTING:
                        # Check for connection timeout
                        if (
                            self.connection_start_time
                            and time.time() - self.connection_start_time > 120
                        ):
                            self.logger.warning(
                                "Connection timeout, returning to hotspot mode"
                            )
                            self.current_state = ServiceState.HOTSPOT_MODE
                            await self._start_hotspot_mode()

                except Exception as e:
                    self.logger.error(f"Error in main loop: {e}")
                    await asyncio.sleep(5)

        except Exception as e:
            self.logger.error(f"Service error: {e}")
            raise
        finally:
            await self.cleanup()

    async def _scan_networks_properly(self):
        """Scan for networks with proper hotspot handling"""
        try:
            if self.current_state == ServiceState.HOTSPOT_MODE:
                # Check if we're in the middle of a connection attempt
                if self.current_state == ServiceState.CONNECTING:
                    self.logger.info("Connection in progress, skipping network scan")
                    return []

                # Temporarily stop hotspot to get proper network scan
                self.logger.info("Temporarily stopping hotspot for network scan")
                hotspot_was_active = self.wifi_manager.is_hotspot_active()

                if hotspot_was_active:
                    await self.wifi_manager.stop_hotspot()
                    await asyncio.sleep(3)  # Wait longer for interface to be ready

                # Perform scan
                networks = await self.wifi_manager.get_available_networks()

                # Filter out our own hotspot SSID
                filtered_networks = [
                    net for net in networks if net.ssid != self.hotspot_ssid
                ]

                # Only restart hotspot if we're still in hotspot mode (not connecting)
                if (
                    hotspot_was_active
                    and self.current_state == ServiceState.HOTSPOT_MODE
                ):
                    self.logger.info("Restarting hotspot after network scan")
                    # Add delay to prevent race conditions
                    await asyncio.sleep(2)
                    await self.wifi_manager.start_hotspot(
                        self.hotspot_ssid, self.hotspot_password
                    )

                return filtered_networks
            else:
                # Normal scan when not in hotspot mode
                return await self.wifi_manager.get_available_networks()

        except Exception as e:
            self.logger.error(f"Error in network scan: {e}")
            # Try to restore hotspot if we're supposed to be in hotspot mode
            if self.current_state == ServiceState.HOTSPOT_MODE:
                try:
                    await asyncio.sleep(3)  # Prevent race conditions
                    await self.wifi_manager.start_hotspot(
                        self.hotspot_ssid, self.hotspot_password
                    )
                except Exception as restore_error:
                    self.logger.error(f"Failed to restore hotspot: {restore_error}")
            return []

    async def cleanup(self):
        """Cleanup service resources"""
        self.logger.info("Cleaning up WiFi service")

        try:
            # Stop mDNS service
            self.device_config.stop_mdns_service()
            
            # Stop hotspot if running
            if self.wifi_manager.is_hotspot_active():
                await self.wifi_manager.stop_hotspot()

        except Exception as e:
            self.logger.error(f"Cleanup error: {e}")

    async def _get_networks_without_hotspot_restart(self):
        """Get networks without stopping hotspot - use alternative method"""
        try:
            # Use a different approach that doesn't interfere with hotspot
            cmd = [
                "nmcli",
                "-t",
                "-f",
                "SSID,SIGNAL,SECURITY,FREQ",
                "device",
                "wifi",
                "list",
                "--rescan",
                "no",
            ]
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()

            if process.returncode != 0:
                return []

            networks = []
            seen_ssids = set()

            for line in stdout.decode().strip().split("\n"):
                if not line:
                    continue

                parts = line.split(":")
                if len(parts) >= 4:
                    ssid = parts[0]
                    signal = int(parts[1]) if parts[1].isdigit() else 0
                    security = "encrypted" if parts[2] else "open"
                    frequency = parts[3]

                    # Skip empty SSIDs, our hotspot, and duplicates
                    if ssid and ssid != self.hotspot_ssid and ssid not in seen_ssids:
                        from network.wifi_manager import NetworkInfo

                        networks.append(
                            NetworkInfo(
                                ssid=ssid,
                                signal=signal,
                                security=security,
                                frequency=frequency,
                                in_use=False,
                            )
                        )
                        seen_ssids.add(ssid)

            # Sort by signal strength
            networks.sort(key=lambda x: x.signal, reverse=True)
            # Networks found successfully
            return networks

        except Exception:
            return []


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Fixed Distiller WiFi Service")

    parser.add_argument(
        "--ssid",
        default=None,
        help="Hotspot SSID (default: auto-generated with random suffix)",
    )
    parser.add_argument(
        "--password", default=None, help="Hotspot password (default: from device config)"
    )
    parser.add_argument(
        "--device-name",
        default=None,
        help="Device name for display (default: auto-generated with random suffix)",
    )
    parser.add_argument(
        "--port", type=int, default=8080, help="Web server port (default: 8080)"
    )
    parser.add_argument("--no-eink", action="store_true", help="Disable e-ink display")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Check root privileges
    if os.geteuid() != 0:
        print("Error: This service requires root privileges")
        sys.exit(1)

    try:
        service = DistillerWiFiServiceFixed(
            hotspot_ssid=args.ssid,
            hotspot_password=args.password,
            device_name=args.device_name,
            web_port=args.port,
            enable_eink=not args.no_eink,
        )

        asyncio.run(service.run())

    except KeyboardInterrupt:
        print("\nService interrupted by user")
    except Exception as e:
        print(f"Service failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
