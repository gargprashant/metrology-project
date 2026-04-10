import numpy as np
from scipy.spatial import distance
from scipy.optimize import least_squares
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gdt-evaluation")

def fit_cylinder(points):
    # Simplified cylinder fit: axis along z, optimize radius
    xy = points[:, :2]
    def residuals(params):
        r = params[0]
        return np.sqrt((xy**2).sum(axis=1)) - r
    res = least_squares(residuals, x0=[1.0])
    return res.x[0]

def cylindricity(points, tolerance):
    radius = fit_cylinder(points)
    xy = points[:, :2]
    deviations = np.sqrt((xy**2).sum(axis=1)) - radius
    variation = np.ptp(deviations)
    return {
        "deviation": float(variation),
        "tolerance": tolerance,
        "status": "PASS" if variation <= tolerance else "FAIL"
    }

def flatness(points: np.ndarray, tolerance: float):
    """
    Fit a plane to the points and compute flatness deviation.
    Flatness = peak-to-peak deviation from best-fit plane.
    """
    A = np.c_[points[:,0], points[:,1], np.ones(points.shape[0])]
    coeffs, _, _, _ = np.linalg.lstsq(A, points[:,2], rcond=None)
    fitted = A @ coeffs
    deviation = np.ptp(points[:,2] - fitted)
    return {
        "deviation": float(deviation),
        "tolerance": tolerance,
        "status": "PASS" if deviation <= tolerance else "FAIL"
    }

def position(points: np.ndarray, nominal_points: np.ndarray, tolerance: float):
    """
    Compute position deviation as distance between centroids.
    """
    centroid = points.mean(axis=0)
    nominal_centroid = nominal_points.mean(axis=0)
    deviation = distance.euclidean(centroid, nominal_centroid)
    return {
        "deviation": float(deviation),
        "tolerance": tolerance,
        "status": "PASS" if deviation <= tolerance else "FAIL"
    }

def evaluate_features(points: np.ndarray, nominal_features: dict, tolerances: dict):
    """
    Evaluate GD&T features based on provided tolerances.
    """
    logger.info("Points shape: %s", points.shape)
    logger.info("Nominal features keys: %s", nominal_features.keys())
    logger.info("Tolerances: %s", tolerances)

    results = {}

    if "plane" in nominal_features and "flatness" in tolerances:
        results["flatness"] = flatness(points, tolerances["flatness"])

    if "circle" in nominal_features and "position" in tolerances:
        nominal_circle = np.array(nominal_features["circle"])
        results["position"] = position(points, nominal_circle, tolerances["position"])
    
    if "cylinder" in nominal_features and "cylindricity" in tolerances:
        results["cylindricity"] = cylindricity(points, tolerances["cylindricity"])

    return results