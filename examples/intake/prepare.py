"""Prepare a manifest for the bundled example using the provisioned provider."""

import argparse
import json
from pathlib import Path

import rfc8785

from service.provider import Ollama
from service.workflows import BINDING_SET, digest

directory = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output-dir", type=Path, default=directory)
output = parser.parse_args().output_dir
output.mkdir(parents=True, exist_ok=True)
example = json.loads(
    (directory.parents[1] / "contracts" / "examples.json").read_text()
)["workflow_bundle"]
example.update(
    {
        "identity": Ollama().identity,
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
