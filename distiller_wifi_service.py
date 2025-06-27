#!/usr/bin/env python3
"""
Fixed Distiller WiFi Service with Proper State Transitions

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

from network.wifi_manager import FixedWiFiManager, WiFiManagerError, ConnectionStatus

try:
    from flask import Flask, render_template, request, jsonify, redirect, url_for
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

# E-ink display imports (optional)
try:
    from wifi_info_display import create_wifi_setup_image, create_wifi_success_image
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
    
    def __init__(self,
                 hotspot_ssid: str = "DistillerSetup",
                 hotspot_password: str = "setup123",
                 device_name: str = "Distiller",
                 web_port: int = 8080,
                 enable_eink: bool = True):
        
        self.hotspot_ssid = hotspot_ssid
        self.hotspot_password = hotspot_password
        self.device_name = device_name
        self.web_port = web_port
        self.enable_eink = enable_eink and EINK_AVAILABLE
        
        # Service state
        self.current_state = ServiceState.INITIALIZING
        self.running = False
        self.target_ssid: Optional[str] = None
        self.target_password: Optional[str] = None
        self.connection_start_time: Optional[float] = None
        self._connection_in_progress = False  # Flag to prevent race conditions
        
        # Setup logging
        self.setup_logging()
        self.logger = logging.getLogger(__name__)
        
        # Initialize WiFi manager
        self.wifi_manager = FixedWiFiManager()
        
        # Flask app for web interface
        self.app = self._create_flask_app() if FLASK_AVAILABLE else None
        self.web_server_thread: Optional[threading.Thread] = None
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger.info("Fixed WiFi Service initialized")

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
            level=logging.INFO,
            format=log_format,
            handlers=handlers,
            force=True
        )

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def _create_flask_app(self) -> Flask:
        """Create Flask web application"""
        app = Flask(__name__, template_folder="templates", static_folder="static")
        
        # Disable caching
        app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
        
        @app.after_request
        def add_no_cache_headers(response):
            response.cache_control.max_age = 0
            response.cache_control.no_cache = True
            response.cache_control.must_revalidate = True
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
                return jsonify({
                    "success": True,
                    "networks": [
                        {
                            "ssid": net.ssid,
                            "signal_strength": net.signal,
                            "security": net.security,
                            "frequency": net.frequency
                        }
                        for net in networks
                    ]
                })
            except Exception as e:
                self.logger.error(f"Error in API scan: {e}")
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 500
        
        @app.route("/change-network")
        def change_network():
            """Handle change network request"""
            return self._handle_change_network()
        
        @app.route("/test")
        def test_page():
            """Test page for debugging"""
            return render_template("test_connect.html")
        
        # Catch-all for captive portal
        @app.route("/<path:path>")
        def catch_all(path):
            self.logger.info(f"Redirecting path: {path}")
            return redirect(url_for('index'))
        
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
                web_port=self.web_port
            )
            elif self.current_state == ServiceState.CONNECTED:
                # Show connected status
                return redirect(url_for('status'))
            elif self.current_state == ServiceState.INITIALIZING:
                # Service is transitioning (e.g., changing networks)
                # Redirect to status page to show progress
                return redirect(url_for('status'))
            else:
                # Show loading or error state
                return render_template(
                    "index.html",
                    networks=[],
                    device_name=self.device_name,
                    current_state=self.current_state.value,
                    message="Service initializing...",
                    web_port=self.web_port
                )
        except Exception as e:
            self.logger.error(f"Error in index handler: {e}")
            return render_template(
                "index.html",
                networks=[],
                device_name=self.device_name,
                error="Failed to load networks",
                web_port=self.web_port
            )

    def _handle_confirm(self):
        """Handle network confirmation page"""
        try:
            ssid = request.args.get("ssid", "")
            encrypted = request.args.get("encrypted", "unencrypted")
            
            if not ssid:
                return redirect(url_for('index'))
            
            return render_template(
                "confirm.html",
                ssid=ssid,
                encrypted=encrypted,
                device_name=self.device_name,
                web_port=self.web_port
            )
        except Exception as e:
            self.logger.error(f"Error in confirm handler: {e}")
            return redirect(url_for('index'))

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
            
            self.logger.info(f"Connection request received: SSID='{ssid}', Password={'***' if password else 'None'}")
            
            if not ssid:
                self.logger.warning("No SSID provided in connection request")
                if request.is_json:
                    return jsonify({"success": False, "error": "No SSID provided"}), 400
                return redirect(url_for('index'))
            
            # Store connection target
            self.target_ssid = ssid
            self.target_password = password
            self.connection_start_time = time.time()
            
            self.logger.info(f"Starting connection process to '{ssid}' in background")
            
            # Start connection in background
            self._start_connection_background()
            
            if request.is_json:
                return jsonify({"success": True, "message": "Connection started"})
            return redirect(url_for('status'))
            
        except Exception as e:
            self.logger.error(f"Error in connect handler: {e}")
            if request.is_json:
                return jsonify({"success": False, "error": str(e)}), 500
            return redirect(url_for('index'))

    def _handle_status(self):
        """Handle status page"""
        try:
            # Get current status
            status_info = self._get_current_status()
            
            return render_template(
                "status.html",
                **status_info,
                device_name=self.device_name,
                web_port=self.web_port
            )
        except Exception as e:
            self.logger.error(f"Error in status handler: {e}")
            return render_template(
                "status.html",
                connected=False,
                connecting=False,
                error="Status unavailable",
                device_name=self.device_name,
                web_port=self.web_port
            )

    def _handle_api_status(self):
        """Handle status API endpoint"""
        try:
            status_info = self._get_current_status()
            return jsonify({
                "success": True,
                **status_info
            })
        except Exception as e:
            self.logger.error(f"Error in API status: {e}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    def _handle_api_networks(self):
        """Handle API request for available networks"""
        try:
            # Don't scan networks if we're in the middle of a connection
            if self.current_state == ServiceState.CONNECTING:
                self.logger.info("Connection in progress, returning cached/empty network list")
                return jsonify({
                    "success": True,
                    "networks": [],
                    "message": "Connection in progress"
                })
            
            # Use cached or simplified scan for hotspot mode
            if self.current_state == ServiceState.HOTSPOT_MODE:
                # Don't stop hotspot for network scan - use a simpler approach
                try:
                    # Get a quick scan without stopping hotspot
                    networks = asyncio.run(self._get_networks_without_hotspot_restart())
                    return jsonify({
                        "success": True,
                        "networks": [
                            {
                                "ssid": net.ssid,
                                "signal_strength": net.signal,
                                "security": net.security,
                                "frequency": net.frequency
                            }
                            for net in networks
                        ]
                    })
                except Exception as e:
                    self.logger.error(f"Error getting networks: {e}")
                    # Return some common networks as fallback
                    return jsonify({
                        "success": True,
                        "networks": [],
                        "message": "Scan temporarily unavailable"
                    })
            else:
                # Normal scan when not in hotspot mode
                networks = asyncio.run(self.wifi_manager.get_available_networks())
                return jsonify({
                    "success": True,
                    "networks": [
                        {
                            "ssid": net.ssid,
                            "signal_strength": net.signal,
                            "security": net.security,
                            "frequency": net.frequency
                        }
                        for net in networks
                    ]
                })
        except Exception as e:
            self.logger.error(f"Error in API networks: {e}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    def _handle_change_network(self):
        """Handle change network request - disconnect and re-enable hotspot"""
        try:
            self.logger.info("Change network requested - transitioning to hotspot mode")
            
            # Set service state to indicate transition
            self.current_state = ServiceState.INITIALIZING
            
            # Start the transition to hotspot mode in background with delay
            def transition_worker():
                try:
                    # Give user time to see the change network page before disconnecting
                    import time
                    time.sleep(15)  # Wait for countdown to complete
                    
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self._transition_to_hotspot())
                except Exception as e:
                    self.logger.error(f"Error in hotspot transition: {e}")
                    # Ensure we end up in a usable state
                    try:
                        self.current_state = ServiceState.HOTSPOT_MODE
                    except:
                        pass
                finally:
                    loop.close()
            
            # Start transition in background thread
            import threading
            thread = threading.Thread(target=transition_worker, daemon=True)
            thread.start()
            
            # Show dedicated change network page with instructions
            return render_template(
                "change_network.html",
                device_name=self.device_name,
                web_port=self.web_port
            )
            
        except Exception as e:
            self.logger.error(f"Error in change network handler: {e}")
            return redirect(url_for('status'))

    def _get_current_status(self) -> dict:
        """Get current service status"""
        try:
            # Get WiFi connection status with timeout and error handling
            try:
                wifi_status = asyncio.run(self.wifi_manager.get_connection_status())
            except Exception as wifi_error:
                self.logger.warning(f"WiFi status check failed: {wifi_error}")
                # Return status based on service state when WiFi check fails
                return self._get_fallback_status()
            
            # Determine service state
            connecting = (self.current_state == ServiceState.CONNECTING and 
                         self.connection_start_time and 
                         time.time() - self.connection_start_time < 120)  # 2 min timeout
            
            connected = wifi_status.connected and self.current_state == ServiceState.CONNECTED
            
            # Handle redirection after successful connection
            redirect_url = None
            hotspot_ips = ["192.168.4.1", "localhost", "127.0.0.1"]
            if connected and wifi_status.ip_address and wifi_status.ip_address not in hotspot_ips:
                # Connected to new network, should redirect to new IP
                redirect_url = f"http://{wifi_status.ip_address}:{self.web_port}"
            
            return {
                "connected": connected,
                "connecting": connecting,
                "ssid": wifi_status.ssid,
                "ip_address": wifi_status.ip_address,
                "interface": wifi_status.interface,
                "current_state": self.current_state.value,
                "target_ssid": self.target_ssid,
                "elapsed": time.time() - self.connection_start_time if self.connection_start_time else 0,
                "redirect_url": redirect_url,
                "timestamp": int(time.time())
            }
            
        except Exception as e:
            self.logger.error(f"Error getting status: {e}")
            return self._get_fallback_status()

    def _get_fallback_status(self) -> dict:
        """Get fallback status when WiFi status check fails"""
        # Return status based on current service state
        if self.current_state == ServiceState.HOTSPOT_MODE:
            return {
                "connected": False,
                "connecting": False,
                "ssid": None,
                "ip_address": "localhost",  # Use localhost as primary, fallback to 192.168.4.1 in frontend
                "interface": None,
                "current_state": self.current_state.value,
                "target_ssid": self.target_ssid,
                "elapsed": 0,
                "redirect_url": None,
                "timestamp": int(time.time()),
                "message": "Hotspot mode active"
            }
        elif self.current_state == ServiceState.CONNECTING:
            return {
                "connected": False,
                "connecting": True,
                "ssid": None,
                "ip_address": None,
                "interface": None,
                "current_state": self.current_state.value,
                "target_ssid": self.target_ssid,
                "elapsed": time.time() - self.connection_start_time if self.connection_start_time else 0,
                "redirect_url": None,
                "timestamp": int(time.time())
            }
        elif self.current_state == ServiceState.INITIALIZING:
            return {
                "connected": False,
                "connecting": False,
                "ssid": None,
                "ip_address": None,
                "interface": None,
                "current_state": self.current_state.value,
                "target_ssid": None,
                "elapsed": 0,
                "redirect_url": None,
                "timestamp": int(time.time()),
                "message": "Changing network configuration..."
            }
        else:
            return {
                "connected": False,
                "connecting": False,
                "error": "Status check failed - service transitioning",
                "current_state": self.current_state.value,
                "timestamp": int(time.time())
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
                self.target_ssid, 
                password
            )
            
            if success:
                self.logger.info(f"Successfully connected to {self.target_ssid}")
                self.current_state = ServiceState.CONNECTED
                
                # Update e-ink display
                if self.enable_eink:
                    status = await self.wifi_manager.get_connection_status()
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
                
                # Web server will continue running on all interfaces
                # The status page will handle redirection to new IP
                
                # Log success
                self.logger.info(f"WiFi setup completed successfully")
                self.logger.info(f"Device accessible at: http://{status.ip_address}:{self.web_port}")
                
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
            
            success = await self.wifi_manager.start_hotspot(
                self.hotspot_ssid,
                self.hotspot_password
            )
            
            if success:
                self.logger.info(f"Hotspot started: {self.hotspot_ssid}")
                self.logger.info(f"Web interface: http://localhost:{self.web_port} or http://192.168.4.1:{self.web_port}")
                
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
                self.logger.info(f"Disconnecting from current network: {current_status.ssid}")
                
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
            self.logger.info(f"Starting web server on port {self.web_port}")
            if self.app:  # Additional None check for type safety
                self.app.run(
                    host="0.0.0.0",
                    port=self.web_port,
                    debug=False,
                    use_reloader=False,
                    threaded=True
                )
        
        self.web_server_thread = threading.Thread(target=run_server, daemon=True)
        self.web_server_thread.start()

    def _update_eink_setup(self):
        """Update e-ink display for setup mode"""
        if not self.enable_eink:
            return
        try:
            # Implementation would use create_wifi_setup_image()
            pass
        except Exception as e:
            self.logger.error(f"E-ink update error: {e}")

    def _update_eink_connecting(self, ssid: str):
        """Update e-ink display for connecting state"""
        if not self.enable_eink:
            return
        try:
            # Implementation would use connecting display update
            pass
        except Exception as e:
            self.logger.error(f"E-ink update error: {e}")

    def _update_eink_success(self, ssid: str, ip_address: str):
        """Update e-ink display for success state"""
        if not self.enable_eink:
            return
        try:
            # Implementation would use create_wifi_success_image()
            pass
        except Exception as e:
            self.logger.error(f"E-ink update error: {e}")

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
                                await asyncio.sleep(3)  # Wait longer to prevent race conditions
                                # Double-check state and connection flag haven't changed
                                if (self.current_state == ServiceState.HOTSPOT_MODE and 
                                    not self._connection_in_progress):
                                    await self._start_hotspot_mode()
                    
                    elif self.current_state == ServiceState.CONNECTED:
                        # Monitor connection health
                        status = await self.wifi_manager.get_connection_status()
                        if not status.connected:
                            self.logger.warning("WiFi connection lost, starting hotspot")
                            self.current_state = ServiceState.HOTSPOT_MODE
                            await self._start_hotspot_mode()
                    
                    elif self.current_state == ServiceState.CONNECTING:
                        # Check for connection timeout
                        if (self.connection_start_time and 
                            time.time() - self.connection_start_time > 120):
                            self.logger.warning("Connection timeout, returning to hotspot mode")
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
                filtered_networks = [net for net in networks if net.ssid != self.hotspot_ssid]
                
                # Only restart hotspot if we're still in hotspot mode (not connecting)
                if hotspot_was_active and self.current_state == ServiceState.HOTSPOT_MODE:
                    self.logger.info("Restarting hotspot after network scan")
                    # Add delay to prevent race conditions
                    await asyncio.sleep(2)
                    await self.wifi_manager.start_hotspot(self.hotspot_ssid, self.hotspot_password)
                
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
                    await self.wifi_manager.start_hotspot(self.hotspot_ssid, self.hotspot_password)
                except Exception as restore_error:
                    self.logger.error(f"Failed to restore hotspot: {restore_error}")
            return []

    async def cleanup(self):
        """Cleanup service resources"""
        self.logger.info("Cleaning up WiFi service")
        
        try:
            # Stop hotspot if running
            if self.wifi_manager.is_hotspot_active():
                await self.wifi_manager.stop_hotspot()
                
        except Exception as e:
            self.logger.error(f"Cleanup error: {e}")

    async def _get_networks_without_hotspot_restart(self):
        """Get networks without stopping hotspot - use alternative method"""
        try:
            # Use a different approach that doesn't interfere with hotspot
            cmd = ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,FREQ", "device", "wifi", "list", "--rescan", "no"]
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
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
                        networks.append(NetworkInfo(
                            ssid=ssid,
                            signal=signal,
                            security=security,
                            frequency=frequency,
                            in_use=False
                        ))
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
    
    parser.add_argument("--ssid", default="DistillerSetup",
                       help="Hotspot SSID (default: DistillerSetup)")
    parser.add_argument("--password", default="setup123",
                       help="Hotspot password (default: setup123)")
    parser.add_argument("--device-name", default="Distiller",
                       help="Device name for display (default: Distiller)")
    parser.add_argument("--port", type=int, default=8080,
                       help="Web server port (default: 8080)")
    parser.add_argument("--no-eink", action="store_true",
                       help="Disable e-ink display")
    parser.add_argument("--verbose", action="store_true",
                       help="Enable verbose logging")
    
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
            enable_eink=not args.no_eink
        )
        
        asyncio.run(service.run())
        
    except KeyboardInterrupt:
        print("\nService interrupted by user")
    except Exception as e:
        print(f"Service failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main() 
