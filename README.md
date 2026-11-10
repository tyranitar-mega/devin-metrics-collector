# devin-metrics

Send Devin CLI OpenTelemetry metrics to a local Telescope OTLP receiver, which batches and writes them to ScopeDB Cloud.

```text
Devin CLI --OTLP/HTTP protobuf--> Telescope :4318 --ScopeDB append--> scopedb.devin_cli.metrics
```

Only the `metrics` signal is enabled. Devin CLI logs and traces are not part of this pipeline. Telescope uses `ghcr.io/scopedb/telescope:v0.4.0`; see `telescope.yaml` for the mapping.

## Prerequisites

- Docker and Docker Compose
- Python 3 for the Catalog API helper
- A ScopeDB Cloud workspace data-plane endpoint and API key
- A Devin CLI version with OTLP metrics export

Get the endpoint and key with:

```bash
scope workspace get
scope api-key create devin-metrics --expires-in 720h
```

`scope api-key create` shows the key only once. You can also create a key in the workspace's API Keys page in the ScopeDB Console.

## Prepare the ScopeDB resources

Telescope does not create catalog resources automatically. This project creates the `devin_cli` schema and its `metrics` table through the ScopeDB Catalog API instead of ScopeQL `CREATE TABLE`/`CREATE SCHEMA` statements.

```bash
cp .env.example .env
# Edit .env and set TELESCOPE_SCOPEDB_ENDPOINT and TELESCOPE_SCOPEDB_API_KEY
make config-check
make plan
```

`make plan` sends the definition in `catalog/devin-cli.schema.json` to `PUT /v1/catalog/databases/scopedb/schemas/devin_cli?dry_run=true`, then writes the reusable preview to `catalog-plan.json`. The plan records the catalog ETag, and `catalog-apply` sends it back with `If-Match`; regenerate the plan after any catalog change or successful apply. Review the `before`, `after`, and `destructive` fields, then commit the same definition with:

```bash
make catalog-apply
```

Catalog resources are complete replacements: the schema definition owns every table and view in `devin_cli`. If the preview reports `destructive: true`, inspect it carefully and rerun with `ALLOW_DESTRUCTIVE=1 make catalog-apply` only when data loss is intended. `catalog-plan.json` does not contain the API key and is ignored by git.

After the table exists, validate that the live catalog matches the Telescope mapping:

```bash
make validate
```

## Load model pricing

The same catalog definition includes `scopedb.devin_cli.model_pricing`, which holds the per-model token rates used by `queries/model-usage-last-7-days.scopeql`. The rates live in `pricing/model-pricing.scopeql`; load or refresh them with scopedb-cli:

```bash
scope query -f pricing/model-pricing.scopeql
```

The table deduplicates on `model` by `updated_at DESC`, so each run retains the newest row for every model instead of accumulating duplicate rates.

## Start the receiver

```bash
make up
make status
make verify
```

`make verify` sends a synthetic metrics probe through OTLP and waits for the ScopeDB append acknowledgement. A successful run ends with a message like:

```text
metrics: ScopeDB append succeeded for synthetic probe (...)
```

The Telescope OTLP/HTTP endpoint is `http://localhost:4318`; Devin CLI posts metrics to `/v1/metrics`. The gRPC port 4317 is available inside the container but is not published by Compose by default.

## Configure Devin CLI

Merge the `otel` block from `devin/config.json.example` into the user configuration at `~/.config/devin/config.json` (Windows: `%APPDATA%\devin\config.json`). Do not replace the whole file:

```json
{
  "otel": {
    "metrics_endpoint": "http://localhost:4318/v1/metrics",
    "metric_export_interval_ms": 10000,
    "resource_attributes": {
      "team.id": "devin-metrics",
      "deployment.environment": "local"
    }
  }
}
```

If Telescope runs on another host, change `metrics_endpoint` to `http://<host>:4318/v1/metrics` and set `TELESCOPE_OTLP_HTTP_BIND=0.0.0.0` in `.env`, or place Telescope behind a trusted-network TLS/authenticated proxy. The default bind is loopback only.

Devin CLI exports metrics every 60 seconds by default; the example uses 10 seconds for easier verification. Pending metrics are also flushed when a session ends. Run `devin doctor` to confirm that metrics export is enabled.

## Where metrics are recorded

Each OTLP metric data point becomes one row in `scopedb.devin_cli.metrics`. The table columns are defined by `telescope.yaml` and include:

- Identity/context: `service_name`, `session_id`, `user_id`, `terminal_type`, `team_id`, `deployment_environment`
- Metric definition: `metric_name`, `metric_description`, `metric_unit`, `metric_type`, `metric_temporality`, `metric_is_monotonic`
- Values: `value`, `value_type`, `int_value`, `double_value`, `distribution`
- Dimensions: `attribute_type`, `model`, `start_type`, `tool_name`, `decision`, `decision_source`
- Raw context: `datapoint_attributes`, `resource_attributes`, `scope_name`

Examples:

```bash
make query Q="FROM scopedb.devin_cli.metrics WHERE metric_name = 'devin.token.usage' SELECT recorded_at, model, attribute_type, value LIMIT 20"
make query Q="FROM scopedb.devin_cli.metrics WHERE metric_name = 'devin.session.count' SELECT recorded_at, user_id, session_id, value LIMIT 20"
```

Devin CLI counters use OTLP delta temporality. `metric_temporality` is stored in the table; interpret sums and aggregations with delta semantics.

## End-to-end delivery flow

1. `make plan` and `make catalog-apply` create or update `scopedb.devin_cli` through the ScopeDB Catalog API.
2. Devin CLI exports OTLP/HTTP protobuf metrics to `http://localhost:4318/v1/metrics`.
3. Telescope receives the metrics through its OTLP receiver.
4. Telescope maps each data point into a row for `scopedb.devin_cli.metrics`.
5. Telescope batches rows and appends them to the ScopeDB workspace configured by `TELESCOPE_SCOPEDB_ENDPOINT` and `TELESCOPE_SCOPEDB_API_KEY`.
6. `make status` shows `received`, `written`, `queued`, `dropped`, and `ambiguous` counters; `make verify` confirms a real ScopeDB append.

If ScopeDB is unavailable or the table is not ready yet, Telescope keeps accepted metrics in a disk-backed file queue and retries delivery. The queue is stored in the Docker named volume `telescope-queue` (`/var/lib/telescope/queue` in the container). Do not delete the volume while it contains undelivered data.

## Operations

```bash
make logs       # Follow Telescope container logs
make status     # Receiver, queue, and ScopeDB delivery counters
make capture    # Capture the latest 100 metric data points for mapping debug
make catalog-get # Read the current devin_cli schema through the Catalog API
curl -s http://127.0.0.1:8080/v1/ingestion/status
curl -s http://127.0.0.1:8080/metrics
```

In `status`, `received` counts data points accepted by the OTLP receiver, `written` counts rows confirmed by ScopeDB append acknowledgements, and `queued`/`dropped`/`ambiguous` help diagnose delivery problems.

## Validate the mapping locally

No ScopeDB credentials are required:

```bash
make preview    # Preview the rows produced from samples/devin-metrics.otlp.json
make inspect    # List the OTLP selectors present in the sample
```

For live traffic:

```bash
devin   # Generate metrics in another terminal
make capture > captured.otlp.json
# Move captured.otlp.json into samples/ and inspect/preview it with Telescope
```

## Security notes

- `.env` contains the ScopeDB API key and is ignored by git; never commit the real key.
- The Telescope OTLP receiver uses plaintext HTTP. Use it only on trusted networks; for remote deployments, put it behind a TLS/authenticated proxy.
- The operational endpoints `/healthz`, `/readyz`, `/v1/ingestion/status`, and `/metrics` have no authentication. Compose binds them to `127.0.0.1:8080` by default.
- Devin CLI metrics can include metadata such as `session.id`, `user.id`, model names, and tool names. Do not put high-cardinality or sensitive values in `resource_attributes`.

## License

Apache License 2.0. See [LICENSE](LICENSE).
