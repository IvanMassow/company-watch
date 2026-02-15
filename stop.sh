#!/bin/bash
# Stop Company Watch daemon
cd "$(dirname "$0")"

if [ -f .companywatch.pid ]; then
    PID=$(cat .companywatch.pid)
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping Company Watch (PID $PID)..."
        kill "$PID"
        rm .companywatch.pid
        echo "Stopped."
    else
        echo "PID $PID not running. Cleaning up."
        rm .companywatch.pid
    fi
else
    echo "No PID file found. May not be running."
    echo "Check: ps aux | grep runner.py"
fi
