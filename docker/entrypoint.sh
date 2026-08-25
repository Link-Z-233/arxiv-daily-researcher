#!/bin/bash
set -e

echo "================================================"
echo "  ArXiv Daily Researcher - Docker Container"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================"

# Configuration with defaults.
# The daily run time is configured from the WebUI (configs/config.json,
# "daily_research.run_time" as HH:MM) and installed at container start;
# there is deliberately no environment-variable override anymore.
CRON_SCHEDULE=""
if [ -f /app/configs/config.json ]; then
    CRON_SCHEDULE=$(python - <<'PYEOF'
import json, re

try:
    raw = open("/app/configs/config.json", encoding="utf-8").read()
    raw = re.sub(r"^\s*//.*$", "", raw, flags=re.M)
    value = json.loads(raw).get("daily_research", {}).get("run_time")
    if isinstance(value, str) and re.fullmatch(r"\d{1,2}:\d{2}", value.strip()):
        hh, mm = value.strip().split(":")
        if 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59:
            print(f"{int(mm)} {int(hh)}")
except Exception:
    pass
PYEOF
)
fi
if [ -n "$CRON_SCHEDULE" ]; then
    CRON_SCHEDULE="$CRON_SCHEDULE * * *"
else
    # 配置缺失或不可读时的兜底：中午 12 点。
    CRON_SCHEDULE="0 12 * * *"
fi
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
find /app/logs -name "keyword_*.log" -type f -mtime +${LOG_KEEP_DAYS} -delete 2>/dev/null || true
find /app/logs -name "legacy_import_*.log" -type f -mtime +${LOG_KEEP_DAYS} -delete 2>/dev/null || true
find /app/logs -name "supplement_*.log" -type f -mtime +${LOG_KEEP_DAYS} -delete 2>/dev/null || true
find /app/logs -name "backfill_*.log" -type f -mtime +${LOG_KEEP_DAYS} -delete 2>/dev/null || true
find /app/logs -name "update_*.log" -type f -mtime +${LOG_KEEP_DAYS} -delete 2>/dev/null || true

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

# ==================== Release Update Availability ====================
# A container must not replace its own image.  Check GitHub Releases after a
# normal worker start, then the dedicated cron task below checks daily even if
# daily research is not run.  The Python entry point observes the WebUI toggle
# and sends a notification only when a newer release is available.
if [ "$MODE" != "run-once" ] && [ "$RUN_ON_STARTUP" != "true" ]; then
    UPDATE_CHECK_LOG="/app/logs/update_$(date +%Y%m%d).log"
    echo "Checking published release availability in background..."
    (
        cd /app && PYTHONPATH=/app/src /usr/local/bin/python -m utils.updater
    ) >> "$UPDATE_CHECK_LOG" 2>&1 &
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

# cron does not inherit the container's environment by default.  The worker
# loads application settings from the mounted /app/.env, so never copy the
# whole process environment here: that would persist API keys, webhook URLs,
# and SMTP/WebDAV passwords in the container filesystem.  Keep only the
# non-sensitive runtime values needed by cron-launched Python processes.
{
    printf 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n'
    printf 'PYTHONUNBUFFERED=1\n'
    printf 'PYTHONDONTWRITEBYTECODE=1\n'
    if [ -n "${TZ:-}" ]; then
        printf 'TZ=%s\n' "$TZ"
    fi
} > /etc/environment
chmod 0644 /etc/environment

# Create the cron job
CRON_LOG="/app/logs/cron_\$(date +\%Y\%m\%d_\%H\%M\%S).log"
CRON_CMD="cd /app && /usr/local/bin/python main.py >> $CRON_LOG 2>&1"
WEBDAV_CRON_LOG="/app/logs/webdav_\$(date +\%Y\%m\%d).log"
WEBDAV_CRON_CMD="cd /app && PYTHONPATH=/app/src /usr/local/bin/python -m utils.webdav_scheduler >> $WEBDAV_CRON_LOG 2>&1"
# 关键词标准化/趋势报告：每天 0 点静默执行，与日报主流程解耦。
KEYWORD_CRON_LOG="/app/logs/keyword_\$(date +\%Y\%m\%d).log"
KEYWORD_CRON_CMD="cd /app && PYTHONPATH=/app/src /usr/local/bin/python -m modes.keyword_maintenance >> $KEYWORD_CRON_LOG 2>&1"
UPDATE_CRON_LOG="/app/logs/update_\$(date +\%Y\%m\%d).log"
UPDATE_CRON_CMD="cd /app && PYTHONPATH=/app/src /usr/local/bin/python -m utils.updater >> $UPDATE_CRON_LOG 2>&1"
{
    echo "$CRON_SCHEDULE $CRON_CMD"
    # This lightweight tick only performs a transfer when config.json selects
    # WebDAV's scheduled mode and its own cron expression matches.  It keeps
    # the established cron/watcher/tail container lifecycle unchanged.
    echo "* * * * * $WEBDAV_CRON_CMD"
    echo "0 0 * * * $KEYWORD_CRON_CMD"
    # Independent from daily research: update availability remains observable
    # when the research task is disabled, queued, or otherwise not run.
    echo "17 9 * * * $UPDATE_CRON_CMD"
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
    # The WebUI restart button drops this marker into the shared volume; a
    # worker restart re-runs this entrypoint, reinstalling cron from config.
    # The marker is archived (never deleted) so restarts stay auditable.
    RESTART_MARKER="$TRIGGER_DIR/restart_worker.request"
    while true; do
        if [ -e "$RESTART_MARKER" ]; then
            mv "$RESTART_MARKER" \
               "$RESTART_MARKER.done-$(date +%Y%m%dT%H%M%S)" 2>/dev/null || true
            echo "[trigger-watcher] WebUI restart request: restarting container..."
            # PID 1 在独立 PID namespace 内默认丢弃一切信号（含 KILL）；
            # 只有注册了 handler 的信号才会送达，见文件末尾的 trap。
            kill -TERM 1
        fi
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

# Keep the container alive by tailing the system log.
# The tail runs as a child (not exec'd): as PID 1, bash in its own PID
# namespace drops every signal it has no handler for — including SIGKILL —
# so the restart path works by installing a TERM handler that terminates
# the tail and lets this script (PID 1) exit normally. The same handler
# also gives `docker stop` a clean, fast shutdown.
touch /app/logs/system.log
tail -f /app/logs/system.log &
TAIL_PID=$!
trap 'kill -TERM "$TAIL_PID" 2>/dev/null' TERM INT
wait "$TAIL_PID" || true
