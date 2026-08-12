#!/bin/bash
set -e

echo "================================================"
echo "  ArXiv Daily Researcher - Docker Container"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================"

# Configuration with defaults
CRON_SCHEDULE="${CRON_SCHEDULE:-0 8 * * *}"
RUN_ON_STARTUP="${RUN_ON_STARTUP:-false}"
MODE="${MODE:-cron}"

echo "Mode: $MODE"
echo "Timezone: $TZ"
echo "Cron Schedule: $CRON_SCHEDULE"
echo "Run on Startup: $RUN_ON_STARTUP"

# Ensure data directories exist
mkdir -p /app/data/reports/daily_research/markdown \
         /app/data/reports/daily_research/html \
         /app/data/reports/trend_research/markdown \
         /app/data/reports/trend_research/html \
         /app/data/reports/keyword_trend/markdown \
         /app/data/reports/keyword_trend/html \
         /app/data/history \
         /app/data/reference_pdfs /app/data/downloaded_pdfs \
         /app/logs

# Clean up stale log files
LOG_KEEP_DAYS="${LOG_KEEP_DAYS:-30}"
find /app/logs -name "cron_*.log" -type f -mtime +${LOG_KEEP_DAYS} -delete 2>/dev/null || true
find /app/logs -name "startup_*.log" -type f -mtime +${LOG_KEEP_DAYS} -delete 2>/dev/null || true
find /app/logs -name "daily_*.log" -type f -mtime +${LOG_KEEP_DAYS} -delete 2>/dev/null || true
find /app/logs -name "trend_*.log" -type f -mtime +${LOG_KEEP_DAYS} -delete 2>/dev/null || true
find /app/logs -name "webdav_*.log" -type f -mtime +${LOG_KEEP_DAYS} -delete 2>/dev/null || true

# ==================== Interactive Setup Wizard ====================
# Run setup wizard on first deployment (no .env file) or when SETUP_WIZARD=true
SETUP_WIZARD="${SETUP_WIZARD:-auto}"
if [ "$SETUP_WIZARD" = "true" ]; then
    echo ""
    echo "Running interactive setup wizard..."
    cd /app && python src/utils/setup_wizard.py
    echo "Setup wizard complete."
    echo ""
elif [ "$SETUP_WIZARD" = "auto" ] && [ ! -f /app/.env ]; then
    echo ""
    echo "No .env file detected — first deployment."
    echo "Running interactive setup wizard..."
    cd /app && python src/utils/setup_wizard.py
    echo "Setup wizard complete."
    echo ""
fi

# ==================== Single Execution Mode ====================
if [ "$MODE" = "run-once" ]; then
    LOG_FILE="/app/logs/cron_$(date +%Y%m%d_%H%M%S).log"
    echo "Running in single-execution mode..."
    echo "Log: $LOG_FILE"
    cd /app && python main.py 2>&1 | tee "$LOG_FILE"
    exit ${PIPESTATUS[0]}
fi

# ==================== Cron Mode ====================

# Export environment variables for cron
# (cron does not inherit the container's environment by default)
printenv | grep -v "no_proxy" > /etc/environment

# Create the cron job
CRON_LOG="/app/logs/cron_\$(date +\%Y\%m\%d_\%H\%M\%S).log"
CRON_CMD="cd /app && /usr/local/bin/python main.py >> $CRON_LOG 2>&1"
WEBDAV_CRON_LOG="/app/logs/webdav_\$(date +\%Y\%m\%d).log"
WEBDAV_CRON_CMD="cd /app && PYTHONPATH=/app/src /usr/local/bin/python -m utils.webdav_scheduler >> $WEBDAV_CRON_LOG 2>&1"
{
    echo "$CRON_SCHEDULE $CRON_CMD"
    # This lightweight tick only performs a transfer when config.json selects
    # WebDAV's scheduled mode and its own cron expression matches.  It keeps
    # the established cron/watcher/tail container lifecycle unchanged.
    echo "* * * * * $WEBDAV_CRON_CMD"
} > /etc/cron.d/arxiv-daily
chmod 0644 /etc/cron.d/arxiv-daily
crontab /etc/cron.d/arxiv-daily

echo "Cron job installed:"
crontab -l

# Run immediately on startup if configured
if [ "$RUN_ON_STARTUP" = "true" ]; then
    echo ""
    echo "Running initial execution..."
    cd /app && python main.py 2>&1 | tee /app/logs/startup_$(date +%Y%m%d_%H%M%S).log
    echo "Initial execution complete."
    echo ""
fi

# ==================== WebUI Trigger File Watcher ====================
# The Streamlit config panel (in a separate, thin container) puts validated
# JSON requests in this shared queue.  Do not delete requests on startup: they
# are durable user actions and must survive a worker restart.
TRIGGER_DIR="/app/data/run/webui_triggers"
PID_FILE="/app/data/run/webui_triggered.pid"
mkdir -p "$TRIGGER_DIR/status"

# A container restart kills the child process with it.  Return an atomically
# claimed request to the queue so a SIGKILL/redeploy cannot silently lose a
# manual user action.  A normal completed request removes its .running file.
for CLAIMED_FILE in "$TRIGGER_DIR"/*.running; do
    [ -e "$CLAIMED_FILE" ] || continue
    REQUEST_FILE="${CLAIMED_FILE%.running}.json"
    if [ ! -e "$REQUEST_FILE" ]; then
        mv "$CLAIMED_FILE" "$REQUEST_FILE" || echo "[trigger-watcher] Failed to recover $CLAIMED_FILE"
    fi
done

trigger_watcher() {
    echo "[trigger-watcher] Started. Polling $TRIGGER_DIR every 5s..."
    while true; do
        REQUEST_FILE=$(find "$TRIGGER_DIR" -maxdepth 1 -type f -name '*.json' -print | sort | head -n 1)
        if [ -n "$REQUEST_FILE" ]; then
            CLAIMED_FILE="${REQUEST_FILE%.json}.running"
            # Atomic claim prevents a future watcher implementation or a manual
            # operator invocation from executing the same request twice.
            if mv "$REQUEST_FILE" "$CLAIMED_FILE" 2>/dev/null; then
                LOG_FILE="/app/logs/manual_$(date +%Y%m%d_%H%M%S).log"
                echo "[trigger-watcher] Claimed request: $CLAIMED_FILE"
                # Run synchronously to preserve FIFO ordering and avoid two
                # resource-heavy WebUI requests competing in one worker.
                # ``set -e`` applies to this shell too.  Keep a rejected or
                # failed manual request from terminating the watcher loop (and
                # therefore the otherwise healthy cron container).
                RESULT=0
                python /app/src/utils/webui_trigger.py "$CLAIMED_FILE" --pid-file "$PID_FILE" >> "$LOG_FILE" 2>&1 || RESULT=$?
                echo "[trigger-watcher] Request finished with exit=$RESULT"
            fi
        fi
        sleep 5
    done
}

trigger_watcher &

# Start cron daemon
echo "Starting cron daemon..."
cron

# Keep container alive
echo "Container is running. Waiting for scheduled executions..."
echo "Schedule: $CRON_SCHEDULE"
echo ""

# Tail the system log to keep container alive and show output
touch /app/logs/system.log
tail -f /app/logs/system.log
