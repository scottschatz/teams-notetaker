#!/bin/bash
# Setup script for systemd user services (WSL2)

set -e

echo "=== Teams Meeting Transcript Summarizer - Service Setup ==="
echo ""

# Get current user
USER=$(whoami)
HOME_DIR=$(eval echo ~$USER)
PROJECT_DIR="$HOME_DIR/projects/teams-notetaker"

echo "User: $USER"
echo "Home: $HOME_DIR"
echo "Project: $PROJECT_DIR"
echo ""

# Check if project directory exists
if [ ! -d "$PROJECT_DIR" ]; then
    echo "Error: Project directory not found: $PROJECT_DIR"
    exit 1
fi

# Check if venv exists
if [ ! -d "$PROJECT_DIR/venv" ]; then
    echo "Error: Virtual environment not found. Run: python3 -m venv venv"
    exit 1
fi

# Create systemd user directory
SYSTEMD_USER_DIR="$HOME_DIR/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"

echo "Installing service files..."

# Copy service files
cp deployment/teams-notetaker-poller.service "$SYSTEMD_USER_DIR/"
cp deployment/teams-notetaker-web.service "$SYSTEMD_USER_DIR/"

# Replace %u with actual user in service files
sed -i "s|%u|$USER|g" "$SYSTEMD_USER_DIR/teams-notetaker-poller.service"
sed -i "s|%u|$USER|g" "$SYSTEMD_USER_DIR/teams-notetaker-web.service"

echo "✓ Service files installed to $SYSTEMD_USER_DIR"
echo ""

# Reload systemd
echo "Reloading systemd daemon..."
systemctl --user daemon-reload

echo "✓ Systemd daemon reloaded"
echo ""

# Enable services
echo "Enabling services..."
systemctl --user enable teams-notetaker-poller.service
systemctl --user enable teams-notetaker-web.service

echo "✓ Services enabled"
echo ""

# Start services
echo "Starting services..."
systemctl --user start teams-notetaker-poller.service
systemctl --user start teams-notetaker-web.service

echo "✓ Services started"
echo ""

# Check status
echo "=== Service Status ==="
systemctl --user status teams-notetaker-poller.service --no-pager
echo ""
systemctl --user status teams-notetaker-web.service --no-pager
echo ""

echo "=== Setup Complete ==="
echo ""
echo "Useful commands:"
echo "  View logs (poller):   journalctl --user -u teams-notetaker-poller -f"
echo "  View logs (web):      journalctl --user -u teams-notetaker-web -f"
echo "  Restart poller:       systemctl --user restart teams-notetaker-poller"
echo "  Restart web:          systemctl --user restart teams-notetaker-web"
echo "  Stop all:             systemctl --user stop teams-notetaker-{poller,web}"
echo "  Disable services:     systemctl --user disable teams-notetaker-{poller,web}"
echo ""
echo "Web dashboard: http://localhost:8000"
echo ""
