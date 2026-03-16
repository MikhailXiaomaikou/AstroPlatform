"""CoordTransform node — converts coordinates between reference frames."""

from astropy.coordinates import SkyCoord
import astropy.units as u


SUPPORTED_FRAMES = ["icrs", "galactic", "ecliptic", "fk5", "fk4"]


def coord_transform(input_data: dict, params: dict) -> dict:
    """Transform coordinates from one frame to another.

    params:
        from_frame: str — source frame (default "icrs")
        to_frame: str — target frame (default "galactic")
        ra_key: str — key for RA array (default "ra")
        dec_key: str — key for Dec array (default "dec")
    """
    from_frame = params.get("from_frame", "icrs")
    to_frame = params.get("to_frame", "galactic")
    ra_key = params.get("ra_key", "ra")
    dec_key = params.get("dec_key", "dec")

    for f in (from_frame, to_frame):
        if f not in SUPPORTED_FRAMES:
            raise ValueError(f"Unsupported frame '{f}'. Choose from: {SUPPORTED_FRAMES}")

    data = input_data.get("data", {})
    ra_vals = data.get(ra_key, [])
    dec_vals = data.get(dec_key, [])

    if not ra_vals or not dec_vals:
        raise ValueError("CoordTransform: no coordinate data found")

    coords = SkyCoord(ra=ra_vals, dec=dec_vals, unit=(u.degree, u.degree), frame=from_frame)
    transformed = coords.transform_to(to_frame)

    output_data = dict(data)

    if to_frame == "galactic":
        output_data["l"] = transformed.l.deg.tolist()
        output_data["b"] = transformed.b.deg.tolist()
    elif to_frame == "ecliptic":
        output_data["lon"] = transformed.lon.deg.tolist()
        output_data["lat"] = transformed.lat.deg.tolist()
    else:
        output_data["ra_transformed"] = transformed.ra.deg.tolist()
        output_data["dec_transformed"] = transformed.dec.deg.tolist()

    return {
        **input_data,
        "data": output_data,
        "coord_transform": {
            "from_frame": from_frame,
            "to_frame": to_frame,
            "count": len(ra_vals),
        },
    }
