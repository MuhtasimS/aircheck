# AIRCheck API — AWS Lambda container image (public HTTPS via an API Gateway HTTP
# API, served by the AWS Lambda Web Adapter). One portable OCI image: it runs the
# unchanged FastAPI application boundary over the frozen M4 deterministic runtime,
# with ffmpeg/ffprobe for deterministic media truth. The same image is Fargate-
# compatible (it is a normal web server); Lambda + API Gateway is the ingress
# because it provides managed public HTTPS with no custom domain and no ALB (public
# Lambda Function URLs are blocked account-wide), and builds in the cloud (CodeBuild)
# with no local Docker. See DECISIONS D-019.
FROM public.ecr.aws/docker/library/python:3.13-slim

# AWS Lambda Web Adapter: bridges the Lambda runtime API to a normal web server,
# so the FastAPI app is unchanged. Pinned by digest-tag for reproducibility.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.0 /lambda-adapter /opt/extensions/lambda-adapter

# ffmpeg + ffprobe: Tier-0 inspection and the Tier-2 loudness-normalization
# remediation. Baking the universe below uses THIS ffmpeg, so runtime source
# generation and inspection share one encoder.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    AWS_LWA_PORT=8000 \
    AWS_LWA_INVOKE_MODE=buffered \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    AIRCHECK_GATE=M7 \
    AIRCHECK_STORE_ROOT=/tmp/aircheck/store \
    AIRCHECK_WORKSPACE_ROOT=/tmp/aircheck/workspace \
    AIRCHECK_UNIVERSE_ROOT=/opt/aircheck/universe

WORKDIR /var/task

COPY deploy/requirements-api.txt ./requirements-api.txt
RUN pip install --no-cache-dir -r requirements-api.txt

# Application source (frozen runtime + API boundary + synthetic universe + specs).
COPY aircheck ./aircheck
COPY apps ./apps
COPY synthetic ./synthetic
COPY specifications ./specifications
COPY pyproject.toml ./pyproject.toml

# Bake the frozen synthetic universe once, with this image's ffmpeg, so it is
# read-only and deterministic at runtime (Lambda disk outside /tmp is read-only).
RUN python -c "from apps.api.service import AIRCheckService; AIRCheckService().get_universe(); print('universe baked')"

# The deployed revision. CodeBuild passes the exact committed Git SHA; /health
# reports it truthfully. Placed last so earlier layers cache across revisions.
ARG AIRCHECK_GIT_SHA=unknown
ENV AIRCHECK_GIT_SHA=${AIRCHECK_GIT_SHA}

CMD ["python", "-m", "uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
