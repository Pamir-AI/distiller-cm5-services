import re
import subprocess
import logging
import socket

logger = logging.getLogger(__name__)


class NetworkUtils:
    """
    Utility class for network-related functionality.

    Provides methods for obtaining network information such as IP addresses.
    """

    def get_wifi_name(self):
        """Get the WiFi SSID name.

        Returns:
            SSID name as a string or an error message
        """
        try:
            return self._get_linux_wifi_name()
        except Exception as e:
            logger.error(f"Error getting WiFi name: {e}")
            return "Unknown WiFi"

    def get_wifi_ip_address(self):
        """Get the WiFi IP address of the system.

        Returns:
            IP address as a string or an error message
        """
        try:
            return self._get_linux_ip()
        except Exception as e:
            logger.error(f"Error getting IP address: {e}")
            return "Error getting IP address"

    def get_wifi_mac_address(self):
        """Get the WiFi MAC address of the system.

        Returns:
            MAC address as a string or an error message
        """
        try:
            return self._get_linux_mac()
        except Exception as e:
            logger.error(f"Error getting MAC address: {e}")
            return "Error getting MAC address"

    def get_wifi_signal_strength(self):
        """Get the WiFi signal strength.

        Returns:
            Signal strength as a string or an error message
        """
        try:
            return self._get_linux_signal_strength()
        except Exception as e:
            logger.error(f"Error getting signal strength: {e}")
            return "Error getting signal strength"

    def get_network_details(self):
        """Get detailed information about the network.

        Returns:
            Dictionary with network details
        """
        try:
            details = {
                "hostname": socket.gethostname(),
                "ip_address": self.get_wifi_ip_address(),
                "mac_address": self.get_wifi_mac_address(),
                "signal_strength": self.get_wifi_signal_strength(),
            }

            # Add interface information
            interfaces = self._get_network_interfaces()
            if interfaces:
                details["interfaces"] = interfaces

            return details
        except Exception as e:
            logger.error(f"Error getting network details: {e}")
            return {"error": "Failed to get network details"}

    def _get_linux_ip(self):
        """Get the IP address for Linux systems.

        Returns:
            IP address as a string or an error message
        """
        try:
            # Try using ip command first (modern)
            try:
                result = subprocess.run(
                    ["ip", "-4", "addr", "show"],
                    capture_output=True,
                    text=True,
                    check=True,
                )

                # Use smart IP prioritization logic instead of just finding WiFi
                output = result.stdout
                return self._find_best_ip_from_output(output)

            except FileNotFoundError:
                # Fall back to ifconfig
                result = subprocess.run(
                    ["ifconfig"], capture_output=True, text=True, check=True
                )

                # Use smart IP prioritization logic for ifconfig output too
                output = result.stdout
                return self._find_best_ip_from_ifconfig_output(output)

            return "No network IP found"
        except Exception as e:
            logger.error(f"Error getting Linux IP address: {e}")
            return "Error getting IP address"

    def _find_best_ip_from_output(self, output):
        """Find the best IP address from ip command output, avoiding virtual interfaces.
        
        Args:
            output: Output from 'ip -4 addr show' command
            
        Returns:
            Best IP address or "No network IP found"
        """
        # Virtual/bridge interfaces to avoid
        virtual_interfaces = ["docker", "br-", "veth", "lxc", "virbr", "vmnet", "tun", "tap"]
        
        # Collect all potential IPs with their interfaces
        candidate_ips = []
        current_interface = None
        
        for line in output.split("\n"):
            line = line.strip()
            # Check if this is an interface line
            if ":" in line and not line.startswith("inet"):
                interface_match = re.search(r"\d+:\s*([^:]+):", line)
                if interface_match:
                    current_interface = interface_match.group(1).strip()
            elif "inet " in line and current_interface:
                ip_match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", line)
                if ip_match:
                    ip = ip_match.group(1)
                    if ip != "127.0.0.1":  # Skip loopback
                        # Check if it's a virtual interface
                        is_virtual = any(vif in current_interface.lower() for vif in virtual_interfaces)
                        candidate_ips.append({
                            'ip': ip,
                            'interface': current_interface,
                            'is_virtual': is_virtual,
                            'is_ethernet': current_interface.startswith(('eth', 'en')),
                            'is_wifi': current_interface.startswith(('wlan', 'wl')),
                            'is_private': self._is_private_network_ip(ip)
                        })
        
        # Prioritize IPs: WiFi > Ethernet > Private Network > Non-virtual
        def ip_priority(ip_info):
            priority = 0
            if ip_info['is_wifi']:
                priority += 1000
            elif ip_info['is_ethernet']:
                priority += 800
            if ip_info['is_private']:
                priority += 100
            if not ip_info['is_virtual']:
                priority += 50
            # Prefer 192.168.x.x and 10.x.x.x over 172.x.x.x (which Docker often uses)
            if ip_info['ip'].startswith('192.168.') or ip_info['ip'].startswith('10.'):
                priority += 20
            return priority
        
        if candidate_ips:
            # Sort by priority and return the best one
            candidate_ips.sort(key=ip_priority, reverse=True)
            return candidate_ips[0]['ip']
        
        return "No network IP found"

    def _find_best_ip_from_ifconfig_output(self, output):
        """Find the best IP address from ifconfig output, avoiding virtual interfaces.
        
        Args:
            output: Output from 'ifconfig' command
            
        Returns:
            Best IP address or "No network IP found"
        """
        # Virtual/bridge interfaces to avoid
        virtual_interfaces = ["docker", "br-", "veth", "lxc", "virbr", "vmnet", "tun", "tap"]
        
        # Collect all potential IPs with their interfaces
        candidate_ips = []
        current_interface = None
        
        for line in output.split("\n"):
            line = line.strip()
            # Check if this is a new interface (starts at beginning of line)
            if line and not line.startswith(" ") and ":" in line:
                current_interface = line.split(":")[0].strip()
            elif "inet " in line and current_interface:
                ip_match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", line)
                if ip_match:
                    ip = ip_match.group(1)
                    if ip != "127.0.0.1":  # Skip loopback
                        # Check if it's a virtual interface
                        is_virtual = any(vif in current_interface.lower() for vif in virtual_interfaces)
                        candidate_ips.append({
                            'ip': ip,
                            'interface': current_interface,
                            'is_virtual': is_virtual,
                            'is_ethernet': current_interface.startswith(('eth', 'en')),
                            'is_wifi': current_interface.startswith(('wlan', 'wl')),
                            'is_private': self._is_private_network_ip(ip)
                        })
        
        # Use same prioritization logic
        def ip_priority(ip_info):
            priority = 0
            if ip_info['is_wifi']:
                priority += 1000
            elif ip_info['is_ethernet']:
                priority += 800
            if ip_info['is_private']:
                priority += 100
            if not ip_info['is_virtual']:
                priority += 50
            # Prefer 192.168.x.x and 10.x.x.x over 172.x.x.x (which Docker often uses)
            if ip_info['ip'].startswith('192.168.') or ip_info['ip'].startswith('10.'):
                priority += 20
            return priority
        
        if candidate_ips:
            # Sort by priority and return the best one
            candidate_ips.sort(key=ip_priority, reverse=True)
            return candidate_ips[0]['ip']
        
        return "No network IP found"

    def _is_private_network_ip(self, ip):
        """Check if an IP address is in a private network range.
        
        Args:
            ip: IP address string
            
        Returns:
            True if IP is in private network range, False otherwise
        """
        try:
            parts = ip.split('.')
            if len(parts) != 4:
                return False
            
            first = int(parts[0])
            second = int(parts[1])
            
            # 10.0.0.0/8
            if first == 10:
                return True
            # 172.16.0.0/12
            elif first == 172 and 16 <= second <= 31:
                return True
            # 192.168.0.0/16
            elif first == 192 and second == 168:
                return True
                
            return False
        except (ValueError, IndexError):
            return False

    def _get_linux_mac(self):
        """Get the MAC address for Linux systems.

        Returns:
            MAC address as a string or an error message
        """
        try:
            # Try using ip command first (modern)
            try:
                result = subprocess.run(
                    ["ip", "link", "show"],
                    capture_output=True,
                    text=True,
                    check=True,
                )

                # Parse output looking for wifi interface (wlan0, wlp2s0, etc.)
                output = result.stdout
                wifi_regex = r"(wl\w+)"
                wifi_interfaces = re.findall(wifi_regex, output)

                if wifi_interfaces:
                    wifi_interface = wifi_interfaces[0]
                    # Look for MAC address on this interface
                    for line in output.split("\n"):
                        if wifi_interface in line:
                            # The MAC address is typically on the same line or next line
                            mac_match = re.search(r"link/ether ([0-9a-f:]{17})", line)
                            if mac_match:
                                return mac_match.group(1)
                            # Check next line if not found
                            next_line_index = output.split("\n").index(line) + 1
                            if next_line_index < len(output.split("\n")):
                                next_line = output.split("\n")[next_line_index]
                                mac_match = re.search(
                                    r"link/ether ([0-9a-f:]{17})", next_line
                                )
                                if mac_match:
                                    return mac_match.group(1)

                # If no WiFi interface, try to find any MAC
                mac_match = re.search(r"link/ether ([0-9a-f:]{17})", output)
                if mac_match:
                    return mac_match.group(1)

            except FileNotFoundError:
                # Fall back to ifconfig
                result = subprocess.run(
                    ["ifconfig"], capture_output=True, text=True, check=True
                )

                output = result.stdout
                wifi_regex = r"(wl\w+)"
                wifi_interfaces = re.findall(wifi_regex, output)

                if wifi_interfaces:
                    wifi_interface = wifi_interfaces[0]
                    interface_section = False
                    for line in output.split("\n"):
                        if wifi_interface in line:
                            interface_section = True
                        elif interface_section and "ether" in line:
                            mac_match = re.search(r"ether ([0-9a-f:]{17})", line)
                            if mac_match:
                                return mac_match.group(1)
                        elif interface_section and len(line.strip()) == 0:
                            interface_section = False

                # If no WiFi, find any MAC
                mac_match = re.search(r"ether ([0-9a-f:]{17})", output)
                if mac_match:
                    return mac_match.group(1)

            return "No WiFi MAC address found"
        except Exception as e:
            logger.error(f"Error getting Linux MAC address: {e}")
            return "Error getting MAC address"

    def _get_linux_signal_strength(self):
        """Get the WiFi signal strength for Linux systems.

        Returns:
            Signal strength as a string or an error message
        """
        try:
            # Try iwconfig first
            try:
                # Find WiFi interface
                wifi_interface = None
                ip_result = subprocess.run(
                    ["ip", "link", "show"], capture_output=True, text=True, check=True
                )
                wifi_regex = r"(wl\w+)"
                wifi_interfaces = re.findall(wifi_regex, ip_result.stdout)
                if wifi_interfaces:
                    wifi_interface = wifi_interfaces[0]

                if not wifi_interface:
                    return "No WiFi interface found"

                # Get signal strength using iwconfig
                result = subprocess.run(
                    ["iwconfig", wifi_interface],
                    capture_output=True,
                    text=True,
                    check=True,
                )

                output = result.stdout
                for line in output.split("\n"):
                    if "Signal level" in line:
                        # Extract signal level
                        signal_match = re.search(r"Signal level=([^d]+)dBm", line)
                        if signal_match:
                            dbm = signal_match.group(1).strip()
                            try:
                                # Convert dBm to percentage-like value
                                dbm_val = float(dbm)
                                # Typical WiFi range is -30 dBm (excellent) to -90 dBm (poor)
                                percent = min(100, max(0, 2 * (dbm_val + 100)))
                                return f"{int(percent)}% ({dbm}dBm)"
                            except ValueError:
                                return f"{dbm}dBm"

                # Alternative check for Link Quality
                for line in output.split("\n"):
                    if "Link Quality" in line:
                        quality_match = re.search(r"Link Quality=(\d+)/(\d+)", line)
                        if quality_match:
                            quality = quality_match.group(1)
                            max_quality = quality_match.group(2)
                            try:
                                percent = int(float(quality) / float(max_quality) * 100)
                                return f"{percent}% (Quality: {quality}/{max_quality})"
                            except ValueError:
                                return f"Quality: {quality}/{max_quality}"

            except (FileNotFoundError, subprocess.CalledProcessError):
                # Try iw command as alternative
                try:
                    # Find WiFi interface
                    wifi_interface = None
                    ip_result = subprocess.run(
                        ["ip", "link", "show"],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    wifi_regex = r"(wl\w+)"
                    wifi_interfaces = re.findall(wifi_regex, ip_result.stdout)
                    if wifi_interfaces:
                        wifi_interface = wifi_interfaces[0]

                    if not wifi_interface:
                        return "No WiFi interface found"

                    # Get signal strength using iw
                    result = subprocess.run(
                        ["iw", "dev", wifi_interface, "link"],
                        capture_output=True,
                        text=True,
                        check=True,
                    )

                    output = result.stdout
                    for line in output.split("\n"):
                        if "signal" in line.lower():
                            # Extract signal level
                            signal_match = re.search(r"signal:\s+([-\d]+)\s+dBm", line)
                            if signal_match:
                                dbm = signal_match.group(1)
                                try:
                                    # Convert dBm to percentage-like value
                                    dbm_val = float(dbm)
                                    # Typical WiFi range is -30 dBm (excellent) to -90 dBm (poor)
                                    percent = min(100, max(0, 2 * (dbm_val + 100)))
                                    return f"{int(percent)}% ({dbm}dBm)"
                                except ValueError:
                                    return f"{dbm}dBm"
                except Exception:
                    pass

            return "No signal strength information available"
        except Exception as e:
            logger.error(f"Error getting Linux signal strength: {e}")
            return "Error getting signal strength"

    def _get_network_interfaces(self):
        """Get information about all network interfaces.

        Returns:
            List of dictionaries with interface information
        """
        try:
            interfaces = []

            # Linux implementation
            try:
                command = ["ip", "addr", "show"]

                result = subprocess.run(
                    command, capture_output=True, text=True, check=True
                )

                output = result.stdout
                current_interface = None

                for line in output.split("\n"):
                    line = line.strip()
                    # New interface starts
                    if line and not line.startswith(" ") and ":" in line:
                        if current_interface:
                            interfaces.append(current_interface)

                        if_name = line.split(":")[0].strip()
                        current_interface = {
                            "name": if_name,
                            "type": (
                                "wireless" if if_name.startswith("wl") else "wired"
                            ),
                        }
                    elif current_interface and line:
                        # Parse address info
                        if "inet " in line:
                            ip_match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", line)
                            if ip_match:
                                current_interface["ip_address"] = ip_match.group(1)
                        elif "ether" in line or "link/ether" in line:
                            mac_match = re.search(r"([0-9a-f:]{17})", line)
                            if mac_match:
                                current_interface["mac_address"] = mac_match.group(1)

                if current_interface:
                    interfaces.append(current_interface)

            except Exception as e:
                logger.error(f"Error getting interface details: {e}")

            return interfaces
        except Exception as e:
            logger.error(f"Error getting network interfaces: {e}")
            return []

    def _get_linux_wifi_name(self):
        """Get the WiFi SSID name for Linux systems.

        Returns:
            SSID name as a string or an error message
        """
        try:
            # Try nmcli first (NetworkManager)
            try:
                result = subprocess.run(
                    ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
                    capture_output=True,
                    text=True,
                    check=True,
                )

                # Parse output to find active connection
                for line in result.stdout.split("\n"):
                    if line.startswith("yes:"):
                        return line.split(":", 1)[1]  # Return SSID
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass

            # Try iwconfig as fallback
            try:
                # Find WiFi interface
                wifi_interface = None
                ip_result = subprocess.run(
                    ["ip", "link", "show"], capture_output=True, text=True, check=True
                )
                wifi_regex = r"(wl\w+)"
                wifi_interfaces = re.findall(wifi_regex, ip_result.stdout)
                if wifi_interfaces:
                    wifi_interface = wifi_interfaces[0]

                if not wifi_interface:
                    return "No WiFi interface found"

                # Get SSID using iwconfig
                result = subprocess.run(
                    ["iwconfig", wifi_interface],
                    capture_output=True,
                    text=True,
                    check=True,
                )

                # Extract ESSID from output
                essid_match = re.search(r'ESSID:"([^"]*)"', result.stdout)
                if essid_match:
                    return essid_match.group(1)
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass

            # Try iw as second fallback
            try:
                # Find WiFi interface
                wifi_interface = None
                ip_result = subprocess.run(
                    ["ip", "link", "show"], capture_output=True, text=True, check=True
                )
                wifi_regex = r"(wl\w+)"
                wifi_interfaces = re.findall(wifi_regex, ip_result.stdout)
                if wifi_interfaces:
                    wifi_interface = wifi_interfaces[0]

                if not wifi_interface:
                    return "No WiFi interface found"

                # Get SSID using iw
                result = subprocess.run(
                    ["iw", "dev", wifi_interface, "link"],
                    capture_output=True,
                    text=True,
                    check=True,
                )

                # Extract SSID from output
                ssid_match = re.search(r"SSID: (.*?)$", result.stdout, re.MULTILINE)
                if ssid_match:
                    return ssid_match.group(1).strip()
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass

            return "No WiFi name found"
        except Exception as e:
            logger.error(f"Error getting Linux WiFi name: {e}")
            return "Unknown WiFi"
