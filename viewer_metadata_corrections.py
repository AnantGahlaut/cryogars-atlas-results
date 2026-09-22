"""Reviewed, metadata-only viewer corrections; never opens or edits an archive.

SNEX-004: provider Table 2 and Section 2.3 identify this exact snow-off DTM
as LiDAR-derived. HRSI's snow-on DEMs use a different acquisition technique.
The approved 2026-09-12 wording/history is retained across fresh exports.
"""

SOURCE_FILENAME = "SNEX_HRSI_SD_DEM_CO_GM_DTM_1m_V01.0.tif"
DEM_PATH = "science/LIDAR/DEM/grids/elevation"
PROVIDER_GUIDE = "https://nsidc.org/sites/default/files/documents/user-guide/snex_hrsi_sd_dem_co-v001-userguide.pdf"
OLD_SITE = ("DEM is satellite photogrammetry (HRSI), a different technique from the lidar DEMs at the other seven sites. "
            "Carries two campaigns: SnowEx 2017 (ASO) and 2020. They cover the same ground, so they must never be split across train and test.")
NEW_SITE = ("DEM is the LiDAR-derived snow-off reference DTM from the HRSI collection. "
            "Carries two campaigns: SnowEx 2017 (ASO) and 2020. They cover the same ground, so they must never be split across train and test.")
OLD_SOURCE = "HRSI satellite photogrammetry DTM, not lidar. Resampled 1 m -> 3 m."
NEW_SOURCE = "LiDAR-derived snow-off reference DTM from the HRSI collection. Resampled 1 m -> 3 m."
HISTORY = ("SNEX-004, 2026-09-12: viewer source description corrected using the provider guide, Section 2.3. "
           "The previous note is retained. HDF5 metadata repair awaits verified archive backups. No raster values changed.")


def apply_viewer_metadata_corrections(payload):
    """Correct only known wording in place; return explicit applied changes.

    Validate every target first, so unreviewed metadata cannot be partially
    rewritten. Already-correct source text does not gain invented history.
    Packed arrays, historical build provenance and all other fields stay intact.
    """
    ident = payload.get("identification", {})
    if payload.get("site") != "grand_mesa" or ident.get("reference_filename") != SOURCE_FILENAME:
        return []
    targets = [("payload.identification", ident, "site_note", OLD_SITE, NEW_SITE)]
    for object_path, field, before, after in (
            ("identification", "site_note", OLD_SITE, NEW_SITE),
            (DEM_PATH, "source_note", OLD_SOURCE, NEW_SOURCE)):
        nodes = [node for node in payload.get("tree", []) if node.get("path") == object_path]
        if len(nodes) != 1 or not isinstance(nodes[0].get("attrs"), dict):
            raise ValueError("SNEX-004 requires exactly one metadata node: " + object_path)
        targets.append(("payload.tree[" + object_path + "].attrs", nodes[0]["attrs"], field, before, after))
    changes = []
    for location, attrs, field, before, after in targets:
        if attrs.get(field) not in (before, after):
            raise ValueError("SNEX-004 unreviewed wording: " + location + "." + field)
        previous_key, correction_key = field + "_previous", field + "_correction"
        if ((previous_key in attrs or correction_key in attrs)
                and (attrs.get(previous_key) != before or attrs.get(correction_key) != HISTORY)):
            raise ValueError("SNEX-004 conflicting correction history: " + location + "." + field)
        if attrs[field] == before:
            changes.append((attrs, field, {"issue": "SNEX-004", "object": location,
                "attribute": field, "before": before, "after": after, "history": HISTORY,
                "provider_guide": PROVIDER_GUIDE, "provider_section": "2.3"}))
    for attrs, field, change in changes:
        attrs.update({field: change["after"], field + "_previous": change["before"],
                      field + "_correction": change["history"]})
    return [change for _, _, change in changes]
