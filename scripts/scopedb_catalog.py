#!/usr/bin/env python3
# Copyright 2026 Yunze Xu
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request


def load_env_file(path):
    values = {}
    try:
        env_file = open(path, encoding="utf-8")
    except OSError as error:
        raise SystemExit(f"cannot read {path}: {error}") from error
    with env_file:
        for line_number, line in enumerate(env_file, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].lstrip()
            key, separator, value = line.partition("=")
            key = key.strip()
            if separator != "=" or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise SystemExit(f"{path}:{line_number}: expected KEY=VALUE")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[key] = value
    return values


def credentials(env_file):
    values = load_env_file(env_file)
    endpoint = (
        os.getenv("TELESCOPE_SCOPEDB_ENDPOINT")
        or os.getenv("SCOPEDB_ENDPOINT")
        or values.get("TELESCOPE_SCOPEDB_ENDPOINT")
        or values.get("SCOPEDB_ENDPOINT")
    )
    api_key = (
        os.getenv("TELESCOPE_SCOPEDB_API_KEY")
        or os.getenv("SCOPEDB_API_KEY")
        or values.get("TELESCOPE_SCOPEDB_API_KEY")
        or values.get("SCOPEDB_API_KEY")
    )
    if not endpoint:
        raise SystemExit("set TELESCOPE_SCOPEDB_ENDPOINT or SCOPEDB_ENDPOINT")
    if not api_key:
        raise SystemExit("set TELESCOPE_SCOPEDB_API_KEY or SCOPEDB_API_KEY")
    return endpoint.rstrip("/"), api_key


def read_json_file(path):
    try:
        with open(path, encoding="utf-8") as json_file:
            value = json.load(json_file)
    except OSError as error:
        raise SystemExit(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise SystemExit(f"invalid JSON in {path}: {error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: catalog definition must be a JSON object")
    return value


def write_json_file(path, value):
    directory = os.path.dirname(os.path.abspath(path))
    temporary, temporary_path = tempfile.mkstemp(prefix=".catalog-plan-", dir=directory)
    try:
        with os.fdopen(temporary, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2, sort_keys=True)
            output.write("\n")
        os.replace(temporary_path, path)
    except BaseException:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        raise


def catalog_request(method, endpoint, resource, api_key, payload=None, params=None, etag=None, timeout=30):
    if not resource.startswith("/v1/catalog"):
        raise SystemExit("--resource must start with /v1/catalog")
    query = urllib.parse.urlencode(params or {})
    url = endpoint + resource + (("?" + query) if query else "")
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Accept", "application/json")
    request.add_header("Authorization", "Bearer " + api_key)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if etag:
        request.add_header("If-Match", etag)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
            response_etag = response.headers.get("ETag", "")
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", "replace").strip()
        raise SystemExit(f"catalog request failed: HTTP {error.code}: {details}") from error
    except urllib.error.URLError as error:
        raise SystemExit(f"catalog request failed: {error.reason}") from error

    try:
        result = json.loads(data) if data else None
    except json.JSONDecodeError as error:
        raise SystemExit("catalog API returned invalid JSON") from error
    return result, response_etag


def print_json(value):
    json.dump(value, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def command_plan(args):
    endpoint, api_key = credentials(args.env_file)
    definition = read_json_file(args.file)
    result, etag = catalog_request(
        "PUT",
        endpoint,
        args.resource,
        api_key,
        payload=definition,
        params={"dry_run": "true"},
        timeout=args.timeout,
    )
    plan = {
        "version": 1,
        "endpoint": endpoint,
        "resource": args.resource,
        "definition": definition,
        "etag": etag,
        "result": result,
    }
    write_json_file(args.out, plan)
    print_json(plan)


def command_apply(args):
    endpoint, api_key = credentials(args.env_file)
    plan = read_json_file(args.plan)
    if plan.get("version") != 1 or not isinstance(plan.get("resource"), str) or not isinstance(plan.get("definition"), dict) or not isinstance(plan.get("etag"), str) or not plan["etag"]:
        raise SystemExit(f"{args.plan}: invalid catalog plan; regenerate it with the plan command")
    if plan.get("endpoint") != endpoint:
        raise SystemExit("catalog plan targets a different ScopeDB endpoint; regenerate the plan")
    result = plan.get("result")
    if not isinstance(result, dict):
        raise SystemExit(f"{args.plan}: invalid catalog plan result; regenerate it with the plan command")
    if result.get("destructive") and not args.allow_destructive:
        raise SystemExit("catalog plan is destructive; regenerate/review it and pass --allow-destructive")
    params = {"allow_destructive": "true"} if args.allow_destructive else None
    response, _ = catalog_request(
        "PUT",
        endpoint,
        plan["resource"],
        api_key,
        payload=plan["definition"],
        params=params,
        etag=plan["etag"],
        timeout=args.timeout,
    )
    print_json(response)


def command_get(args):
    endpoint, api_key = credentials(args.env_file)
    result, _ = catalog_request("GET", endpoint, args.resource, api_key, timeout=args.timeout)
    print_json(result)


def main():
    parser = argparse.ArgumentParser(description="Preview and apply ScopeDB Catalog API resources")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(command_parser):
        command_parser.add_argument("--env-file", default=".env")
        command_parser.add_argument("--timeout", type=float, default=30)

    plan_parser = subparsers.add_parser("plan", help="dry-run a catalog resource apply and save a plan")
    add_common(plan_parser)
    plan_parser.add_argument("--resource", required=True, help="catalog API path such as /v1/catalog/databases/scopedb/schemas/devin_cli")
    plan_parser.add_argument("--file", required=True, help="JSON catalog resource definition")
    plan_parser.add_argument("--out", required=True, help="write the reusable plan JSON")
    plan_parser.set_defaults(run=command_plan)

    apply_parser = subparsers.add_parser("apply", help="apply a saved catalog plan")
    add_common(apply_parser)
    apply_parser.add_argument("--allow-destructive", action="store_true")
    apply_parser.add_argument("plan", help="plan JSON produced by the plan command")
    apply_parser.set_defaults(run=command_apply)

    get_parser = subparsers.add_parser("get", help="read a catalog resource")
    add_common(get_parser)
    get_parser.add_argument("--resource", required=True)
    get_parser.set_defaults(run=command_get)

    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
