class WiFiSetup {
  constructor() {
    this.statusCard = document.getElementById("status-card");
    this.statusInfo = document.getElementById("status-info");
    this.ssidInput = document.getElementById("ssid-input");
    this.passwordInput = document.getElementById("password-input");
    this.connectBtn = document.getElementById("connect-btn");
    // this.completeBtn = document.getElementById('complete-btn'); // Commented out
    this.alertContainer = document.getElementById("alert-container");

    this.init();
  }

  init() {
    this.connectBtn.addEventListener("click", () => this.connectToNetwork());
    // this.completeBtn.addEventListener('click', () => this.completeSetup()); // Commented out

    // Remove automatic status checking since we'll redirect to status page
    // this.checkStatus();
    // setInterval(() => this.checkStatus(), 5000);

    // Show initial disconnected state
    this.updateStatusDisplay({ connected: false });
  }

  // Keep this method but don't use it automatically
  async checkStatus() {
    try {
      const response = await fetch("/api/status");
      const status = await response.json();
      this.updateStatusDisplay(status);
    } catch (error) {
      console.error("Status check failed:", error);
    }
  }

  updateStatusDisplay(status) {
    if (status.connected) {
      this.statusCard.className = "status-card";
      this.statusInfo.innerHTML = `
                <strong>Connected to:</strong> ${status.ssid}<br>
                <strong>IP Address:</strong> ${status.ip_address || "N/A"}<br>
                <strong>Interface:</strong> ${status.interface || "N/A"}
            `;
      // this.completeBtn.style.display = 'inline-block'; // Commented out
    } else {
      this.statusCard.className = "status-card disconnected";
      this.statusInfo.textContent = "Ready to connect to WiFi network";
      // this.completeBtn.style.display = 'none'; // Commented out
    }
  }

  async connectToNetwork() {
    const ssid = this.ssidInput.value.trim();
    const password = this.passwordInput.value;

    if (!ssid) {
      this.showAlert("Please enter a network name", "error");
      return;
    }

    this.connectBtn.textContent = "Connecting...";
    this.connectBtn.disabled = true;

    try {
      // Initiate the connection
      const connectResponse = await fetch("/api/connect", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ssid: ssid, password: password }),
      });

      if (!connectResponse.ok) {
        throw new Error("Failed to initiate connection");
      }

      // Show connection progress and redirect to status page
      this.showConnectionProgress(ssid);
      
    } catch (error) {
      console.error("Connection process failed:", error);
      this.showAlert(`Connection failed: ${error.message}`, "error");
      this.connectBtn.textContent = "Connect";
      this.connectBtn.disabled = false;
    }
  }

  showConnectionProgress(ssid) {
    // Show static connection message
    this.showAlert(`Connecting to ${ssid}... Please wait while we establish the connection.`, "info");
    
    // Redirect to status page after a delay
    setTimeout(() => {
      this.showAlert("Redirecting to status page...", "info");
      
      // Redirect to status page
      setTimeout(() => {
        window.location.href = "/status";
      }, 2000);
    }, 3000); // Wait 3 seconds before redirecting
  }

  // Commented out complete setup functionality
  /*
    async completeSetup() {
        try {
            const response = await fetch('/api/complete-setup', {
                method: 'POST'
            });
            
            const result = await response.json();
            if (result.success) {
                this.showAlert('Setup completed successfully!', 'success');
            }
        } catch (error) {
            console.error('Complete setup failed:', error);
        }
    }
    */

  showAlert(message, type) {
    const alertClass = `alert-${type}`;
    const alertHtml = `
            <div class="alert ${alertClass}">
                ${message}
            </div>
        `;

    this.alertContainer.innerHTML = alertHtml;

    setTimeout(() => {
      this.alertContainer.innerHTML = "";
    }, 5000);
  }
}

// Initialize the WiFi setup interface when the page loads
document.addEventListener("DOMContentLoaded", function () {
  window.wifiSetup = new WiFiSetup();
});
