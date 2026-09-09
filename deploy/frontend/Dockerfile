# AIRCheck web (Vinext) — AWS Lambda container image serving the frozen five-surface
# UI via `vinext start` (the Node production server) behind a public Function-URL/
# API Gateway using the AWS Lambda Web Adapter. Built in the cloud (CodeBuild); no
# local Docker. The UI is unchanged; the only deployment change is that layout.tsx
# injects the production API base from AIRCHECK_API_BASE so the browser client
# targets the deployed API. All run data is fetched client-side from that API.
FROM public.ecr.aws/docker/library/node:22-slim AS build
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
# `npm install` (not `npm ci`): the repo lock file is generated on Windows and
# omits Linux-only optional native deps (@emnapi/*), which `npm ci` rejects.
# `npm install` resolves the platform deps for the Linux build image.
RUN npm install --no-audit --no-fund --loglevel=error
COPY apps/web/ ./
RUN node node_modules/vinext/dist/cli.js build

FROM public.ecr.aws/docker/library/node:22-slim
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.0 /lambda-adapter /opt/extensions/lambda-adapter
ENV NODE_ENV=production \
    PORT=3000 \
    AWS_LWA_PORT=3000 \
    AWS_LWA_INVOKE_MODE=buffered \
    AWS_LWA_READINESS_CHECK_PATH=/
WORKDIR /app
COPY --from=build /app ./
# vinext start serves dist/server (SSR shells) + dist/client (assets) on port 3000.
CMD ["node", "node_modules/vinext/dist/cli.js", "start", "-p", "3000"]
