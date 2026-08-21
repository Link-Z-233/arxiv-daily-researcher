# syntax=docker/dockerfile:1
# 统一多阶段构建：`--target worker` 或 `--target webui`。
# 两个目标共享 slim 基底 + 公共依赖层（L2），各自的独有依赖只装一份；
# 构建后剥离 __pycache__（L1）。两个镜像合计比拆分前明显更省磁盘。

# ==================== 公共层：基底 + 时区 + 共享 Python 依赖 ====================
FROM python:3.12-slim AS common

LABEL maintainer="yzr278892"

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com

RUN apt-get update && \
    apt-get install -y --no-install-recommends tzdata && \
    rm -rf /var/lib/apt/lists/* && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir \
      -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com \
      json5==0.13.0 requests==2.32.5 webdavclient3>=3.14.0 && \
    find /usr/local/lib/python3.12 -name '__pycache__' -type d -prune -exec rm -rf {} +

# ==================== Worker 依赖层 ====================
FROM common AS worker-deps

COPY requirements-core.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir \
      -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com \
      -r requirements-core.txt && \
    find /usr/local/lib/python3.12 -name '__pycache__' -type d -prune -exec rm -rf {} +

# ==================== WebUI 依赖层 ====================
FROM common AS webui-deps

COPY requirements-webui.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir \
      -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com \
      -r requirements-webui.txt && \
    find /usr/local/lib/python3.12 -name '__pycache__' -type d -prune -exec rm -rf {} +

# ==================== Worker 最终镜像（target: worker） ====================
FROM worker-deps AS worker

LABEL description="ArXiv Daily Researcher - LLM-powered paper monitoring and analysis"

# cron 只属于 worker；WebUI 目标不装它。
RUN apt-get update && \
    apt-get install -y --no-install-recommends cron && \
    rm -rf /var/lib/apt/lists/*

COPY main.py VERSION* ./
# worker 从不运行 Streamlit，刻意不带 src/webui/。
COPY src/agents/ ./src/agents/
COPY src/config.py ./src/
COPY src/keyword_tracker/ ./src/keyword_tracker/
COPY src/modes/ ./src/modes/
COPY src/notifications/ ./src/notifications/
COPY src/parsers/ ./src/parsers/
COPY src/report/ ./src/report/
COPY src/scoring_policy.py ./src/
COPY src/sources/ ./src/sources/
COPY src/utils/ ./src/utils/
RUN touch ./src/__init__.py
COPY .env.example ./
COPY configs/ ./configs/

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN mkdir -p /app/data/reports/daily_research/markdown \
             /app/data/reports/daily_research/html \
             /app/data/reports/trend_research/markdown \
             /app/data/reports/trend_research/html \
             /app/data/reports/keyword_trend/markdown \
             /app/data/reports/keyword_trend/html \
             /app/data/history /app/data/reference_pdfs \
             /app/data/downloaded_pdfs /app/logs \
    && chmod -R 777 /app/data /app/logs

HEALTHCHECK --interval=300s --timeout=10s --retries=3 \
    CMD python -c "import sys; sys.path.insert(0,'src'); import config; print('OK')" || exit 1

ENTRYPOINT ["/entrypoint.sh"]

# ==================== WebUI 最终镜像（target: webui） ====================
FROM webui-deps AS webui

LABEL description="ArXiv Daily Researcher - Streamlit Config Panel"

COPY src/utils/config_io.py ./src/utils/
COPY src/utils/run_lock.py ./src/utils/
COPY src/utils/daily_research_store.py ./src/utils/
COPY src/utils/scoring_evaluation.py ./src/utils/
COPY src/utils/source_registry.py ./src/utils/
COPY src/utils/webdav_sync.py ./src/utils/
COPY src/utils/webui_trigger.py ./src/utils/
COPY src/webui/ ./src/webui/
COPY VERSION* ./
COPY .env.example ./
COPY configs/ ./configs/

RUN touch ./src/__init__.py ./src/utils/__init__.py

RUN mkdir -p /root/.streamlit && \
    printf '[server]\nheadless = true\nport = 8501\naddress = "0.0.0.0"\nenableCORS = true\nenableXsrfProtection = true\nfileWatcherType = "none"\n\n[browser]\ngatherUsageStats = false\n' > /root/.streamlit/config.toml

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "src/webui/config_panel.py", "--server.port=8501"]
