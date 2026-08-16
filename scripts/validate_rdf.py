from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rdflib import Graph
from pyshacl import validate


def load_graph(
    data_path: Path,
    ontology_path: Path,
    vocab_dir: Path,
) -> Graph:

    graph = Graph()

    print(f"Loading data: {data_path}")

    graph.parse(
        data_path,
        format="nt",
    )

    print(
        f"  {len(graph):,} triples after instance data"
    )

    print(f"Loading ontology: {ontology_path}")

    graph.parse(
        ontology_path,
        format="turtle",
    )

    print(
        f"  {len(graph):,} triples after ontology"
    )

    vocabulary_files = sorted(
        vocab_dir.glob("*.ttl")
    )

    if not vocabulary_files:
        raise RuntimeError(
            f"No generated vocabularies found in {vocab_dir}"
        )

    print(
        f"Loading {len(vocabulary_files)} generated vocabularies..."
    )

    for path in vocabulary_files:
        graph.parse(
            path,
            format="turtle",
        )

    print(
        f"  {len(graph):,} triples after vocabularies"
    )

    return graph


def parse_args() -> argparse.Namespace:

    project_root = (
        Path(__file__)
        .resolve()
        .parents[1]
    )

    parser = argparse.ArgumentParser(
        description=(
            "Validate the generated BSS RDF graph "
            "against SHACL shapes."
        )
    )

    parser.add_argument(
        "--data",
        type=Path,
        default=(
            project_root
            / "data/processed/bss-loiret.nt"
        ),
    )

    parser.add_argument(
        "--ontology",
        type=Path,
        default=(
            project_root
            / "ontology/bss-poc.ttl"
        ),
    )

    parser.add_argument(
        "--vocab",
        type=Path,
        default=(
            project_root
            / "vocab/generated"
        ),
    )

    parser.add_argument(
        "--shapes",
        type=Path,
        default=(
            project_root
            / "shapes/bss-shapes.ttl"
        ),
    )

    parser.add_argument(
        "--report",
        type=Path,
        default=(
            project_root
            / "data/processed/shacl-report.ttl"
        ),
    )

    parser.add_argument(
        "--text-report",
        type=Path,
        default=(
            project_root
            / "data/processed/shacl-report.txt"
        ),
    )

    return parser.parse_args()


def main() -> None:

    args = parse_args()

    data_graph = load_graph(
        data_path=args.data,
        ontology_path=args.ontology,
        vocab_dir=args.vocab,
    )

    shapes_graph = Graph()

    print(f"Loading shapes: {args.shapes}")

    shapes_graph.parse(
        args.shapes,
        format="turtle",
    )

    print(
        f"  {len(shapes_graph):,} SHACL triples"
    )

    print()
    print("Running SHACL validation...")

    conforms, results_graph, results_text = validate(
        data_graph=data_graph,
        shacl_graph=shapes_graph,

        # Our converter emits the types needed for validation explicitly.
        inference="none",

        abort_on_first=False,

        # First validate the SHACL graph itself.
        meta_shacl=True,

        # We only use SHACL Core in v1.
        advanced=False,

        debug=False,
    )

    args.report.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if hasattr(results_graph, "serialize"):
        results_graph.serialize(
            destination=args.report,
            format="turtle",
            encoding="utf-8",
        )

    args.text_report.write_text(
        str(results_text),
        encoding="utf-8",
    )

    print()
    print("=" * 60)

    if conforms:
        print("SHACL RESULT: CONFORMS ✓")
    else:
        print("SHACL RESULT: DOES NOT CONFORM ✗")

    print("=" * 60)

    print()
    print(f"Data triples:  {len(data_graph):,}")
    print(f"Report TTL:    {args.report}")
    print(f"Report text:   {args.text_report}")

    if not conforms:
        print()
        print(results_text)

        sys.exit(1)


if __name__ == "__main__":
    main()