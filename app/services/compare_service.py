"""Vehicle comparison.

Builds a row-per-attribute matrix and marks which rows actually differ, so the
UI can highlight the differences instead of making the reader scan for them.
"""

from __future__ import annotations

from app.schemas.vehicle import VehicleRecord, label_for
from app.services.normalize import comparison_key

#: (canonical field, accessor path, "higher is better" | "lower is better" | None)
COMPARE_FIELDS: list[tuple[str, str, str | None]] = [
    ("year", "year", "higher"),
    ("make", "make", None),
    ("model", "model", None),
    ("trim", "trim", None),
    ("body_type", "body_type", None),
    ("engine_displacement_l", "engine.displacement_l", None),
    ("engine_type", "engine.type", None),
    ("engine_cylinders", "engine.cylinders", None),
    ("horsepower", "horsepower", "higher"),
    ("torque_lb_ft", "engine.torque_lb_ft", "higher"),
    ("fuel", "fuel", None),
    ("drivetrain", "drivetrain", None),
    ("transmission", "transmission", None),
    ("transmission_speeds", "transmission_speeds", None),
    ("mpg_city", "mpg_city", "higher"),
    ("mpg_highway", "mpg_highway", "higher"),
    ("mpg_combined", "mpg_combined", "higher"),
    ("plant_country", "plant_country", None),
]


def _read(record: VehicleRecord, path: str):
    node = record
    for part in path.split("."):
        node = getattr(node, part, None)
        if node is None:
            return None
    return node


def build_comparison(records: list[VehicleRecord]) -> dict:
    """Return ``{vehicles, rows}`` where each row flags whether it differs."""
    vehicles = [
        {
            "vin": r.vin,
            "title": " ".join(
                str(p) for p in (r.year, r.make, r.model) if p
            ) or r.vin,
            "trim": r.trim,
            "confidence": r.confidence.get("overall", "UNKNOWN"),
            "discrepancies": len(r.discrepancies),
        }
        for r in records
    ]

    rows = []
    for field_name, path, direction in COMPARE_FIELDS:
        values = [_read(r, path) for r in records]
        present = [v for v in values if v is not None]
        if not present:
            continue      # nothing to compare; omit the row entirely

        keys = {comparison_key(field_name, v) for v in present}
        differs = len(keys) > 1 or len(present) != len(values)

        best_index = None
        if direction and len(present) > 1:
            numeric = [
                (i, float(v)) for i, v in enumerate(values)
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            ]
            if len(numeric) > 1:
                picker = max if direction == "higher" else min
                best_index = picker(numeric, key=lambda pair: pair[1])[0]
                # Only call out a leader when it is genuinely alone at the top.
                best_value = dict(numeric)[best_index]
                if sum(1 for _, v in numeric if v == best_value) > 1:
                    best_index = None

        rows.append(
            {
                "field": field_name,
                "label": label_for(field_name),
                "values": values,
                "differs": differs,
                "best_index": best_index,
                "direction": direction,
                "sources": [
                    (r.fields[field_name].source if field_name in r.fields else None)
                    for r in records
                ],
                "confidences": [
                    (r.fields[field_name].confidence.value if field_name in r.fields else None)
                    for r in records
                ],
            }
        )

    return {
        "vehicles": vehicles,
        "rows": rows,
        "difference_count": sum(1 for r in rows if r["differs"]),
    }
