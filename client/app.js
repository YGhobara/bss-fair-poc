const COLLECTION_ID = "bss-ouvrages";

const MAX_FEATURES = 1000;


const map = L.map("map", {
    preferCanvas: true,
});


L.tileLayer(
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    {
        maxZoom: 19,

        attribution:
            '&copy; <a href="https://www.openstreetmap.org/copyright">' +
            "OpenStreetMap contributors</a>",
    },
).addTo(map);


const featureLayer = L.geoJSON(null, {

    pointToLayer(feature, latlng) {

        return L.circleMarker(latlng, {
            radius: 5,
            weight: 1,
            fillOpacity: 0.75,
        });
    },


    onEachFeature(feature, layer) {

        layer.bindPopup(
            buildPopup(feature),
        );
    },

}).addTo(map);


function buildPopup(feature) {

    const p = feature.properties ?? {};

    const rows = [];


    rows.push([
        "Identifier",
        p.identifier ?? feature.id ?? "—",
    ]);


    if (p.nature) {
        rows.push([
            "Nature",
            p.nature,
        ]);
    }


    if (p.communeName) {

        const commune = p.communeCode
            ? `${p.communeName} (${p.communeCode})`
            : p.communeName;

        rows.push([
            "Commune",
            commune,
        ]);
    }


    if (p.investigationDepthM !== undefined) {

        rows.push([
            "Depth",
            `${p.investigationDepthM} m`,
        ]);
    }


    if (p.groundwaterPoint !== undefined) {

        rows.push([
            "Groundwater point",
            p.groundwaterPoint
                ? "Yes"
                : "No",
        ]);
    }


    const content = rows
        .map(
            ([label, value]) => `
                <div class="popup-label">
                    ${escapeHtml(label)}
                </div>

                <div>
                    ${escapeHtml(String(value))}
                </div>
            `,
        )
        .join("");


    return `
        <div class="popup-title">
            ${escapeHtml(
                p.label
                ?? p.identifier
                ?? feature.id
                ?? "BSS feature"
            )}
        </div>

        <div class="popup-grid">
            ${content}
        </div>
    `;
}


function escapeHtml(value) {

    return value
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


function status(message) {

    document
        .getElementById("status")
        .textContent = message;
}


async function getJson(url) {

    const response = await fetch(url);

    if (!response.ok) {

        throw new Error(
            `${response.status} ${response.statusText}`,
        );
    }

    return response.json();
}


async function initialize() {

    status("Loading collection…");


    const collection = await getJson(
        `/collections/${COLLECTION_ID}`,
    );


    const bbox =
        collection
            ?.extent
            ?.spatial
            ?.bbox
            ?.[0];


    if (bbox?.length >= 4) {

        const [
            minLon,
            minLat,
            maxLon,
            maxLat,
        ] = bbox;


        map.fitBounds([
            [minLat, minLon],
            [maxLat, maxLon],
        ]);

    } else {

        // Fallback only.
        map.setView(
            [47.9, 2.2],
            8,
        );
    }


    await loadVisibleFeatures();
}


let requestNumber = 0;


async function loadVisibleFeatures() {

    const currentRequest =
        ++requestNumber;


    const bounds =
        map.getBounds();


    const bbox = [
        bounds.getWest(),
        bounds.getSouth(),
        bounds.getEast(),
        bounds.getNorth(),
    ].join(",");


    status("Loading features…");


    try {

        const params =
            new URLSearchParams({
                bbox,
                limit: String(MAX_FEATURES),
            });


        const data = await getJson(
            `/collections/${COLLECTION_ID}/items?${params}`,
        );


        // Ignore an old request if the user moved the
        // map again before it completed.
        if (currentRequest !== requestNumber) {
            return;
        }


        featureLayer.clearLayers();


        const mappableFeatures =
            data.features.filter(
                feature =>
                    feature.geometry !== null,
            );


        featureLayer.addData(
            mappableFeatures,
        );


        const matched =
            data.numberMatched ?? "?";

        const returned =
            data.numberReturned
            ?? data.features.length;


        status(
            `${mappableFeatures.length} mapped · ` +
            `${returned} returned · ` +
            `${matched} matched`,
        );

    } catch (error) {

        console.error(error);

        status(
            `API error: ${error.message}`,
        );
    }
}


/*
 * Simple debounce:
 * don't query the API continuously while the user
 * is actively panning/zooming.
 */
let moveTimer;


map.on("moveend", () => {

    clearTimeout(moveTimer);

    moveTimer = setTimeout(
        loadVisibleFeatures,
        200,
    );
});


initialize().catch(error => {

    console.error(error);

    status(
        `Initialization error: ${error.message}`,
    );
});