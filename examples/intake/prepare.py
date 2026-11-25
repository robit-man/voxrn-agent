"""Prepare a manifest for the bundled example using the provisioned provider."""

import argparse
import json
from pathlib import Path

import rfc8785

from service.provider_registry import load_providers
from service.workflows import BINDING_SET, digest

directory = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output-dir", type=Path, default=directory)
parser.add_argument("--model", default="qwen-local")
options = parser.parse_args()
provider = load_providers().get(options.model)
if provider is None or not provider.features["structured_outputs"]:
    parser.error(
        "--model must name a configured model with structured workflow support."
    )
output = options.output_dir
output.mkdir(parents=True, exist_ok=True)
example = json.loads(
    (directory.parents[1] / "contracts" / "examples.json").read_text()
)["workflow_bundle"]
example.update(
    {
        "identity": provider.identity,
        "entrypoint": "record_intake",
        "source_sha256": digest((directory / "source.txt").read_bytes()),
        "executable_sha256": digest((directory / "instructions.txt").read_bytes()),
        "binding_set_sha256": digest(rfc8785.dumps(BINDING_SET)),
    }
)
for filename in ("source.txt", "instructions.txt"):
    if output.resolve() != directory:
        (output / filename).write_bytes((directory / filename).read_bytes())
(output / "manifest.json").write_text(json.dumps(example, indent=2) + "\n")
