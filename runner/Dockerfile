# Shared agent sandbox for every arm: same Python stack the task promises
# (NumPy, pandas, scikit-learn, SciPy, PyTorch), plus each harness's CLI.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git ripgrep \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir numpy pandas scikit-learn scipy pyarrow \
 && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
# Harness2 executor: OpenCode, pinned to the version in Harness2's wb_runner config.
RUN npm install -g opencode-ai@1.14.18 @ai-sdk/openai-compatible
# Arm A harness: OmniCLI.
COPY omnicli-src /opt/omnicli
RUN pip install --no-cache-dir /opt/omnicli
RUN useradd -m -u 1000 agent
USER agent
WORKDIR /work
