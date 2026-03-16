"""LoadData node — loads a FITS file from MinIO and returns spectrum/image data."""

import io

import numpy as np
from astropy.io import fits
from astropy.table import Table

from app.storage import download_fits


def load_data(input_data: dict | None, params: dict) -> dict:
    """Load FITS from storage and extract tabular data.

    params:
        fits_path: str — MinIO path to the FITS file

    Returns dict with keys: columns, data, header, fits_path
    """
    fits_path = params.get("fits_path") or (input_data or {}).get("fits_path")
    if not fits_path:
        raise ValueError("LoadData requires 'fits_path' in params or input_data")

    raw = download_fits(fits_path)
    hdul = fits.open(io.BytesIO(raw))

    # Try to find a table extension first, fall back to primary HDU
    table_data = None
    header_dict = {}
    for hdu in hdul:
        header_dict.update({k: str(v) for k, v in hdu.header.items() if k})
        if isinstance(hdu, (fits.BinTableHDU, fits.TableHDU)):
            table = Table.read(hdu)
            table_data = {col: table[col].tolist() for col in table.colnames}
            break

    if table_data is None and hdul[0].data is not None:
        # Image or 1D spectrum in primary HDU
        data = hdul[0].data
        if data.ndim == 1:
            table_data = {
                "index": list(range(len(data))),
                "flux": data.tolist(),
            }
        else:
            # 2D image — flatten stats
            table_data = {
                "shape": list(data.shape),
                "min": float(np.nanmin(data)),
                "max": float(np.nanmax(data)),
                "mean": float(np.nanmean(data)),
            }

    hdul.close()

    return {
        "type": "spectrum",
        "fits_path": fits_path,
        "columns": list((table_data or {}).keys()),
        "data": table_data or {},
        "header": header_dict,
    }
