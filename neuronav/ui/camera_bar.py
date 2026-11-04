"""Procedural Trio-style display housing anchored to calibrated optical centres.

Case and lens details are illustrative. They are not collision geometry.
Coordinates are metres in tracker space; no tracking measurements are modified.
"""
import numpy as np


def rotation(q):
    x, y, z, w = np.asarray(q) / np.linalg.norm(q)
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def cylinder(radius, depth, segments=48):
    angles = np.linspace(0, 2*np.pi, segments, endpoint=False)
    ring = np.column_stack([radius*np.cos(angles), radius*np.sin(angles)])
    vertices = np.vstack([np.column_stack([ring, np.full(segments, -depth/2)]),
                          np.column_stack([ring, np.full(segments, depth/2)]),
                          [0,0,-depth/2], [0,0,depth/2]])
    faces = []
    for i in range(segments):
        j = (i+1) % segments
        faces += [[i,j,j+segments], [i,j+segments,i+segments],
                  [2*segments,j,i], [2*segments+1,i+segments,j+segments]]
    return vertices, np.asarray(faces)


def case(width, height, depth, bevel=.003):
    x, y = width/2, height/2
    outline = np.array([[-x+bevel,-y], [x-bevel,-y], [x,-y+bevel],
                        [x,y-bevel], [x-bevel,y], [-x+bevel,y],
                        [-x,y-bevel], [-x,-y+bevel]])
    vertices = np.vstack([np.column_stack([outline,np.full(8,-depth/2)]),
                          np.column_stack([outline,np.full(8,depth/2)]),
                          [0,0,-depth/2], [0,0,depth/2]])
    faces = []
    for i in range(8):
        j = (i+1) % 8
        faces += [[i,j,j+8], [i,j+8,i+8], [16,j,i], [17,i+8,j+8]]
    return vertices, np.asarray(faces)


def camera_bar_meshes(cameras):
    """Return batched material meshes and one label; requires calibrated poses."""
    if not cameras:
        return [], None
    positions = np.asarray([c[1] for c in cameras], dtype=float)
    rotations = [rotation(c[2]) for c in cameras]
    center = positions.mean(axis=0)
    if len(cameras) > 1:
        _, _, vh = np.linalg.svd(positions-center)
        x = vh[0]
    else:
        x = rotations[0][:,0]
    # OptiTrack camera viewing direction is represented here along local -Z.
    forward = -np.mean([r[:,2] for r in rotations], axis=0)
    forward -= x*np.dot(forward,x)
    forward /= np.linalg.norm(forward)
    up = np.cross(forward,x)
    if up[1] < 0:
        x, up = -x, -up
    basis = np.column_stack([x,up,forward])
    projection = (positions-center) @ x
    residual = positions-center-projection[:,None]*x
    joined = len(cameras) == 3 and np.max(np.linalg.norm(residual,axis=1)) < .01
    batches = {}

    def add(geometry, origin, orientation, color, smooth=False):
        vertices, faces = geometry
        vertices = vertices @ orientation.T + origin
        key = color, smooth
        batch = batches.setdefault(key, [[], [], 0])
        batch[0].append(vertices)
        batch[1].append(faces+batch[2])
        batch[2] += len(vertices)

    if joined:
        length = float(np.ptp(projection)+.084)
        # Approximate 40.6 mm high, 50.8 mm deep Trio housing; length follows
        # the measured camera baseline so lens centres remain authoritative.
        add(case(length,.0406,.0508), center-forward*.0254, basis, (.20,.22,.24,1))
        add(case(length-.006,.035,.002), center+forward*.001, basis, (.055,.065,.08,1))
        for sign in [-1,1]:
            add(case(.007,.038,.049,bevel=.002),
                center+x*sign*(length/2-.0035)-forward*.024, basis, (.14,.16,.19,1))
        # Short mounting boss underneath the centre of the housing.
        mount_basis = np.column_stack([x,forward,-up])
        add(cylinder(.012,.014,32), center-forward*.027-up*.024,
            mount_basis, (.24,.27,.31,1), True)

    for position, r in zip(positions, rotations):
        normal = -r[:,2]
        u = x-normal*np.dot(normal,x)
        u /= np.linalg.norm(u)
        v = np.cross(normal,u)
        optical_basis = np.column_stack([u,v,normal])
        if not joined:
            add(case(.046,.0406,.0508), position-normal*.0254,
                optical_basis, (.43,.47,.52,1))
        # Bezel, lens barrel, retaining rim, dark coated optical glass.
        add(cylinder(.0196,.0025), position+normal*.0015,
            optical_basis, (.12,.14,.17,1), True)
        add(cylinder(.012,.007), position+normal*.004,
            optical_basis, (.23,.26,.30,1), True)
        add(cylinder(.0105,.0014), position+normal*.008,
            optical_basis, (.055,.065,.08,1), True)
        add(cylinder(.0088,.0005), position+normal*.0089,
            optical_basis, (.055,.14,.22,1), True)
        # Metallic/IR diode ring. Near-infrared illumination is not visible red
        # light; the muted colour is an illustrative material appearance.
        for angle in np.linspace(0,2*np.pi,26,endpoint=False):
            led_position = position+.016*(np.cos(angle)*u+np.sin(angle)*v)+normal*.0033
            add(cylinder(.00085,.0007,10), led_position,
                optical_basis, (.50,.30,.31,1), True)
        # Small blue lens reflection, placed on the optical front face.
        add(cylinder(.0022,.0002,24), position+normal*.0093-u*.0026+v*.0026,
            optical_basis, (.19,.42,.57,1), True)
    meshes = [dict(vertices=np.vstack(parts[0]), faces=np.vstack(parts[1]),
                   color=color, smooth=smooth)
              for (color,smooth),parts in batches.items()]
    return meshes, (center+up*.06, 'OptiTrack Trio 3' if joined else 'OptiTrack cameras')
