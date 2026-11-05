# Multi-tenant file sharing bot

This service runs one Telegram bot per tenant. A web control plane manages
tenants, while horizontally scalable workers claim tenant leases from MongoDB.

## First deployment

1. Copy `.env.example` to `.env` and set `API_ID`, `API_HASH`,
   `PLATFORM_ADMIN_PASSWORD`, and a generated Fernet `TENANT_MASTER_KEY`.
2. Inspect existing database contents without deleting anything:

   ```sh
   docker compose run --rm web python scripts/reset_schema.py --dry-run
   ```

3. Existing single-tenant data is intentionally not migrated. To reset the
   configured database, explicitly provide both the execution flag and exact
   database name:

   ```sh
   docker compose run --rm web python scripts/reset_schema.py \
     --execute --confirm filesharing
   ```

4. For local development, start the control plane and an initial worker pool:

   ```sh
   docker compose up -d --build --scale worker=10
   ```

5. Run the read-only production preflight after initialization:

   ```sh
   docker compose run --rm web python scripts/preflight.py
   ```

6. Open `/admin`, log in as the platform administrator, create tenants in a
   disabled state, and enable each tenant after its Bot Token and database
   channel validation succeeds.

## Production deployment

Production must use `compose.production.yml`, an immutable image tag, and an
external replica-set or sharded MongoDB. The production Compose file does not
start the local unauthenticated MongoDB service.

```sh
export IMAGE=ghcr.io/owner/repository:sha-abcdef0
docker compose -f compose.production.yml config --quiet
docker compose -f compose.production.yml run --rm web \
  python scripts/reset_schema.py --dry-run
# Only after verifying the target shown above; replace this example with the
# exact DATABASE_NAME. This permanently removes the old database contents.
docker compose -f compose.production.yml run --rm web \
  python scripts/reset_schema.py --execute --confirm filesharing_production
docker compose -f compose.production.yml up -d --scale worker=10
docker compose -f compose.production.yml ps
docker compose -f compose.production.yml run --rm web \
  python scripts/preflight.py --require-ha --require-workers
```

Do not deploy the mutable `latest` tag. Keep `DATABASE_URL` only in `.env`, and
set `WEB_PORT` when the reverse proxy should reach a port other than `7860`.
`TMPFS_SIZE` controls temporary backup space and defaults to `256m`.
The final preflight must pass only after workers have acquired every enabled
tenant. If no tenants are enabled yet, `--require-workers` permits zero leases.

The default capacity is 10 bots and 100 concurrent deliveries per worker.
Increase or decrease worker replicas based on queue depth and Telegram
`FloodWait` frequency. Production MongoDB must be a replica set or managed
high-availability service; the Compose Mongo service is for local operation.

## Process roles

- `PROCESS_ROLE=web`: web UI, authentication, tenant management, and job creation.
- `PROCESS_ROLE=worker`: leased Telegram bot runtimes and bot-job execution.
- `PROCESS_ROLE=all`: local development only; runs both roles in one process.

Workers renew leases every 10 seconds. A lease expires after 30 seconds and a
replacement worker should restore the tenant bot within 60 seconds. A worker
that cannot renew before its safety window stops its bots to avoid split brain.

## Operational checks

- `/health/live`: control-plane process liveness.
- `/health/ready`: MongoDB-backed readiness.
- Platform tenant list: worker ownership, heartbeat, runtime status, and errors.
- Platform audit page: tenant lifecycle, impersonation, and tenant mutations.

Do not log or export `.env`, `TENANT_MASTER_KEY`, Bot Tokens, password hashes, or
MongoDB session documents. Tenant backups intentionally exclude credentials.

## Troubleshooting

- **Startup reports legacy or unsupported schema:** run the reset script in
  dry-run mode first. Only use `--execute --confirm <database>` after confirming
  the target database and accepting that old links will stop working.
- **A tenant remains stopped/error:** inspect its runtime error and heartbeat in
  the platform tenant list, verify the Bot is an administrator of the configured
  database channel, then re-save the Token/channel or disable and re-enable it.
- **A worker disappears:** wait up to 60 seconds for lease expiry and takeover.
  If recovery does not occur, check MongoDB availability and ensure enough
  workers are running for `ceil(enabled tenants / WORKER_BOT_CAPACITY)`.
- **Broadcast/forward is queued:** confirm that the tenant Bot is running and
  inspect the returned job ID. Expired claims are retried up to the configured
  attempt limit; terminal task records are retained for seven days.
- **Frequent `FloodWait` or delivery queue rejections:** reduce request rate or
  add workers. Do not raise concurrency limits before checking Telegram limits,
  MongoDB latency, and process memory.

## Tests

```sh
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest -q
```

Mongo integration tests run when `RUN_MONGO_TESTS=1` and `DATABASE_URL` points
to an isolated test database.

After initializing an isolated database, the non-container process smoke test
can also verify that both roles start and Web readiness succeeds:

```sh
python scripts/process_smoke.py
```
