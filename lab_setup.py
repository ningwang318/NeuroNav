"""Appearance-only lab scene inferred from a single reference photograph.

Robot posture, base placement, tripod and breadboard are approximate static
illustrations. They must never be used for robot motion or collision checking.
"""
import numpy as np
from camera_bar import cylinder, case, rotation
from table_layout import TableLayout

WHITE = (.87,.89,.91,1)
SILVER = (.50,.54,.58,1)
BLACK = (.15,.17,.19,1)
GREEN = (.45,.72,.12,1)
GROUND_Y = -.09  # breadboard underside and tripod feet share this level


def axial(direction):
    z = np.asarray(direction,dtype=float)
    z /= np.linalg.norm(z)
    seed = np.array([0.,1.,0.]) if abs(z[1]) < .9 else np.array([1.,0.,0.])
    x = np.cross(seed,z)
    x /= np.linalg.norm(x)
    return np.column_stack([x,np.cross(z,x),z])


def sphere(radius, rows=14, columns=24):
    vertices = []
    for i in range(rows+1):
        theta = np.pi*i/rows
        for j in range(columns):
            phi = 2*np.pi*j/columns
            vertices.append([radius*np.sin(theta)*np.cos(phi),
                             radius*np.cos(theta), radius*np.sin(theta)*np.sin(phi)])
    faces = []
    for i in range(rows):
        for j in range(columns):
            k = (j+1)%columns
            a,b,c,d = i*columns+j,i*columns+k,(i+1)*columns+j,(i+1)*columns+k
            if i:
                faces.append([a,c,b])
            if i < rows-1:
                faces.append([b,c,d])
    return np.asarray(vertices),np.asarray(faces)


def curved_link(start,end,radius,bow):
    """Smooth swept shell with rounded profile and a gently bowed centreline."""
    start,end = np.asarray(start),np.asarray(end)
    rings, sides = 18, 28
    t = np.linspace(0,1,rings)
    path = start+(end-start)*t[:,None]+np.sin(np.pi*t)[:,None]*np.asarray(bow)
    vertices=[]
    for i in range(rings):
        tangent = path[min(i+1,rings-1)]-path[max(i-1,0)]
        basis = axial(tangent)
        r = radius*(.82+.18*np.sin(np.pi*t[i]))
        for angle in np.linspace(0,2*np.pi,sides,endpoint=False):
            vertices.append(path[i]+r*(np.cos(angle)*basis[:,0]+.88*np.sin(angle)*basis[:,1]))
    vertices.extend([start,end])
    faces=[]
    for i in range(rings-1):
        for j in range(sides):
            k=(j+1)%sides
            a,b,c,d=i*sides+j,i*sides+k,(i+1)*sides+j,(i+1)*sides+k
            faces.extend([[a,b,c],[b,d,c]])
    for j in range(sides):
        k=(j+1)%sides
        faces.extend([[rings*sides,j,k],[rings*sides+1,(rings-1)*sides+k,(rings-1)*sides+j]])
    return np.asarray(vertices),np.asarray(faces)


def lab_setup_meshes(cameras, reference, table=None):
    if reference is None:
        return [], [], []
    table=table or TableLayout()
    origin,layout=table.frame(reference)
    right,normal,back=layout[:,0],layout[:,1],layout[:,2]
    camera_center=np.mean([c[1] for c in cameras],axis=0) if cameras else origin+reference.basis[:,0]
    batches={}
    def add(geometry, point, orientation=np.eye(3), color=WHITE, smooth=False):
        vertices,faces=geometry
        vertices=vertices @ orientation.T+np.asarray(point)
        key=color,smooth
        batch=batches.setdefault(key,[[],[],0])
        batch[0].append(vertices)
        batch[1].append(faces+batch[2])
        batch[2]+=len(vertices)
    def position(local):
        return origin+layout @ np.asarray(local)
    def box(size, local, color, bevel=.004):
        # Primitive case local XY front; map its Y to scene depth, Z to up.
        orientation=np.column_stack([right,-back,normal])
        add(case(size[0],size[2],size[1],bevel),position(local),orientation,color)
    def rod(a,b,r,color,segments=24):
        a,b=np.asarray(a),np.asarray(b)
        add(cylinder(r,float(np.linalg.norm(b-a)),segments),(a+b)/2,axial(b-a),color,True)
    def local_rod(a,b,r,color):
        rod(position(a),position(b),r,color)

    # Photo estimate: 450 x 600 mm, 25 mm hole pitch, 50 mm edge margins.
    # The top is below the calibration-sphere centres, not at their plane.
    width,depth,thickness=np.array([table.width_mm,table.depth_mm,table.thickness_mm])/1000
    box((width,thickness-.016,depth),[0,-thickness/2,0],BLACK)
    box((width,.008,depth),[0,-.004,0],(.63,.65,.67,1))
    box((width,.008,depth),[0,-thickness+.004,0],(.40,.44,.48,1))
    for x in np.arange(-width/2+.05,width/2-.05+.0001,.025):
        for z in np.arange(-depth/2+.05,depth/2-.05+.0001,.025):
            add(cylinder(.0016,.00035,8),position([x,.0002,z]),axial(normal),(.12,.14,.16,1))
    # The breadboard is a low slab directly on the ground, with no table legs.

    content_vertices=np.vstack([np.vstack(batch[0]) for batch in batches.values()])
    meshes=[dict(vertices=np.vstack(batch[0]),faces=np.vstack(batch[1]),color=color,smooth=smooth)
            for (color,smooth),batch in batches.items()]
    bounds=[content_vertices.min(axis=0),content_vertices.max(axis=0)]
    labels=[]
    return meshes,labels,bounds
