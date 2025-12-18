#!/bin/bash
#
# Setup script for Teams Notetaker webhook listener systemd service.
# This enables auto-start on WSL boot and manages the webhook listener.
#

set -e

echo "🔧 Teams Notetaker Webhook Service Setup"
echo "=" | tr -d '\n' | xargs -0 printf '%.80s\n'
echo

# Check if systemd is enabled in WSL
if ! grep -q "systemd=true" /etc/wsl.conf 2>/dev/null; then
    echo "⚠️  systemd is not enabled in WSL!"
    echo
    echo "To enable systemd, add this to /etc/wsl.conf:"
    echo "[boot]"
    echo "systemd=true"
    echo
    echo "Then restart WSL with: wsl --shutdown"
    echo
    read -p "Do you want me to enable systemd now? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        sudo bash -c "echo '[boot]' >> /etc/wsl.conf"
        sudo bash -c "echo 'systemd=true' >> /etc/wsl.conf"
        echo "✅ systemd enabled in /etc/wsl.conf"
        echo "⚠️  You must restart WSL with: wsl --shutdown"
        echo "   Then run this script again."
        exit 0
    else
        echo "❌ Cannot proceed without systemd"
        exit 1
    fi
fi

# Enable lingering (keeps user services running after logout)
echo "1. Enabling user service lingering..."
if sudo loginctl enable-linger $USER; then
    echo "   ✅ Lingering enabled for $USER"
else
    echo "   ⚠️  Could not enable lingering (might already be enabled)"
fi
echo

# Reload systemd daemon
echo "2. Reloading systemd daemon..."
systemctl --user daemon-reload
echo "   ✅ Daemon reloaded"
echo

# Enable and start webhook listener service
echo "3. Enabling webhook listener service..."
if systemctl --user enable teams-notetaker-webhook.service; then
    echo "   ✅ Webhook listener enabled (will auto-start on boot)"
else
    echo "   ❌ Failed to enable webhook listener"
    exit 1
fi
echo

echo "4. Starting webhook listener service..."
if systemctl --user start teams-notetaker-webhook.service; then
    echo "   ✅ Webhook listener started"
else
    echo "   ⚠️  Failed to start webhook listener (check logs)"
fi
echo

# Enable and start renewal timer
echo "5. Enabling subscription renewal timer..."
if systemctl --user enable teams-notetaker-renew.timer; then
    echo "   ✅ Renewal timer enabled (runs daily)"
else
    echo "   ❌ Failed to enable renewal timer"
    exit 1
fi
echo

echo "6. Starting renewal timer..."
if systemctl --user start teams-notetaker-renew.timer; then
    echo "   ✅ Renewal timer started"
else
    echo "   ⚠️  Failed to start renewal timer (check logs)"
fi
echo

# Show service status
echo "=" | tr -d '\n' | xargs -0 printf '%.80s\n'
echo "📊 Service Status:"
echo
systemctl --user status teams-notetaker-webhook.service --no-pager -l || true
echo
systemctl --user list-timers teams-notetaker-renew.timer --no-pager || true
echo

echo "=" | tr -d '\n' | xargs -0 printf '%.80s\n'
echo "✅ Setup complete!"
echo
echo "Useful commands:"
echo "  systemctl --user status teams-notetaker-webhook    # Check status"
echo "  systemctl --user stop teams-notetaker-webhook      # Stop service"
echo "  systemctl --user restart teams-notetaker-webhook   # Restart service"
echo "  journalctl --user -u teams-notetaker-webhook -f    # View logs"
echo "  systemctl --user list-timers                       # View renewal timer"
echo
echo "Next steps:"
echo "  1. Create subscription: python -m src.main webhooks subscribe-transcripts"
echo "  2. Test with a real meeting that has transcription enabled"
echo "  3. Check logs: journalctl --user -u teams-notetaker-webhook -f"
echo
