import numpy as np
import json

# ---------- Feature Generators ----------

def generate_cylinder(radius=10.0, height=20.0, num_points=500):
    theta = np.linspace(0, 2*np.pi, num_points)
    z = np.linspace(0, height, num_points)
    x = radius * np.cos(theta)
    y = radius * np.sin(theta)
    points = np.column_stack((x, y, z))
    return points

def generate_sphere(radius=10.0, num_points=500):
    phi = np.linspace(0, np.pi, int(np.sqrt(num_points)))
    theta = np.linspace(0, 2*np.pi, int(np.sqrt(num_points)))
    phi, theta = np.meshgrid(phi, theta)
    x = radius * np.sin(phi) * np.cos(theta)
    y = radius * np.sin(phi) * np.sin(theta)
    z = radius * np.cos(phi)
    points = np.column_stack((x.flatten(), y.flatten(), z.flatten()))
    return points

def generate_cone(radius=10.0, height=20.0, num_points=500):
    theta = np.linspace(0, 2*np.pi, num_points)
    z = np.linspace(0, height, num_points)
    r = (radius/height) * z  # radius grows linearly with z
    x = r * np.cos(theta)
    y = r * np.sin(theta)
    points = np.column_stack((x, y, z))
    return points

def generate_circle(radius=10.0, z=0.0, num_points=200):
    theta = np.linspace(0, 2*np.pi, num_points)
    x = radius * np.cos(theta)
    y = radius * np.sin(theta)
    z = np.full_like(x, z)
    points = np.column_stack((x, y, z))
    return points

def generate_taper(base_radius=10.0, top_radius=5.0, height=20.0, num_points=500):
    theta = np.linspace(0, 2*np.pi, num_points)
    z = np.linspace(0, height, num_points)
    r = base_radius + (top_radius - base_radius) * (z/height)
    x = r * np.cos(theta)
    y = r * np.sin(theta)
    points = np.column_stack((x, y, z))
    return points

# ---------- Noise + Probe Simulation ----------

def add_noise(points, sigma=0.05, offset=(0.1, 0.1, 0.1)):
    noisy_points = points + np.random.normal(0, sigma, points.shape)
    noisy_points += np.array(offset)
    return noisy_points

def simulate_probe_tip(points, probe_radius=1.0, normals=None):
    if normals is None:
        normals = np.tile(np.array([0,0,1]), (points.shape[0],1))
    tip_points = points + probe_radius * normals
    return tip_points, normals

def export_json(cmm_id, probe_diameter, tip_points, normals):
    payload = {
        "cmmId": cmm_id,
        "probeDiameter": probe_diameter,
        "points": [
            {"x": float(p[0]), "y": float(p[1]), "z": float(p[2]),
             "normal": [float(n[0]), float(n[1]), float(n[2])]}
            for p, n in zip(tip_points, normals)
        ]
    }
    return json.dumps(payload, indent=2)

def generate_features(shape="taper", **kwargs):
    if shape == "cylinder":
        return generate_cylinder(**kwargs)
    elif shape == "sphere":
        return generate_sphere(**kwargs)
    elif shape == "cone":
        return generate_cone(**kwargs)
    elif shape == "circle":
        return generate_circle(**kwargs)
    elif shape == "taper":
        return generate_taper(**kwargs)
    else:
        raise ValueError(f"Unknown shape: {shape}")
