from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import requests

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from pyproj import Transformer

from pathlib import Path

from fastapi.staticfiles import StaticFiles


FUSEKI_SPARQL_URL = os.getenv(
    "FUSEKI_SPARQL_URL",
    "http://localhost:3030/bss/sparql",
)

PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "http://localhost:8000",
).rstrip("/")

COLLECTION_ID = "bss-ouvrages"

MAX_LIMIT = 1000


# ---------------------------------------------------------------------------
# Coordinate transformation
# ---------------------------------------------------------------------------

# Source:
# Lambert-93 / EPSG:2154
#
# API representation:
# OGC CRS84 = WGS84 longitude / latitude
TRANSFORMER = Transformer.from_crs(
    "EPSG:2154",
    "OGC:CRS84",
    always_xy=True,
)


# ---------------------------------------------------------------------------
# In-memory feature cache
#
# The POC dataset is static and only 21,700 features, so loading the semantic
# feature view once from Fuseki keeps bbox requests simple and fast.
#
# Fuseki remains the source of truth.
# ---------------------------------------------------------------------------

FEATURES: list[dict[str, Any]] = []
FEATURES_BY_ID: dict[str, dict[str, Any]] = {}


FEATURE_QUERY = """
PREFIX bsspoc: <https://fair.scribis.fr/ontology/bss#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX qudt: <http://qudt.org/schema/qudt/>

SELECT
    ?identifier
    ?label
    ?wkt
    ?natureCode
    ?communeCode
    ?communeName
    ?groundwaterPoint
    ?depth
WHERE {
    ?ouvrage
        a bsspoc:UndergroundWork ;
        dcterms:identifier ?identifier .

    OPTIONAL {
        ?ouvrage rdfs:label ?label .
    }

    OPTIONAL {
        ?ouvrage geo:hasGeometry ?geometry .
        ?geometry geo:asWKT ?wkt .
    }

    OPTIONAL {
        ?ouvrage bsspoc:hasNature ?nature .
        ?nature skos:notation ?natureCode .
    }

    OPTIONAL {
        ?ouvrage bsspoc:locatedInCommune ?commune .
        ?commune dcterms:identifier ?communeCode .

        OPTIONAL {
            ?commune rdfs:label ?communeName .
        }
    }

    OPTIONAL {
        ?ouvrage
            bsspoc:isGroundwaterPoint
            ?groundwaterPoint .
    }

    OPTIONAL {
        ?ouvrage
            bsspoc:investigationDepth
            ?depthQuantity .

        ?depthQuantity qudt:value ?depth .
    }
}
ORDER BY ?identifier
"""


POINT_RE = re.compile(
    r"POINT\s*\(\s*"
    r"([-+]?\d+(?:\.\d+)?)\s+"
    r"([-+]?\d+(?:\.\d+)?)"
    r"\s*\)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Fuseki helpers
# ---------------------------------------------------------------------------

def run_sparql(query: str) -> dict[str, Any]:
    response = requests.get(
        FUSEKI_SPARQL_URL,
        params={"query": query},
        headers={
            "Accept": "application/sparql-results+json",
        },
        timeout=60,
    )

    response.raise_for_status()

    return response.json()


def binding_value(
    row: dict[str, Any],
    variable: str,
) -> str | None:
    value = row.get(variable)

    if not value:
        return None

    return value.get("value")


# ---------------------------------------------------------------------------
# Geometry conversion
# ---------------------------------------------------------------------------

def parse_wkt_point(
    value: str | None,
) -> tuple[float, float] | None:

    if not value:
        return None

    match = POINT_RE.search(value)

    if not match:
        return None

    x = float(match.group(1))
    y = float(match.group(2))

    return x, y


def to_crs84(
    point: tuple[float, float] | None,
) -> dict[str, Any] | None:

    if point is None:
        return None

    x, y = point

    lon, lat = TRANSFORMER.transform(
        x,
        y,
    )

    return {
        "type": "Point",
        "coordinates": [
            lon,
            lat,
        ],
    }


# ---------------------------------------------------------------------------
# Feature conversion
# ---------------------------------------------------------------------------

def row_to_feature(
    row: dict[str, Any],
) -> dict[str, Any]:

    identifier = binding_value(
        row,
        "identifier",
    )

    if identifier is None:
        raise ValueError(
            "SPARQL result without BSS identifier"
        )

    source_point = parse_wkt_point(
        binding_value(
            row,
            "wkt",
        )
    )

    geometry = to_crs84(source_point)

    groundwater_raw = binding_value(
        row,
        "groundwaterPoint",
    )

    groundwater_point = None

    if groundwater_raw is not None:
        groundwater_point = (
            groundwater_raw.lower() == "true"
        )

    depth_raw = binding_value(
        row,
        "depth",
    )

    depth = (
        float(depth_raw)
        if depth_raw is not None
        else None
    )

    properties = {
        "identifier": identifier,
        "label": binding_value(
            row,
            "label",
        ),
        "nature": binding_value(
            row,
            "natureCode",
        ),
        "communeCode": binding_value(
            row,
            "communeCode",
        ),
        "communeName": binding_value(
            row,
            "communeName",
        ),
        "groundwaterPoint": groundwater_point,
        "investigationDepthM": depth,
    }

    # Keep GeoJSON properties compact.
    properties = {
        key: value
        for key, value in properties.items()
        if value is not None
    }

    return {
        "type": "Feature",
        "id": identifier,
        "geometry": geometry,
        "properties": properties,
    }


def load_features() -> None:
    global FEATURES
    global FEATURES_BY_ID

    print(
        f"Loading feature view from {FUSEKI_SPARQL_URL}"
    )

    result = run_sparql(
        FEATURE_QUERY
    )

    rows = (
        result
        .get("results", {})
        .get("bindings", [])
    )

    features = [
        row_to_feature(row)
        for row in rows
    ]

    FEATURES = features

    FEATURES_BY_ID = {
        feature["id"]: feature
        for feature in features
    }

    print(
        f"Loaded {len(FEATURES):,} BSS features"
    )


# ---------------------------------------------------------------------------
# Collection extent
# ---------------------------------------------------------------------------

def collection_bbox() -> list[float] | None:

    coords = [
        feature["geometry"]["coordinates"]
        for feature in FEATURES
        if feature["geometry"] is not None
    ]

    if not coords:
        return None

    longitudes = [
        coordinate[0]
        for coordinate in coords
    ]

    latitudes = [
        coordinate[1]
        for coordinate in coords
    ]

    return [
        min(longitudes),
        min(latitudes),
        max(longitudes),
        max(latitudes),
    ]


# ---------------------------------------------------------------------------
# bbox filtering
# ---------------------------------------------------------------------------

def parse_bbox(
    raw: str | None,
) -> tuple[float, float, float, float] | None:

    if raw is None:
        return None

    try:
        values = [
            float(value)
            for value in raw.split(",")
        ]
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="bbox must contain numeric values",
        )

    if len(values) not in (4, 6):
        raise HTTPException(
            status_code=400,
            detail=(
                "bbox must contain four "
                "or six numbers"
            ),
        )

    # This collection is 2D.
    if len(values) == 6:
        min_lon, min_lat, _, max_lon, max_lat, _ = values
    else:
        min_lon, min_lat, max_lon, max_lat = values

    if not (
        -180 <= min_lon <= 180
        and -180 <= max_lon <= 180
        and -90 <= min_lat <= 90
        and -90 <= max_lat <= 90
    ):
        raise HTTPException(
            status_code=400,
            detail="bbox is outside CRS84 bounds",
        )

    if min_lat > max_lat:
        raise HTTPException(
            status_code=400,
            detail="bbox latitude range is invalid",
        )

    return (
        min_lon,
        min_lat,
        max_lon,
        max_lat,
    )


def feature_matches_bbox(
    feature: dict[str, Any],
    bbox: tuple[
        float,
        float,
        float,
        float,
    ],
) -> bool:

    geometry = feature.get(
        "geometry"
    )

    # OGC API Features Core specifies that bbox
    # also matches features without a spatial geometry.
    if geometry is None:
        return True

    lon, lat = geometry[
        "coordinates"
    ]

    min_lon, min_lat, max_lon, max_lat = bbox

    # Handle a bbox crossing the anti-meridian.
    if min_lon <= max_lon:
        longitude_matches = (
            min_lon <= lon <= max_lon
        )
    else:
        longitude_matches = (
            lon >= min_lon
            or lon <= max_lon
        )

    latitude_matches = (
        min_lat <= lat <= max_lat
    )

    return (
        longitude_matches
        and latitude_matches
    )


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

def link(
    href: str,
    rel: str,
    media_type: str,
    title: str | None = None,
) -> dict[str, str]:

    result = {
        "href": href,
        "rel": rel,
        "type": media_type,
    }

    if title:
        result["title"] = title

    return result


def items_url(
    request: Request,
    *,
    offset: int | None = None,
) -> str:

    params = dict(
        request.query_params
    )

    if offset is not None:
        params["offset"] = str(
            offset
        )

    query = urlencode(params)

    base = (
        f"{PUBLIC_BASE_URL}"
        f"/collections/"
        f"{COLLECTION_ID}/items"
    )

    return (
        f"{base}?{query}"
        if query
        else base
    )


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_features()

    yield


app = FastAPI(
    title="BSS FAIR OGC API Features prototype",
    version="0.1.0",
    lifespan=lifespan,
)

PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[2]
)

CLIENT_DIR = (
    PROJECT_ROOT
    / "client"
)


app.mount(
    "/demo",
    StaticFiles(
        directory=CLIENT_DIR,
        html=True,
    ),
    name="demo",
)


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------

@app.get("/")
def landing_page():
    return {
        "title": (
            "BSS FAIR OGC API Features prototype"
        ),
        "description": (
            "Feature-level geospatial access to "
            "the semanticized BRGM BSS Loiret dataset."
        ),
        "links": [
            link(
                f"{PUBLIC_BASE_URL}/",
                "self",
                "application/json",
            ),
            link(
                f"{PUBLIC_BASE_URL}/api",
                "service-desc",
                "application/json",
            ),
            link(
                f"{PUBLIC_BASE_URL}/conformance",
                "conformance",
                "application/json",
            ),
            link(
                f"{PUBLIC_BASE_URL}/collections",
                "data",
                "application/json",
            ),
        ],
    }


# ---------------------------------------------------------------------------
# API definition
# ---------------------------------------------------------------------------

@app.get("/api")
def api_definition():
    return app.openapi()


# ---------------------------------------------------------------------------
# Conformance declaration
# ---------------------------------------------------------------------------

@app.get("/conformance")
def conformance():
    return {
        "conformsTo": [
            (
                "http://www.opengis.net/spec/"
                "ogcapi-features-1/1.0/conf/core"
            ),
            (
                "http://www.opengis.net/spec/"
                "ogcapi-features-1/1.0/conf/geojson"
            ),
        ]
    }


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

def collection_metadata() -> dict[str, Any]:

    extent = collection_bbox()

    collection: dict[str, Any] = {
        "id": COLLECTION_ID,
        "title": "BRGM BSS ouvrages - Loiret",
        "description": (
            "Semanticized BSS underground works "
            "and features in the Loiret department."
        ),
        "itemType": "feature",
        "links": [
            link(
                (
                    f"{PUBLIC_BASE_URL}/collections/"
                    f"{COLLECTION_ID}"
                ),
                "self",
                "application/json",
            ),
            link(
                (
                    f"{PUBLIC_BASE_URL}/collections/"
                    f"{COLLECTION_ID}/items"
                ),
                "items",
                "application/geo+json",
            ),
        ],
    }

    if extent:
        collection["extent"] = {
            "spatial": {
                "bbox": [
                    extent
                ]
            }
        }

    return collection


@app.get("/collections")
def collections():
    return {
        "links": [
            link(
                f"{PUBLIC_BASE_URL}/collections",
                "self",
                "application/json",
            ),
        ],
        "collections": [
            collection_metadata()
        ],
    }


@app.get(
    "/collections/{collection_id}"
)
def collection(
    collection_id: str,
):

    if collection_id != COLLECTION_ID:
        raise HTTPException(
            status_code=404,
            detail="Collection not found",
        )

    return collection_metadata()


# ---------------------------------------------------------------------------
# Feature collection
# ---------------------------------------------------------------------------

@app.get(
    "/collections/{collection_id}/items"
)
def items(
    collection_id: str,
    request: Request,

    limit: int = Query(
        default=10,
        ge=1,
    ),

    offset: int = Query(
        default=0,
        ge=0,
    ),

    bbox: str | None = Query(
        default=None,
    ),

    datetime_: str | None = Query(
        default=None,
        alias="datetime",
    ),
):

    if collection_id != COLLECTION_ID:
        raise HTTPException(
            status_code=404,
            detail="Collection not found",
        )

    # OGC Core permits a server-specific maximum.
    effective_limit = min(
        limit,
        MAX_LIMIT,
    )

    selected = FEATURES

    parsed_bbox = parse_bbox(
        bbox
    )

    if parsed_bbox is not None:
        selected = [
            feature
            for feature in selected
            if feature_matches_bbox(
                feature,
                parsed_bbox,
            )
        ]

    # We expose no temporal geometry for the ouvrage
    # collection. Therefore a valid datetime filter
    # does not exclude features in this v1 representation.
    _ = datetime_

    number_matched = len(
        selected
    )

    page = selected[
        offset:
        offset + effective_limit
    ]

    links = [
        link(
            items_url(
                request,
                offset=offset,
            ),
            "self",
            "application/geo+json",
        ),
        link(
            (
                f"{PUBLIC_BASE_URL}/collections/"
                f"{COLLECTION_ID}"
            ),
            "collection",
            "application/json",
        ),
    ]

    next_offset = (
        offset
        + effective_limit
    )

    if next_offset < number_matched:
        links.append(
            link(
                items_url(
                    request,
                    offset=next_offset,
                ),
                "next",
                "application/geo+json",
            )
        )

    if offset > 0:
        previous_offset = max(
            0,
            offset - effective_limit,
        )

        links.append(
            link(
                items_url(
                    request,
                    offset=previous_offset,
                ),
                "prev",
                "application/geo+json",
            )
        )

    result = {
        "type": "FeatureCollection",
        "timeStamp": datetime.now(
            timezone.utc
        ).isoformat(),
        "numberMatched": number_matched,
        "numberReturned": len(page),
        "features": page,
        "links": links,
    }

    return JSONResponse(
        result,
        media_type="application/geo+json",
    )


# ---------------------------------------------------------------------------
# Individual feature
# ---------------------------------------------------------------------------

@app.get(
    "/collections/{collection_id}/items/{feature_id}"
)
def item(
    collection_id: str,
    feature_id: str,
):

    if collection_id != COLLECTION_ID:
        raise HTTPException(
            status_code=404,
            detail="Collection not found",
        )

    feature = FEATURES_BY_ID.get(
        feature_id
    )

    if feature is None:
        raise HTTPException(
            status_code=404,
            detail="Feature not found",
        )

    result = {
        **feature,
        "links": [
            link(
                (
                    f"{PUBLIC_BASE_URL}/collections/"
                    f"{COLLECTION_ID}/items/"
                    f"{feature_id}"
                ),
                "self",
                "application/geo+json",
            ),
            link(
                (
                    f"{PUBLIC_BASE_URL}/collections/"
                    f"{COLLECTION_ID}"
                ),
                "collection",
                "application/json",
            ),
        ],
    }

    return JSONResponse(
        result,
        media_type="application/geo+json",
    )