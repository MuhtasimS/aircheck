"""AIRCheck deployment orchestrator (reproducible, idempotent, boto3).

Topology (see docs/evidence and DECISIONS D-019):
  - ECR repos `aircheck-api` / `aircheck-web` / `aircheck-diagnosis-agent` hold the
    container images, built in the cloud by CodeBuild (no local Docker) and pushed
    with an immutable release-SHA tag plus a `latest` convenience alias.
  - AWS Lambda `aircheck-api` / `aircheck-web` run those images behind public HTTPS
    API Gateway HTTP APIs via the AWS Lambda Web Adapter. (Public Lambda Function
    URLs are blocked account-wide, so API Gateway is the ingress — D-019; the
    Function-URL step below is best-effort and skipped when blocked.)
  - S3 `aircheck-<acct>-state` is the durable S3RunStore system of record under a
    per-release prefix (M7: `aircheck`; M8: `aircheck-m8`, keeping M7 state intact);
    Lambda /tmp is a disposable cache. `aircheck-<acct>-build` holds CodeBuild source.
  - CloudWatch logs/metrics + an error alarm; AgentCore wired separately.

Every resource is tagged Project=AIRCheck, Gate=M8, Environment=hackathon and
named `aircheck-*`. Run one subcommand at a time, e.g.:

  python deploy/deploy.py ecr        --profile aircheck-bedrock --region us-east-1
  python deploy/deploy.py buckets
  python deploy/deploy.py iam
  python deploy/deploy.py build      --sha <git-sha>
  python deploy/deploy.py lambda     --sha <git-sha> --s3-prefix aircheck-m8 --cors <origins>
  python deploy/deploy.py apigw
  python deploy/deploy.py frontend   --sha <git-sha>
  python deploy/deploy.py agentcore  --sha <git-sha>
  python deploy/deploy.py wire-agentcore --runtime-arn <arn>
  python deploy/deploy.py harden-iam            # narrow AgentCore role; retire inert deployer
  python deploy/deploy.py manifest   --sha <git-sha> --out <path>
  python deploy/deploy.py info

Credentials come from the named profile / environment; nothing is printed.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
TAGS = {"Project": "AIRCheck", "Gate": "M8", "Environment": "hackathon"}
ECR_REPO = "aircheck-api"
CODEBUILD_ROLE = "aircheck-codebuild-role"
CODEBUILD_PROJECT = "aircheck-api-build"
LAMBDA_ROLE = "aircheck-lambda-role"
LAMBDA_FN = "aircheck-api"
ALARM_NAME = "aircheck-api-errors"
S3_PREFIX = "aircheck"
DEFAULT_MODEL_ID = "us.amazon.nova-lite-v1:0"
AGENTCORE_ROLE = "aircheck-agentcore-role"
AGENTCORE_RUNTIME = "aircheck_diagnosis"
AGENT_ECR_REPO = "aircheck-diagnosis-agent"
AGENT_CODEBUILD_PROJECT = "aircheck-agent-build"
WEB_ECR_REPO = "aircheck-web"
WEB_CODEBUILD_PROJECT = "aircheck-web-build"
WEB_LAMBDA_FN = "aircheck-web"
WEB_LAMBDA_ROLE = "aircheck-web-role"
API_HTTP_NAME = "aircheck-http"
WEB_HTTP_NAME = "aircheck-web-http"
FRONTEND_SKIP = {
    "node_modules", "dist", ".next", ".vinext", ".wrangler",
    "coverage", "__pycache__", ".git", ".venv",
}

# Source subset the container needs (keeps the CodeBuild context small and the
# public image free of the web app / tests / local state).
SOURCE_INCLUDE = [
    "aircheck",
    "apps/api",
    "synthetic",
    "specifications",
    "deploy/Dockerfile",
    "deploy/requirements-api.txt",
    "deploy/buildspec.yml",
    "deploy/verify_buildspec.yml",
    "deploy/verify_hero.py",
    "deploy/external_verify.sh",
    "deploy/agentcore",
    "pyproject.toml",
]
SKIP_DIR = {"__pycache__", ".pytest_cache", "node_modules", ".git", ".venv"}


def tag_list() -> list[dict]:
    return [{"Key": k, "Value": v} for k, v in TAGS.items()]


class Ctx:
    def __init__(self, args) -> None:
        self.session = boto3.Session(
            profile_name=args.profile, region_name=args.region
        )
        self.region = args.region
        self.account = self.session.client("sts").get_caller_identity()["Account"]
        self.state_bucket = f"aircheck-{self.account}-state"
        self.build_bucket = f"aircheck-{self.account}-build"
        self.ecr_registry = f"{self.account}.dkr.ecr.{self.region}.amazonaws.com"
        self.ecr_uri = f"{self.ecr_registry}/{ECR_REPO}"

    def client(self, name: str):
        return self.session.client(name)


# --------------------------------------------------------------------------- ECR


def cmd_ecr(ctx: Ctx, args) -> None:
    ecr = ctx.client("ecr")
    try:
        ecr.create_repository(
            repositoryName=ECR_REPO,
            imageScanningConfiguration={"scanOnPush": True},
            imageTagMutability="MUTABLE",
            tags=tag_list(),
        )
        print(f"created ECR repo {ECR_REPO}")
    except ecr.exceptions.RepositoryAlreadyExistsException:
        print(f"ECR repo {ECR_REPO} already exists")
    uri = ecr.describe_repositories(repositoryNames=[ECR_REPO])["repositories"][0][
        "repositoryUri"
    ]
    print(f"ECR_URI={uri}")


# ------------------------------------------------------------------------ buckets


def _ensure_bucket(s3, name: str, region: str, *, versioning: bool = False) -> None:
    try:
        if region == "us-east-1":
            s3.create_bucket(Bucket=name)
        else:
            s3.create_bucket(
                Bucket=name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        print(f"created bucket {name}")
    except (s3.exceptions.BucketAlreadyOwnedByYou, s3.exceptions.BucketAlreadyExists):
        print(f"bucket {name} already exists")
    s3.put_public_access_block(
        Bucket=name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_tagging(Bucket=name, Tagging={"TagSet": tag_list()})
    if versioning:
        s3.put_bucket_versioning(
            Bucket=name, VersioningConfiguration={"Status": "Enabled"}
        )


def cmd_buckets(ctx: Ctx, args) -> None:
    s3 = ctx.client("s3")
    _ensure_bucket(s3, ctx.state_bucket, ctx.region, versioning=True)
    _ensure_bucket(s3, ctx.build_bucket, ctx.region)
    print(f"STATE_BUCKET={ctx.state_bucket}")
    print(f"BUILD_BUCKET={ctx.build_bucket}")


# ---------------------------------------------------------------------------- IAM


def _ensure_role(iam, name: str, trust: dict, description: str) -> str:
    try:
        iam.create_role(
            RoleName=name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description=description,
            Tags=tag_list(),
        )
        print(f"created role {name}")
    except iam.exceptions.EntityAlreadyExistsException:
        print(f"role {name} already exists")
    return iam.get_role(RoleName=name)["Role"]["Arn"]


def cmd_iam(ctx: Ctx, args) -> None:
    iam = ctx.client("iam")

    codebuild_trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "codebuild.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    cb_arn = _ensure_role(
        iam, CODEBUILD_ROLE, codebuild_trust, "AIRCheck M7 CodeBuild image builder"
    )
    cb_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "Logs",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": f"arn:aws:logs:{ctx.region}:{ctx.account}:log-group:/aws/codebuild/*",
            },
            {
                "Sid": "EcrAuth",
                "Effect": "Allow",
                "Action": ["ecr:GetAuthorizationToken"],
                "Resource": "*",
            },
            {
                "Sid": "EcrPush",
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:CompleteLayerUpload",
                    "ecr:InitiateLayerUpload",
                    "ecr:PutImage",
                    "ecr:UploadLayerPart",
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                ],
                "Resource": f"arn:aws:ecr:{ctx.region}:{ctx.account}:repository/aircheck-*",
            },
            {
                "Sid": "BuildBucket",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:GetBucketLocation"],
                "Resource": [
                    f"arn:aws:s3:::{ctx.build_bucket}",
                    f"arn:aws:s3:::{ctx.build_bucket}/*",
                ],
            },
        ],
    }
    iam.put_role_policy(
        RoleName=CODEBUILD_ROLE,
        PolicyName="aircheck-codebuild-inline",
        PolicyDocument=json.dumps(cb_policy),
    )
    print(f"CODEBUILD_ROLE_ARN={cb_arn}")

    lambda_trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    la_arn = _ensure_role(
        iam, LAMBDA_ROLE, lambda_trust, "AIRCheck M7 API Lambda execution role"
    )
    iam.attach_role_policy(
        RoleName=LAMBDA_ROLE,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    la_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "StateBucket",
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                    "s3:ListBucket",
                    "s3:GetBucketLocation",
                ],
                "Resource": [
                    f"arn:aws:s3:::{ctx.state_bucket}",
                    f"arn:aws:s3:::{ctx.state_bucket}/*",
                ],
            }
        ],
    }
    iam.put_role_policy(
        RoleName=LAMBDA_ROLE,
        PolicyName="aircheck-lambda-state",
        PolicyDocument=json.dumps(la_policy),
    )
    print(f"LAMBDA_ROLE_ARN={la_arn}")


# -------------------------------------------------------------------------- build


def _make_source_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in SOURCE_INCLUDE:
            path = ROOT / entry
            if path.is_file():
                zf.write(path, entry)
                continue
            for file in path.rglob("*"):
                if not file.is_file():
                    continue
                if any(part in SKIP_DIR for part in file.parts):
                    continue
                if file.suffix in {".pyc", ".pyo"}:
                    continue
                zf.write(file, file.relative_to(ROOT).as_posix())
    return buf.getvalue()


def _ensure_codebuild_project(ctx: Ctx) -> None:
    cb = ctx.client("codebuild")
    iam_arn = ctx.client("iam").get_role(RoleName=CODEBUILD_ROLE)["Role"]["Arn"]
    env_vars = [
        {"name": "AWS_REGION", "value": ctx.region},
        {"name": "ECR_REGISTRY", "value": ctx.ecr_registry},
        {"name": "ECR_URI", "value": ctx.ecr_uri},
        {"name": "AIRCHECK_GIT_SHA", "value": "unknown"},
    ]
    source = {
        "type": "S3",
        "location": f"{ctx.build_bucket}/source.zip",
        "buildspec": "deploy/buildspec.yml",
    }
    environment = {
        "type": "LINUX_CONTAINER",
        "image": "aws/codebuild/standard:7.0",
        "computeType": "BUILD_GENERAL1_SMALL",
        "privilegedMode": True,
        "environmentVariables": env_vars,
    }
    artifacts = {"type": "S3", "location": ctx.build_bucket, "path": "artifacts", "packaging": "NONE"}
    try:
        cb.create_project(
            name=CODEBUILD_PROJECT,
            source=source,
            artifacts=artifacts,
            environment=environment,
            serviceRole=iam_arn,
            tags=[{"key": k, "value": v} for k, v in TAGS.items()],
        )
        print(f"created CodeBuild project {CODEBUILD_PROJECT}")
    except cb.exceptions.ResourceAlreadyExistsException:
        cb.update_project(
            name=CODEBUILD_PROJECT,
            source=source,
            artifacts=artifacts,
            environment=environment,
            serviceRole=iam_arn,
        )
        print(f"updated CodeBuild project {CODEBUILD_PROJECT}")


def cmd_build(ctx: Ctx, args) -> None:
    sha = args.sha or "unknown"
    s3 = ctx.client("s3")
    data = _make_source_zip()
    s3.put_object(Bucket=ctx.build_bucket, Key="source.zip", Body=data)
    print(f"uploaded source.zip ({len(data)} bytes) to {ctx.build_bucket}")
    _ensure_codebuild_project(ctx)
    cb = ctx.client("codebuild")
    build = cb.start_build(
        projectName=CODEBUILD_PROJECT,
        environmentVariablesOverride=[
            {"name": "AIRCHECK_GIT_SHA", "value": sha},
        ],
    )["build"]
    build_id = build["id"]
    print(f"started build {build_id} (sha={sha}) — polling...")
    while True:
        time.sleep(12)
        b = cb.batch_get_builds(ids=[build_id])["builds"][0]
        status = b["buildStatus"]
        phase = b.get("currentPhase")
        print(f"  status={status} phase={phase}")
        if status != "IN_PROGRESS":
            break
    if status != "SUCCEEDED":
        print(f"BUILD FAILED: {status}", file=sys.stderr)
        sys.exit(2)
    print(f"BUILD SUCCEEDED image={ctx.ecr_uri}:{sha}")


# ------------------------------------------------------------------------- lambda


def _lambda_env(ctx: Ctx, args) -> dict:
    env = {
        "AIRCHECK_S3_BUCKET": ctx.state_bucket,
        # Per-release state prefix: M8 uses `aircheck-m8` so the M7 `aircheck` tree
        # is preserved intact for evidence while the release demo state is clean.
        "AIRCHECK_S3_PREFIX": getattr(args, "s3_prefix", None) or S3_PREFIX,
        "AIRCHECK_GATE": getattr(args, "gate", None) or "M8",
        "AIRCHECK_GIT_SHA": args.sha or "unknown",
        # Product-safe CORS: restrict to the real public UI origin plus explicit
        # local-development origins (M8 audit P8). Defaults to "*" only if unset.
        "AIRCHECK_CORS_ORIGINS": args.cors or "*",
        "AIRCHECK_AGENTCORE_MODEL_ID": args.model_id or DEFAULT_MODEL_ID,
    }
    if args.runtime_arn:
        env["AIRCHECK_AGENTCORE_RUNTIME_ARN"] = args.runtime_arn
    return env


def cmd_lambda(ctx: Ctx, args) -> None:
    lam = ctx.client("lambda")
    role_arn = ctx.client("iam").get_role(RoleName=LAMBDA_ROLE)["Role"]["Arn"]
    image = f"{ctx.ecr_uri}:{args.sha}" if args.sha else f"{ctx.ecr_uri}:latest"
    env = _lambda_env(ctx, args)
    exists = True
    try:
        lam.get_function(FunctionName=LAMBDA_FN)
    except lam.exceptions.ResourceNotFoundException:
        exists = False

    if not exists:
        for attempt in range(10):
            try:
                lam.create_function(
                    FunctionName=LAMBDA_FN,
                    PackageType="Image",
                    Code={"ImageUri": image},
                    Role=role_arn,
                    Timeout=300,
                    MemorySize=2048,
                    Architectures=["x86_64"],
                    Environment={"Variables": env},
                    Tags=TAGS,
                )
                break
            except lam.exceptions.InvalidParameterValueException as exc:
                # New role propagation can lag; retry briefly.
                if "cannot be assumed" in str(exc) and attempt < 9:
                    time.sleep(6)
                    continue
                raise
        print(f"created Lambda {LAMBDA_FN}")
    else:
        lam.update_function_code(FunctionName=LAMBDA_FN, ImageUri=image, Publish=False)
        _wait_updated(lam)
        lam.update_function_configuration(
            FunctionName=LAMBDA_FN,
            Role=role_arn,
            Timeout=300,
            MemorySize=2048,
            Environment={"Variables": env},
        )
        print(f"updated Lambda {LAMBDA_FN}")
    _wait_updated(lam)

    # Reserved concurrency 1 keeps the S3RunStore mirror single-writer safe when
    # the account limit allows it. On a low-limit account (default 10, which
    # cannot leave >=10 unreserved) this is skipped; the incremental per-request
    # sync_down keeps cross-container state fresh instead.
    try:
        lam.put_function_concurrency(
            FunctionName=LAMBDA_FN, ReservedConcurrentExecutions=1
        )
        print("reserved concurrency = 1")
    except ClientError as exc:
        print(f"reserved concurrency skipped: {exc.response['Error']['Code']}")

    # No public Lambda Function URL. API Gateway is the single public ingress
    # (D-019); a Function URL would be a redundant second unauthenticated endpoint
    # with its own CORS, undercutting the product-safe CORS restriction (M8 P8), so
    # it is intentionally not created. `apigw` wires the API Gateway HTTP API.
    print("public ingress: API Gateway (no Lambda Function URL)")


def _wait_updated(lam) -> None:
    for _ in range(60):
        cfg = lam.get_function_configuration(FunctionName=LAMBDA_FN)
        if cfg.get("LastUpdateStatus") != "InProgress" and cfg.get("State") != "Pending":
            return
        time.sleep(4)


# -------------------------------------------------------------------------- alarm


def cmd_observability(ctx: Ctx, args) -> None:
    """Enable CloudWatch Transaction Search (one-time account setup for spans).

    AgentCore Runtime emits OTEL spans; Transaction Search indexes them so the
    GenAI Observability view shows traces. This grants X-Ray permission to write
    spans to CloudWatch Logs and points the trace-segment destination at Logs.
    """
    logs = ctx.client("logs")
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "TransactionSearchXRayAccess",
                "Effect": "Allow",
                "Principal": {"Service": "xray.amazonaws.com"},
                "Action": "logs:PutLogEvents",
                "Resource": [
                    f"arn:aws:logs:{ctx.region}:{ctx.account}:log-group:aws/spans:*",
                    f"arn:aws:logs:{ctx.region}:{ctx.account}:log-group:/aws/application-signals/data:*",
                ],
                "Condition": {
                    "ArnLike": {"aws:SourceArn": f"arn:aws:xray:{ctx.region}:{ctx.account}:*"},
                    "StringEquals": {"aws:SourceAccount": ctx.account},
                },
            }
        ],
    }
    logs.put_resource_policy(
        policyName="AIRCheckTransactionSearchAccess",
        policyDocument=json.dumps(policy),
    )
    print("put logs resource policy for Transaction Search")
    xray = ctx.client("xray")
    xray.update_trace_segment_destination(Destination="CloudWatchLogs")
    print("set X-Ray trace segment destination = CloudWatchLogs")
    try:
        dest = xray.get_trace_segment_destination()
        print(f"destination={dest.get('Destination')} status={dest.get('Status')}")
    except ClientError as exc:
        print(f"(destination status unavailable: {exc.response['Error']['Code']})")


def cmd_alarm(ctx: Ctx, args) -> None:
    cw = ctx.client("cloudwatch")
    cw.put_metric_alarm(
        AlarmName=ALARM_NAME,
        AlarmDescription="AIRCheck API Lambda errors (M7).",
        Namespace="AWS/Lambda",
        MetricName="Errors",
        Dimensions=[{"Name": "FunctionName", "Value": LAMBDA_FN}],
        Statistic="Sum",
        Period=300,
        EvaluationPeriods=1,
        Threshold=1.0,
        ComparisonOperator="GreaterThanOrEqualToThreshold",
        TreatMissingData="notBreaching",
        Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()],
    )
    print(f"put alarm {ALARM_NAME}")


# --------------------------------------------------------------- agentcore deploy


def _nova_resources(ctx: Ctx, model_id: str) -> list[str]:
    """Least-privilege Bedrock resources for the configured Nova inference profile.

    Resolves the inference-profile ARN plus its backing foundation-model ARNs so the
    AgentCore role can invoke ONLY that model (M8 audit P6), while preserving both
    the inference-profile and backing foundation-model permissions AWS requires for a
    cross-region (``us.*``) profile. Falls back to the computed Nova ARNs if the
    control-plane lookup is unavailable.
    """
    resources: list[str] = []
    try:
        profile = ctx.client("bedrock").get_inference_profile(
            inferenceProfileIdentifier=model_id
        )
        if profile.get("inferenceProfileArn"):
            resources.append(profile["inferenceProfileArn"])
        for model in profile.get("models", []):
            if model.get("modelArn"):
                resources.append(model["modelArn"])
    except Exception as exc:  # pragma: no cover - network/permission variance
        print(f"  (inference-profile lookup failed: {type(exc).__name__}; using computed ARNs)")
    if not resources:
        base = model_id.split(".", 1)[-1] if model_id.startswith("us.") else model_id
        resources = [
            f"arn:aws:bedrock:{ctx.region}:{ctx.account}:inference-profile/{model_id}",
            *[
                f"arn:aws:bedrock:{r}::foundation-model/{base}"
                for r in ("us-east-1", "us-east-2", "us-west-2")
            ],
        ]
    return sorted(set(resources))


def _ensure_agentcore_role(ctx: Ctx, model_id: str) -> str:
    iam = ctx.client("iam")
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": ctx.account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{ctx.region}:{ctx.account}:*"
                    },
                },
            }
        ],
    }
    arn = _ensure_role(
        iam, AGENTCORE_ROLE, trust, "AIRCheck M7 AgentCore diagnosis runtime execution role"
    )
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "BedrockModel",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                # Least-privilege: only the configured Nova Lite inference profile and
                # its backing foundation models, not every Bedrock model (M8 audit P6).
                "Resource": _nova_resources(ctx, model_id),
            },
            {
                "Sid": "EcrAuth",
                "Effect": "Allow",
                "Action": "ecr:GetAuthorizationToken",
                "Resource": "*",
            },
            {
                "Sid": "EcrPull",
                "Effect": "Allow",
                "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                "Resource": f"arn:aws:ecr:{ctx.region}:{ctx.account}:repository/{AGENT_ECR_REPO}",
            },
            {
                "Sid": "Logs",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                    "logs:DescribeLogGroups",
                ],
                "Resource": f"arn:aws:logs:{ctx.region}:{ctx.account}:log-group:/aws/bedrock-agentcore/runtimes/*",
            },
            {
                "Sid": "XRay",
                "Effect": "Allow",
                "Action": [
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                "Resource": "*",
            },
            {
                "Sid": "Metrics",
                "Effect": "Allow",
                "Action": "cloudwatch:PutMetricData",
                "Resource": "*",
                "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
            },
            {
                "Sid": "WorkloadIdentity",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                ],
                "Resource": "*",
            },
        ],
    }
    iam.put_role_policy(
        RoleName=AGENTCORE_ROLE,
        PolicyName="aircheck-agentcore-inline",
        PolicyDocument=json.dumps(policy),
    )
    return arn


def _agent_ecr_uri(ctx: Ctx) -> str:
    return f"{ctx.ecr_registry}/{AGENT_ECR_REPO}"


def _ensure_agent_ecr(ctx: Ctx) -> None:
    ecr = ctx.client("ecr")
    try:
        ecr.create_repository(
            repositoryName=AGENT_ECR_REPO,
            imageScanningConfiguration={"scanOnPush": True},
            tags=tag_list(),
        )
        print(f"created ECR repo {AGENT_ECR_REPO}")
    except ecr.exceptions.RepositoryAlreadyExistsException:
        print(f"ECR repo {AGENT_ECR_REPO} already exists")


def _ensure_agent_codebuild(ctx: Ctx) -> None:
    cb = ctx.client("codebuild")
    iam_arn = ctx.client("iam").get_role(RoleName=CODEBUILD_ROLE)["Role"]["Arn"]
    source = {
        "type": "S3",
        "location": f"{ctx.build_bucket}/source.zip",
        "buildspec": "deploy/agentcore/buildspec.yml",
    }
    # Native ARM64 build environment — AgentCore Runtime is Graviton/ARM64.
    environment = {
        "type": "ARM_CONTAINER",
        "image": "aws/codebuild/amazonlinux2-aarch64-standard:3.0",
        "computeType": "BUILD_GENERAL1_SMALL",
        "privilegedMode": True,
        "environmentVariables": [
            {"name": "AWS_REGION", "value": ctx.region},
            {"name": "ECR_REGISTRY", "value": ctx.ecr_registry},
            {"name": "AGENT_ECR_URI", "value": _agent_ecr_uri(ctx)},
            {"name": "AIRCHECK_GIT_SHA", "value": "latest"},
        ],
    }
    artifacts = {"type": "NO_ARTIFACTS"}
    try:
        cb.create_project(
            name=AGENT_CODEBUILD_PROJECT,
            source=source,
            artifacts=artifacts,
            environment=environment,
            serviceRole=iam_arn,
            tags=[{"key": k, "value": v} for k, v in TAGS.items()],
        )
        print(f"created CodeBuild project {AGENT_CODEBUILD_PROJECT}")
    except cb.exceptions.ResourceAlreadyExistsException:
        cb.update_project(
            name=AGENT_CODEBUILD_PROJECT,
            source=source,
            artifacts=artifacts,
            environment=environment,
            serviceRole=iam_arn,
        )
        print(f"updated CodeBuild project {AGENT_CODEBUILD_PROJECT}")


def _find_runtime(c, name: str):
    for rt in c.list_agent_runtimes().get("agentRuntimes", []):
        if rt.get("agentRuntimeName") == name:
            return rt
    return None


def cmd_agentcore(ctx: Ctx, args) -> None:
    tag = args.sha or "latest"
    role_arn = _ensure_agentcore_role(ctx, args.model_id or DEFAULT_MODEL_ID)
    print(f"agentcore role: {role_arn}")
    _ensure_agent_ecr(ctx)

    # Upload the current source (includes deploy/agentcore) and build the ARM64 image.
    s3 = ctx.client("s3")
    s3.put_object(Bucket=ctx.build_bucket, Key="source.zip", Body=_make_source_zip())
    _ensure_agent_codebuild(ctx)
    cb = ctx.client("codebuild")
    build = cb.start_build(
        projectName=AGENT_CODEBUILD_PROJECT,
        environmentVariablesOverride=[{"name": "AIRCHECK_GIT_SHA", "value": tag}],
    )["build"]
    build_id = build["id"]
    print(f"started agent build {build_id} (tag={tag}) — polling...")
    while True:
        time.sleep(12)
        b = cb.batch_get_builds(ids=[build_id])["builds"][0]
        status = b["buildStatus"]
        print(f"  status={status} phase={b.get('currentPhase')}")
        if status != "IN_PROGRESS":
            break
    if status != "SUCCEEDED":
        print(f"AGENT BUILD FAILED: {status}", file=sys.stderr)
        sys.exit(2)
    container_uri = f"{_agent_ecr_uri(ctx)}:{tag}"
    print(f"agent image {container_uri}")

    # Role propagation before AgentCore can assume it.
    time.sleep(10)
    c = ctx.client("bedrock-agentcore-control")
    artifact = {"containerConfiguration": {"containerUri": container_uri}}
    env = {"AIRCHECK_AGENTCORE_MODEL_ID": args.model_id or DEFAULT_MODEL_ID}
    existing = _find_runtime(c, AGENTCORE_RUNTIME)
    if existing is None:
        resp = c.create_agent_runtime(
            agentRuntimeName=AGENTCORE_RUNTIME,
            agentRuntimeArtifact=artifact,
            roleArn=role_arn,
            networkConfiguration={"networkMode": "PUBLIC"},
            protocolConfiguration={"serverProtocol": "HTTP"},
            environmentVariables=env,
            description="AIRCheck S3 diagnosis agent (M7)",
            tags=TAGS,
        )
        arn = resp["agentRuntimeArn"]
        print(f"created agent runtime {arn}")
    else:
        rt_id = existing["agentRuntimeId"]
        resp = c.update_agent_runtime(
            agentRuntimeId=rt_id,
            agentRuntimeArtifact=artifact,
            roleArn=role_arn,
            networkConfiguration={"networkMode": "PUBLIC"},
            protocolConfiguration={"serverProtocol": "HTTP"},
            environmentVariables=env,
            description="AIRCheck S3 diagnosis agent (M7)",
        )
        arn = resp["agentRuntimeArn"]
        print(f"updated agent runtime {arn}")

    rt_id = arn.rsplit("/", 1)[-1]
    for _ in range(60):
        time.sleep(8)
        g = c.get_agent_runtime(agentRuntimeId=rt_id)
        status = g.get("status")
        print(f"  status={status}")
        if status not in ("CREATING", "UPDATING"):
            break
    if status not in ("READY",):
        print(f"AGENTCORE NOT READY: {status} reason={g.get('failureReason')}", file=sys.stderr)
        sys.exit(2)
    print(f"AGENTCORE_RUNTIME_ARN={arn}")


# ----------------------------------------------------------------- wire agentcore


def cmd_wire_agentcore(ctx: Ctx, args) -> None:
    if not args.runtime_arn:
        print("--runtime-arn is required", file=sys.stderr)
        sys.exit(2)
    iam = ctx.client("iam")
    # Grant only InvokeAgentRuntime on the specific runtime (+ its endpoints).
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeAgentCore",
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": [args.runtime_arn, args.runtime_arn + "/*"],
            }
        ],
    }
    iam.put_role_policy(
        RoleName=LAMBDA_ROLE,
        PolicyName="aircheck-lambda-agentcore",
        PolicyDocument=json.dumps(policy),
    )
    print("granted Lambda role bedrock-agentcore:InvokeAgentRuntime")
    lam = ctx.client("lambda")
    _wait_updated(lam)
    cfg = lam.get_function_configuration(FunctionName=LAMBDA_FN)
    env = cfg.get("Environment", {}).get("Variables", {})
    env["AIRCHECK_AGENTCORE_RUNTIME_ARN"] = args.runtime_arn
    if args.model_id:
        env["AIRCHECK_AGENTCORE_MODEL_ID"] = args.model_id
    lam.update_function_configuration(
        FunctionName=LAMBDA_FN, Environment={"Variables": env}
    )
    _wait_updated(lam)
    print("wired AIRCHECK_AGENTCORE_RUNTIME_ARN into Lambda")


# ------------------------------------------------------------------------ apigw


def _ensure_http_api(ctx: Ctx, lambda_fn: str, api_name: str) -> tuple[str, str]:
    """Create (or find) a public HTTP API proxying to a Lambda; return (id, url)."""
    apigw = ctx.client("apigatewayv2")
    lam = ctx.client("lambda")
    lambda_arn = lam.get_function(FunctionName=lambda_fn)["Configuration"]["FunctionArn"]
    existing = next(
        (a for a in apigw.get_apis().get("Items", []) if a.get("Name") == api_name),
        None,
    )
    if existing:
        api_id, endpoint = existing["ApiId"], existing["ApiEndpoint"]
        print(f"HTTP API {api_name} already exists ({api_id})")
    else:
        r = apigw.create_api(
            Name=api_name, ProtocolType="HTTP", Target=lambda_arn, Tags=TAGS
        )
        api_id, endpoint = r["ApiId"], r["ApiEndpoint"]
        print(f"created HTTP API {api_name} ({api_id})")
    # Always (re)apply resource tags — the M7 `aircheck-http` API was untagged
    # (M8 audit P8) — and bounded default-stage throttling for a public demo.
    try:
        apigw.tag_resource(
            ResourceArn=f"arn:aws:apigateway:{ctx.region}::/apis/{api_id}", Tags=TAGS
        )
    except ClientError as exc:
        print(f"  tag skipped: {exc.response['Error']['Code']}")
    try:
        apigw.update_stage(
            ApiId=api_id,
            StageName="$default",
            DefaultRouteSettings={
                "ThrottlingBurstLimit": 50,
                "ThrottlingRateLimit": 20,
            },
        )
        print("  default-stage throttle: rate=20 burst=50")
    except ClientError as exc:
        print(f"  throttle skipped: {exc.response['Error']['Code']}")
    try:
        lam.add_permission(
            FunctionName=lambda_fn,
            StatementId=f"apigw-{api_id}",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{ctx.region}:{ctx.account}:{api_id}/*/*",
        )
    except lam.exceptions.ResourceConflictException:
        pass
    return api_id, endpoint


def _discover_api_base(ctx: Ctx) -> str:
    apigw = ctx.client("apigatewayv2")
    api = next(
        (a for a in apigw.get_apis().get("Items", []) if a.get("Name") == API_HTTP_NAME),
        None,
    )
    if api is None:
        raise SystemExit("API HTTP endpoint not found; deploy the API first")
    return api["ApiEndpoint"]


def cmd_apigw(ctx: Ctx, args) -> None:
    api_id, endpoint = _ensure_http_api(ctx, LAMBDA_FN, API_HTTP_NAME)
    print(f"API_URL={endpoint}")


def _web_base(ctx: Ctx) -> str:
    apigw = ctx.client("apigatewayv2")
    api = next(
        (a for a in apigw.get_apis().get("Items", []) if a.get("Name") == WEB_HTTP_NAME),
        None,
    )
    return api["ApiEndpoint"] if api else ""


def cmd_external_verify(ctx: Ctx, args) -> None:
    """Verify the public deployment from an independent AWS CodeBuild host."""
    expected_sha = args.sha
    if not expected_sha:
        raise SystemExit("--sha <deployed git sha> is required")
    api_base = args.api_base or _discover_api_base(ctx)
    web_base = _web_base(ctx)
    s3 = ctx.client("s3")
    s3.put_object(Bucket=ctx.build_bucket, Key="source.zip", Body=_make_source_zip())
    cb = ctx.client("codebuild")
    build = cb.start_build(
        projectName=CODEBUILD_PROJECT,
        buildspecOverride="deploy/verify_buildspec.yml",
        environmentVariablesOverride=[
            {"name": "AIRCHECK_API_BASE", "value": api_base},
            {"name": "AIRCHECK_WEB_BASE", "value": web_base},
            {"name": "EXPECTED_SHA", "value": expected_sha},
        ],
    )["build"]
    build_id = build["id"]
    print(f"started external verify build {build_id} (expected sha={expected_sha})")
    status = "IN_PROGRESS"
    b = build
    while status == "IN_PROGRESS":
        time.sleep(12)
        b = cb.batch_get_builds(ids=[build_id])["builds"][0]
        status = b["buildStatus"]
        print(f"  status={status} phase={b.get('currentPhase')}")
    # Print the build log so the caller can capture it as a receipt.
    logs_info = b.get("logs", {})
    group, stream = logs_info.get("groupName"), logs_info.get("streamName")
    print("----- CODEBUILD LOG -----")
    if group and stream:
        logs = ctx.client("logs")
        token = None
        for _ in range(20):
            kw = {"logGroupName": group, "logStreamName": stream, "startFromHead": True}
            if token:
                kw["nextToken"] = token
            resp = logs.get_log_events(**kw)
            for ev in resp.get("events", []):
                print(ev["message"].rstrip("\n"))
            nt = resp.get("nextForwardToken")
            if nt == token:
                break
            token = nt
    print("----- END LOG -----")
    print(f"EXTERNAL VERIFY: {status}")
    if status != "SUCCEEDED":
        sys.exit(2)


# --------------------------------------------------------------------- frontend


def _make_frontend_zip() -> bytes:
    buf = io.BytesIO()
    web = ROOT / "apps" / "web"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for extra in ("deploy/frontend/Dockerfile", "deploy/frontend/buildspec.yml"):
            zf.write(ROOT / extra, extra)
        for file in web.rglob("*"):
            if not file.is_file():
                continue
            if any(part in FRONTEND_SKIP for part in file.parts):
                continue
            if file.suffix in {".pyc", ".pyo"}:
                continue
            zf.write(file, file.relative_to(ROOT).as_posix())
    return buf.getvalue()


def _ensure_web_role(ctx: Ctx) -> str:
    iam = ctx.client("iam")
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    arn = _ensure_role(iam, WEB_LAMBDA_ROLE, trust, "AIRCheck M7 web (SSR) execution role")
    iam.attach_role_policy(
        RoleName=WEB_LAMBDA_ROLE,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    return arn


def _ensure_web_codebuild(ctx: Ctx) -> None:
    cb = ctx.client("codebuild")
    iam_arn = ctx.client("iam").get_role(RoleName=CODEBUILD_ROLE)["Role"]["Arn"]
    web_uri = f"{ctx.ecr_registry}/{WEB_ECR_REPO}"
    source = {
        "type": "S3",
        "location": f"{ctx.build_bucket}/frontend-source.zip",
        "buildspec": "deploy/frontend/buildspec.yml",
    }
    environment = {
        "type": "LINUX_CONTAINER",
        "image": "aws/codebuild/standard:7.0",
        "computeType": "BUILD_GENERAL1_MEDIUM",
        "privilegedMode": True,
        "environmentVariables": [
            {"name": "AWS_REGION", "value": ctx.region},
            {"name": "ECR_REGISTRY", "value": ctx.ecr_registry},
            {"name": "WEB_ECR_URI", "value": web_uri},
            {"name": "AIRCHECK_GIT_SHA", "value": "latest"},
        ],
    }
    artifacts = {"type": "NO_ARTIFACTS"}
    try:
        cb.create_project(
            name=WEB_CODEBUILD_PROJECT,
            source=source,
            artifacts=artifacts,
            environment=environment,
            serviceRole=iam_arn,
            tags=[{"key": k, "value": v} for k, v in TAGS.items()],
        )
        print(f"created CodeBuild project {WEB_CODEBUILD_PROJECT}")
    except cb.exceptions.ResourceAlreadyExistsException:
        cb.update_project(
            name=WEB_CODEBUILD_PROJECT,
            source=source,
            artifacts=artifacts,
            environment=environment,
            serviceRole=iam_arn,
        )
        print(f"updated CodeBuild project {WEB_CODEBUILD_PROJECT}")


def cmd_frontend(ctx: Ctx, args) -> None:
    tag = args.sha or "latest"
    api_base = args.api_base or _discover_api_base(ctx)
    print(f"frontend API base = {api_base}")
    role_arn = _ensure_web_role(ctx)
    ecr = ctx.client("ecr")
    try:
        ecr.create_repository(
            repositoryName=WEB_ECR_REPO,
            imageScanningConfiguration={"scanOnPush": True},
            tags=tag_list(),
        )
        print(f"created ECR repo {WEB_ECR_REPO}")
    except ecr.exceptions.RepositoryAlreadyExistsException:
        print(f"ECR repo {WEB_ECR_REPO} already exists")
    web_uri = f"{ctx.ecr_registry}/{WEB_ECR_REPO}"

    s3 = ctx.client("s3")
    data = _make_frontend_zip()
    s3.put_object(Bucket=ctx.build_bucket, Key="frontend-source.zip", Body=data)
    print(f"uploaded frontend-source.zip ({len(data)} bytes)")
    _ensure_web_codebuild(ctx)
    cb = ctx.client("codebuild")
    build = cb.start_build(
        projectName=WEB_CODEBUILD_PROJECT,
        environmentVariablesOverride=[{"name": "AIRCHECK_GIT_SHA", "value": tag}],
    )["build"]
    build_id = build["id"]
    print(f"started web build {build_id} — polling...")
    while True:
        time.sleep(15)
        b = cb.batch_get_builds(ids=[build_id])["builds"][0]
        status = b["buildStatus"]
        print(f"  status={status} phase={b.get('currentPhase')}")
        if status != "IN_PROGRESS":
            break
    if status != "SUCCEEDED":
        print(f"WEB BUILD FAILED: {status}", file=sys.stderr)
        sys.exit(2)
    image = f"{web_uri}:{tag}"

    lam = ctx.client("lambda")
    env = {"AIRCHECK_API_BASE": api_base, "AIRCHECK_GIT_SHA": tag}
    try:
        lam.get_function(FunctionName=WEB_LAMBDA_FN)
        lam.update_function_code(FunctionName=WEB_LAMBDA_FN, ImageUri=image, Publish=False)
        _wait_fn(lam, WEB_LAMBDA_FN)
        lam.update_function_configuration(
            FunctionName=WEB_LAMBDA_FN, Role=role_arn, Timeout=30,
            MemorySize=1024, Environment={"Variables": env},
        )
        print(f"updated Lambda {WEB_LAMBDA_FN}")
    except lam.exceptions.ResourceNotFoundException:
        for attempt in range(10):
            try:
                lam.create_function(
                    FunctionName=WEB_LAMBDA_FN, PackageType="Image",
                    Code={"ImageUri": image}, Role=role_arn, Timeout=30,
                    MemorySize=1024, Architectures=["x86_64"],
                    Environment={"Variables": env}, Tags=TAGS,
                )
                break
            except lam.exceptions.InvalidParameterValueException as exc:
                if "cannot be assumed" in str(exc) and attempt < 9:
                    time.sleep(6)
                    continue
                raise
        print(f"created Lambda {WEB_LAMBDA_FN}")
    _wait_fn(lam, WEB_LAMBDA_FN)

    api_id, endpoint = _ensure_http_api(ctx, WEB_LAMBDA_FN, WEB_HTTP_NAME)
    print(f"WEB_URL={endpoint}")


def _wait_fn(lam, fn: str) -> None:
    for _ in range(60):
        cfg = lam.get_function_configuration(FunctionName=fn)
        if cfg.get("LastUpdateStatus") != "InProgress" and cfg.get("State") != "Pending":
            return
        time.sleep(4)


# --------------------------------------------------------------------------- info


def cmd_harden_iam(ctx: Ctx, args) -> None:
    """Least-privilege service IAM (M8 audit P6).

    Narrows the AgentCore execution role's Bedrock invocation to only the configured
    Nova Lite inference profile + its backing foundation models, and retires the
    never-used ``aircheck-m7-deployer`` AdministratorAccess scaffold (DECISIONS D-022).
    """
    model_id = args.model_id or DEFAULT_MODEL_ID
    print("AgentCore Bedrock resources (narrowed) ->")
    for resource in _nova_resources(ctx, model_id):
        print(f"  {resource}")
    _ensure_agentcore_role(ctx, model_id)
    print(f"re-applied least-privilege inline policy on {AGENTCORE_ROLE}")

    iam = ctx.client("iam")
    name = "aircheck-m7-deployer"
    try:
        role = iam.get_role(RoleName=name)["Role"]
        last_used = (role.get("RoleLastUsed") or {}).get("LastUsedDate")
        if last_used:
            print(f"  {name} HAS been used ({last_used}); NOT deleting — review manually")
            return
        for policy in iam.list_attached_role_policies(RoleName=name)["AttachedPolicies"]:
            iam.detach_role_policy(RoleName=name, PolicyArn=policy["PolicyArn"])
            print(f"  detached {policy['PolicyName']}")
        for policy_name in iam.list_role_policies(RoleName=name)["PolicyNames"]:
            iam.delete_role_policy(RoleName=name, PolicyName=policy_name)
        iam.delete_role(RoleName=name)
        print(f"  deleted inert admin scaffold {name} (was never used)")
    except iam.exceptions.NoSuchEntityException:
        print(f"  {name} not present (already retired)")


def cmd_manifest(ctx: Ctx, args) -> None:
    """Emit a machine-readable release manifest pinning the deployed artifacts."""
    lam = ctx.client("lambda")
    ecr = ctx.client("ecr")

    def digest_for(repo: str, tag: str | None) -> str | None:
        if not tag:
            return None
        try:
            imgs = ecr.describe_images(
                repositoryName=repo, imageIds=[{"imageTag": tag}]
            )["imageDetails"]
            return imgs[0]["imageDigest"] if imgs else None
        except ClientError:
            return None

    manifest: dict = {
        "git_sha": args.sha or "unknown",
        "environment": "hackathon",
        "region": ctx.region,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "agentcore_model_id": args.model_id or DEFAULT_MODEL_ID,
        "persistence": {
            "bucket": "aircheck-<ACCT>-state",
            "prefix": getattr(args, "s3_prefix", None) or S3_PREFIX,
            "versioning": "enabled",
        },
        "components": {},
    }
    for label, fn, repo in (
        ("api", LAMBDA_FN, ECR_REPO),
        ("web", WEB_LAMBDA_FN, WEB_ECR_REPO),
    ):
        try:
            fn_resp = lam.get_function(FunctionName=fn)
            image = fn_resp["Code"].get("ImageUri", "")
            tag = image.rsplit(":", 1)[-1] if ":" in image else None
            env = fn_resp["Configuration"].get("Environment", {}).get("Variables", {})
            manifest["components"][label] = {
                "lambda": fn,
                "image_repo": repo,
                "image_tag": tag,
                "image_digest": digest_for(repo, tag),
                "git_sha_env": env.get("AIRCHECK_GIT_SHA"),
                "s3_prefix": env.get("AIRCHECK_S3_PREFIX"),
            }
        except ClientError as exc:
            manifest["components"][label] = {"error": exc.response["Error"]["Code"]}

    try:
        control = ctx.client("bedrock-agentcore-control")
        runtime = _find_runtime(control, AGENTCORE_RUNTIME)
        agent_tag = args.sha or "latest"
        manifest["components"]["agentcore"] = {
            "runtime": AGENTCORE_RUNTIME,
            "status": runtime.get("status") if runtime else None,
            "version": runtime.get("agentRuntimeVersion") if runtime else None,
            "image_repo": AGENT_ECR_REPO,
            "image_tag": agent_tag,
            "image_digest": digest_for(AGENT_ECR_REPO, agent_tag),
            "model_id": args.model_id or DEFAULT_MODEL_ID,
        }
    except ClientError as exc:
        manifest["components"]["agentcore"] = {"error": exc.response["Error"]["Code"]}

    # Clean internal receipt: never leak the account id.
    text = json.dumps(manifest, indent=2, sort_keys=True).replace(ctx.account, "<ACCT>")
    if getattr(args, "out", None):
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote release manifest -> {args.out}")
    print(text)


def cmd_info(ctx: Ctx, args) -> None:
    out = {"account_tail": ctx.account[-4:], "region": ctx.region}
    out["state_bucket"] = ctx.state_bucket
    out["build_bucket"] = ctx.build_bucket
    out["ecr_uri"] = ctx.ecr_uri
    lam = ctx.client("lambda")
    try:
        cfg = lam.get_function_configuration(FunctionName=LAMBDA_FN)
        out["lambda_state"] = cfg.get("State")
        out["lambda_last_update"] = cfg.get("LastUpdateStatus")
        env = cfg.get("Environment", {}).get("Variables", {})
        out["git_sha"] = env.get("AIRCHECK_GIT_SHA")
        out["agentcore"] = bool(env.get("AIRCHECK_AGENTCORE_RUNTIME_ARN"))
        url = lam.get_function_url_config(FunctionName=LAMBDA_FN)["FunctionUrl"]
        out["function_url"] = url
    except ClientError as exc:
        out["lambda"] = f"not-ready ({exc.response['Error']['Code']})"
    print(json.dumps(out, indent=2))


COMMANDS = {
    "ecr": cmd_ecr,
    "buckets": cmd_buckets,
    "iam": cmd_iam,
    "build": cmd_build,
    "lambda": cmd_lambda,
    "alarm": cmd_alarm,
    "observability": cmd_observability,
    "agentcore": cmd_agentcore,
    "wire-agentcore": cmd_wire_agentcore,
    "apigw": cmd_apigw,
    "frontend": cmd_frontend,
    "external-verify": cmd_external_verify,
    "harden-iam": cmd_harden_iam,
    "manifest": cmd_manifest,
    "info": cmd_info,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AIRCheck deployment orchestrator")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "aircheck-bedrock"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--sha", default=None, help="deployed Git SHA (build/lambda)")
    parser.add_argument("--runtime-arn", default=None, help="AgentCore runtime ARN")
    parser.add_argument("--model-id", default=None, help="Bedrock model id")
    parser.add_argument("--cors", default=None, help="CORS origins (comma-separated)")
    parser.add_argument("--api-base", default=None, help="deployed API base URL (frontend)")
    parser.add_argument(
        "--s3-prefix", dest="s3_prefix", default=None,
        help="S3 state prefix (default aircheck; M8 release uses aircheck-m8)",
    )
    parser.add_argument("--gate", default=None, help="AIRCHECK_GATE value reported by /health")
    parser.add_argument("--out", default=None, help="output path (manifest)")
    args = parser.parse_args(argv)
    ctx = Ctx(args)
    COMMANDS[args.command](ctx, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
