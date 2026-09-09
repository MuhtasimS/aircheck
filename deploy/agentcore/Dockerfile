# AIRCheck S3 diagnosis agent — Amazon Bedrock AgentCore Runtime container image.
# AgentCore Runtime is ARM64/Graviton; this image is built natively on an ARM64
# CodeBuild environment (no local Docker). Dependencies are baked at build time so
# the container starts (imports + app.run() on port 8080) well within the 30s
# runtime-initialization budget — CodeZip's runtime dependency install did not.
FROM public.ecr.aws/docker/library/python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /var/task

COPY deploy/agentcore/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY deploy/agentcore/main.py ./main.py

# BedrockAgentCoreApp serves HTTP on 8080 with POST /invocations and GET /ping.
EXPOSE 8080
CMD ["python", "main.py"]
