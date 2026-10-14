import numpy as np


def compute_pixel_to_ccf_transform(pixel_coords, ccf_coords):
    """Compute the affine transformation matrix from 2D pixel coordinates to 3D CCF coordinates.

    Parameters
    ----------
    pixel_coords : array-like, shape (n, 2)
        Pixel coordinates (u, v) of landmark points in the source image.
        Must have at least 3 points (4+ recommended for least-squares fit).
    ccf_coords : array-like, shape (n, 3)
        Corresponding CCF coordinates (x, y, z) for each landmark point.

    Returns
    -------
    A : np.ndarray, shape (3, 3)
        Affine transformation matrix such that [x, y, z]^T = A @ [u, v, 1]^T
    """
    pixel_coords = np.asarray(pixel_coords, dtype=float)
    ccf_coords = np.asarray(ccf_coords, dtype=float)

    n = pixel_coords.shape[0]
    # Build the design matrix: [u, v, 1] for each point
    ones = np.ones((n, 1))
    P = np.hstack([pixel_coords, ones])  # shape (n, 3)

    # Solve P @ A^T = CCF  =>  A^T = lstsq(P, CCF)
    result, residuals, rank, sv = np.linalg.lstsq(P, ccf_coords, rcond=None)
    A = result.T  # shape (3, 3)

    return A


def pixel_to_ccf(pixel_coords_query, A):
    """Transform pixel coordinates to CCF coordinates using a precomputed affine matrix.

    Parameters
    ----------
    pixel_coords_query : array-like, shape (m, 2) or (2,)
        Pixel coordinates to transform.
    A : np.ndarray, shape (3, 3)
        Affine transformation matrix from compute_pixel_to_ccf_transform.

    Returns
    -------
    ccf_coords : np.ndarray, shape (m, 3) or (3,)
        CCF coordinates for each input pixel.
    """
    pixel_coords_query = np.asarray(pixel_coords_query, dtype=float)
    single = pixel_coords_query.ndim == 1
    if single:
        pixel_coords_query = pixel_coords_query.reshape(1, -1)

    ones = np.ones((pixel_coords_query.shape[0], 1))
    P = np.hstack([pixel_coords_query, ones])
    ccf = (A @ P.T).T

    return ccf[0] if single else ccf


def map_all_pixels(image_height, image_width, A):
    """Compute CCF coordinates for every pixel in an image.

    Parameters
    ----------
    image_height : int
    image_width : int
    A : np.ndarray, shape (3, 3)
        Affine transformation matrix.

    Returns
    -------
    ccf_map : np.ndarray, shape (image_height, image_width, 3)
        CCF (x, y, z) coordinates for each pixel.
    """
    v, u = np.mgrid[0:image_height, 0:image_width]
    ones = np.ones_like(u)
    # Stack into (3, H*W)
    P = np.stack([u.ravel(), v.ravel(), ones.ravel()], axis=0)
    ccf_flat = A @ P  # (3, H*W)
    ccf_map = ccf_flat.T.reshape(image_height, image_width, 3)

    return ccf_map


# Example usage with your data
if __name__ == "__main__":
    ccf_landmarks = np.array([
        [8850, 332, 3789],
        [8850, 332, 5739],
        [8850, 332, 7689],
        [2200, 332, 5739],
    ])

    pixel_landmarks = np.array([
        [122.828237, 374.740614],
        [130.722445, 277.078329],
        [103.608284, 182.378204],
        [451.217499, 235.361147],
    ])

    A = compute_pixel_to_ccf_transform(pixel_landmarks, ccf_landmarks)

    # Verify: transform the landmarks back and check residuals
    predicted = pixel_to_ccf(pixel_landmarks, A)
    errors = np.abs(predicted - ccf_landmarks)
    print("Max absolute error per axis:", errors.max(axis=0))
    print("Mean absolute error per axis:", errors.mean(axis=0))

    # Map every pixel in, say, a 512x512 image
    ccf_map = map_all_pixels(512, 512, A)
    print(f"CCF map shape: {ccf_map.shape}")
    print(f"Pixel (0,0) -> CCF {ccf_map[0, 0]}")
    print(f"Pixel (256,256) -> CCF {ccf_map[256, 256]}")