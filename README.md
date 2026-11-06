# devin-metrics

把 Devin CLI 的 OpenTelemetry metrics 通过 OTLP/HTTP 发送到本机 Telescope，再由 Telescope 批量写入 ScopeDB Cloud。

```text
Devin CLI --OTLP/HTTP protobuf--> Telescope :4318 --ScopeDB append--> scopedb.devin_cli.metrics
```

本项目只启用 `metrics` signal；Devin CLI 的 logs/traces 不会进入这条链路。Telescope 使用 `ghcr.io/scopedb/telescope:v0.4.0`，配置见 `telescope.yaml`。

## 前置条件

- Docker 与 Docker Compose
- ScopeDB Cloud workspace 的 data-plane endpoint 和 API key
- Devin CLI 支持 OTLP metrics export

获取 endpoint 和 key：

```bash
scope workspace get
scope api-key create devin-metrics --expires-in 720h
```

`scope api-key create` 只显示一次 key。也可以在 ScopeDB Console 的 workspace API Keys 页面创建。

## 启动

```bash
cp .env.example .env
# 编辑 .env，填写 TELESCOPE_SCOPEDB_ENDPOINT 和 TELESCOPE_SCOPEDB_API_KEY
make config-check
make plan       # 生成 tables.scopeql
# 在 ScopeDB Console SQL editor 或 ScopeQL CLI 中 review 并执行 tables.scopeql
make validate   # 检查 live catalog 是否匹配 mapping
make up
make status
make verify     # 发送 synthetic metrics 并等待 ScopeDB append ack
```

`tables.scopeql` 只包含 Telescope 规划出的 `CREATE DATABASE` / `CREATE SCHEMA` / `CREATE TABLE` / `ALTER TABLE ADD COLUMN`，不会自动执行。首次部署必须先应用它。

Telescope OTLP/HTTP endpoint 是 `http://localhost:4318`；Devin CLI 会请求 `/v1/metrics`。gRPC 端口 4317 在容器内可用，但 Compose 默认不发布。

## 配置 Devin CLI

把 `devin/config.json.example` 中的 `otel` block 合并到用户配置 `~/.config/devin/config.json`（Windows: `%APPDATA%\devin\config.json`），不要覆盖已有配置：

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

如果 Telescope 部署在另一台机器，把 `metrics_endpoint` 改成该机器的 `http://<host>:4318/v1/metrics`，并把 `.env` 中的 `TELESCOPE_OTLP_HTTP_BIND` 设为 `0.0.0.0` 或部署到可信内网/TLS proxy 后面。默认只绑定 loopback。

Devin CLI 默认每 60 秒 export metrics；示例配置改成 10 秒便于验证。结束 session 时也会 flush。运行 `devin doctor` 可确认 `otel export` 已启用。

## 查询数据

表结构由 `telescope.yaml` 的 mapping 决定，目标表是 `scopedb.devin_cli.metrics`。常用列：

- 身份/上下文：`service_name`、`session_id`、`user_id`、`terminal_type`、`team_id`、`deployment_environment`
- 指标定义：`metric_name`、`metric_description`、`metric_unit`、`metric_type`、`metric_temporality`、`metric_is_monotonic`
- 数值：`value`、`value_type`、`int_value`、`double_value`、`distribution`
- 维度：`attribute_type`、`model`、`start_type`、`tool_name`、`decision`、`decision_source`
- 原始上下文：`datapoint_attributes`、`resource_attributes`、`scope_name`

示例：

```bash
make query Q="FROM scopedb.devin_cli.metrics WHERE metric_name = 'devin.token.usage' SELECT recorded_at, model, attribute_type, value LIMIT 20"
make query Q="FROM scopedb.devin_cli.metrics WHERE metric_name = 'devin.session.count' SELECT recorded_at, user_id, session_id, value LIMIT 20"
```

Devin CLI counters 使用 OTLP delta temporality；`metric_temporality` 会写入表中，查询/聚合时按 delta 语义处理。

## 运维

```bash
make logs       # Telescope 容器日志
make status     # receiver / queue / ScopeDB delivery counters
make capture    # capture 最近 100 个 metrics data points，用于调试 mapping
curl -s http://127.0.0.1:8080/v1/ingestion/status
curl -s http://127.0.0.1:8080/metrics
```

`status` 中 `received` 表示 OTLP receiver 收到的数据点，`written` 表示 ScopeDB append 已确认，`queued`/`dropped`/`ambiguous` 用于排查投递问题。

文件队列保存在 Docker named volume `telescope-queue`。ScopeDB 不可用时数据会留在队列中并重试；不要在 queue 未排空时直接删除 volume。

## 本地验证 mapping

不需要 ScopeDB 凭据：

```bash
make preview    # 用 samples/devin-metrics.otlp.json 预览将写入的行
make inspect    # 查看样例中实际出现的 OTLP selectors
```

真实流量调试：

```bash
devin   # 在另一个终端产生 metrics
make capture > captured.otlp.json
# 将 captured.otlp.json 放到 samples/ 后用 telescope preview/inspect 检查
```

## 安全说明

- `.env` 包含 ScopeDB API key，已被 `.gitignore` 忽略；不要提交真实 key。
- Telescope OTLP receiver 默认是明文 HTTP。仅用于可信网络；远程部署应放在 TLS/authenticated proxy 后面。
- operational endpoint `/healthz`、`/readyz`、`/v1/ingestion/status`、`/metrics` 没有认证，Compose 默认只绑定 `127.0.0.1:8080`。
- Devin CLI metrics 可能包含 `session.id`、`user.id`、model、tool name 等元数据；不要把高基数值或敏感值放进 `resource_attributes`。
