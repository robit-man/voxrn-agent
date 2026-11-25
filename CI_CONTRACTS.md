# Contract and runtime CI

The GitHub workflow runs against pull requests targeting `main` and pushes to `main`. It uses clean Python 3.11 and 3.12 environments, the service's pinned dependencies, and isolated fixtures. ASR also uses FFmpeg for runtime tests. The workflow checks:

- Official OpenAPI and applicable AsyncAPI document schemas. The checker verifies the source bytes against a pinned SHA-256 before parsing. It verifies local references, OpenAPI schema objects and streaming item schemas, MCP tool input and output schemas, declared scopes, and the checked-in example payloads.
- Actual FastAPI REST and WebSocket route registrations, declared explicit response statuses, and the mounted MCP route. Health, capabilities and public operations come from OpenAPI. The fixed protocol discovery routes and docs mount are tracked separately. Runtime tests exercise the HTTP and MCP surfaces.
- Generated Pages and any checked-in clients. Once `scripts/generate_clients.py` or `clients/` is present, client regeneration and the TypeScript compile check are required.
- Compatibility against the prior `main` update or the pull request base. The workflow uses the checked-out merge result for pull requests so route and example checks see the candidate as it would merge.

## Local gates

Install the dependencies in a virtual environment and run the gates from the service root. The first command supports an optional `--schema-cache DIRECTORY` containing the pinned `openapi-3.2.json` and, for ASR, `asyncapi-3.1.json`. The checker verifies cached bytes by the same hashes. Its pinned official sources are listed in [the checker](scripts/check_contracts.py).

```sh
python -m pip install -r requirements.txt 'jsonschema[format-nongpl]==4.26.0'
python -B scripts/check_contracts.py --base "$(git merge-base HEAD origin/main)"
python -B -m unittest discover -s tests -v
```

For a checkout with generated clients, use Node 24.14.0 and TypeScript 5.9.3 to run `tsc -p clients/typescript/tsconfig.json`. The contract checker verifies generated client freshness with `python -B scripts/generate_clients.py --check`.

## Compatibility review

The gate reports removed paths, operations, media types, responses, headers, parameters, fields, scopes and MCP tools. It detects newly required request fields, weakened response requiredness, schema type and constraint changes, documented protocol and operation identity changes, and AsyncAPI channel or message changes. Additive response properties are allowed. The comparator checks known wire surfaces and JSON Schema keywords. Changes to keyword combinations, authorization logic or runtime behavior need review and runtime tests alongside the report.

When a breaking change is approved, capture the exact full base SHA and findings fingerprint emitted by the gate in `.github/contract-compatibility.json`. Add a migration reference that explains the old and new behavior, the affected clients and how to transition. The approval applies to that base and that exact set of findings.

```json
{
  "approvals": [
    {
      "base": "<full base commit SHA>",
      "fingerprint": "<findings SHA-256>",
      "migration": "docs/migrations/example.md"
    }
  ]
}
```

The tests use synthetic or mocked providers. Benchmark, accelerator and distributed qualification use their own evidence under [the operational specification](QUALITY_SPEC.md).
